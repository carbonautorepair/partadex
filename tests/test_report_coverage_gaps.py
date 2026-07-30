import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import report_coverage_gaps as rcg  # noqa: E402

CANONICAL_DB = REPO_ROOT / "data" / "partadex.db"


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_temp_vehicles_db(rows):
    """rows: list of (make, year, model, engine) tuples."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "fixture.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            make TEXT NOT NULL,
            year INTEGER,
            model TEXT,
            engine TEXT,
            source_page INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO vehicles (make, year, model, engine) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


class TestGapDetection(unittest.TestCase):
    def test_simple_internal_gap_is_detected(self):
        rows = [
            ("TOYOTA", 2010, "CAMRY", "L4 2.5L"),
            ("TOYOTA", 2011, "CAMRY", "L4 2.5L"),
            # 2012 missing
            ("TOYOTA", 2013, "CAMRY", "L4 2.5L"),
        ]
        report = rcg.build_report(rows)
        model = report["makes"][0]["models"][0]
        self.assertEqual(model["model"], "CAMRY")
        self.assertEqual(model["year_min"], 2010)
        self.assertEqual(model["year_max"], 2013)
        self.assertEqual(model["missing_years"], [2012])

    def test_no_gap_when_years_contiguous(self):
        rows = [
            ("HONDA", 2018, "CIVIC", "L4 2.0L"),
            ("HONDA", 2019, "CIVIC", "L4 2.0L"),
            ("HONDA", 2020, "CIVIC", "L4 2.0L"),
        ]
        report = rcg.build_report(rows)
        model = report["makes"][0]["models"][0]
        self.assertEqual(model["missing_years"], [])

    def test_gap_never_reported_outside_observed_range(self):
        # Only years 2015 and 2017 exist; 2014 and 2018 are NOT gaps
        # because they fall outside [min, max].
        rows = [
            ("FORD", 2015, "FOCUS", "L4 2.0L"),
            ("FORD", 2017, "FOCUS", "L4 2.0L"),
        ]
        report = rcg.build_report(rows)
        model = report["makes"][0]["models"][0]
        self.assertEqual(model["year_min"], 2015)
        self.assertEqual(model["year_max"], 2017)
        self.assertEqual(model["missing_years"], [2016])

    def test_model_and_engine_level_gaps_can_disagree(self):
        # Model-level: years 2010, 2011, 2012 are all present (via two
        # different engines), so the MODEL has no internal gap.
        # Engine-level: "ENGINE A" skips 2011, which IS an internal
        # gap for that specific engine even though the model as a
        # whole has full coverage that year (via ENGINE B).
        rows = [
            ("MAZDA", 2010, "3", "ENGINE A"),
            ("MAZDA", 2012, "3", "ENGINE A"),
            ("MAZDA", 2011, "3", "ENGINE B"),
        ]
        report = rcg.build_report(rows)
        model = report["makes"][0]["models"][0]
        self.assertEqual(model["missing_years"], [], "model-level should show no gap")

        engines_by_name = {e["engine"]: e for e in model["engines"]}
        self.assertEqual(engines_by_name["ENGINE A"]["missing_years"], [2011])
        self.assertEqual(engines_by_name["ENGINE B"]["missing_years"], [])

    def test_blank_engine_reported_as_none(self):
        rows = [("KIA", 2020, "SOUL", "")]
        report = rcg.build_report(rows)
        engine = report["makes"][0]["models"][0]["engines"][0]
        self.assertIsNone(engine["engine"])

    def test_summary_counts(self):
        rows = [
            ("TOYOTA", 2010, "CAMRY", "L4 2.5L"),
            ("TOYOTA", 2012, "CAMRY", "L4 2.5L"),  # gap at 2011
            ("HONDA", 2018, "CIVIC", "L4 2.0L"),
            ("HONDA", 2019, "CIVIC", "L4 2.0L"),  # no gap
        ]
        report = rcg.build_report(rows)
        summary = report["summary"]
        self.assertEqual(summary["total_makes"], 2)
        self.assertEqual(summary["total_model_groups"], 2)
        self.assertEqual(summary["total_model_groups_with_gaps"], 1)

    def test_filter_only_gaps_drops_clean_groups(self):
        rows = [
            ("TOYOTA", 2010, "CAMRY", "L4 2.5L"),
            ("TOYOTA", 2012, "CAMRY", "L4 2.5L"),  # gap
            ("HONDA", 2018, "CIVIC", "L4 2.0L"),
            ("HONDA", 2019, "CIVIC", "L4 2.0L"),  # clean
        ]
        report = rcg.build_report(rows)
        filtered = rcg.filter_only_gaps(report)
        makes_present = {m["make"] for m in filtered["makes"]}
        self.assertEqual(makes_present, {"TOYOTA"})


class TestReadOnlyAccess(unittest.TestCase):
    def test_open_readonly_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            rcg.open_readonly("/nonexistent/path/does-not-exist.db")

    def test_open_readonly_rejects_writes(self):
        db_path = make_temp_vehicles_db([("TOYOTA", 2020, "CAMRY", "L4")])
        conn = rcg.open_readonly(db_path)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO vehicles (make, year, model, engine) VALUES ('X', 2000, 'Y', 'Z')")
        conn.close()

    def test_fetch_and_build_end_to_end_on_fixture_db(self):
        db_path = make_temp_vehicles_db(
            [
                ("TOYOTA", 2010, "CAMRY", "L4 2.5L"),
                ("TOYOTA", 2011, "CAMRY", "L4 2.5L"),
                ("TOYOTA", 2013, "CAMRY", "L4 2.5L"),
            ]
        )
        conn = rcg.open_readonly(db_path)
        rows = rcg.fetch_vehicle_rows(conn)
        conn.close()
        report = rcg.build_report(rows)
        model = report["makes"][0]["models"][0]
        self.assertEqual(model["missing_years"], [2012])


class TestCanonicalDatabaseUntouched(unittest.TestCase):
    def setUp(self):
        if not CANONICAL_DB.exists():
            self.skipTest("canonical database not present in this checkout")
        self.before_hash = hash_file(CANONICAL_DB)

    def tearDown(self):
        after_hash = hash_file(CANONICAL_DB)
        self.assertEqual(
            self.before_hash, after_hash, "data/partadex.db must never be modified by the gap report"
        )

    def test_cli_text_report_does_not_mutate_canonical_db(self):
        tmp_dir = tempfile.mkdtemp()
        out_path = os.path.join(tmp_dir, "report.txt")
        rc = rcg.main(["--db", str(CANONICAL_DB), "--format", "text", "--output", out_path])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.getsize(out_path) > 0)

    def test_cli_json_report_is_well_formed_and_readonly(self):
        tmp_dir = tempfile.mkdtemp()
        out_path = os.path.join(tmp_dir, "report.json")
        rc = rcg.main(["--db", str(CANONICAL_DB), "--format", "json", "--output", out_path, "--make", "ACURA"])
        self.assertEqual(rc, 0)
        with open(out_path) as fh:
            payload = json.load(fh)
        self.assertIn("summary", payload)
        self.assertIn("makes", payload)
        self.assertTrue(all(m["make"] == "ACURA" for m in payload["makes"]))


if __name__ == "__main__":
    unittest.main()
