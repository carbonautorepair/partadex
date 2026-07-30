import csv
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import backfill_import as bi  # noqa: E402

CANONICAL_DB = REPO_ROOT / "data" / "partadex.db"

CSV_HEADER = [
    "year",
    "make",
    "model",
    "engine",
    "filter_category",
    "brand",
    "part_number",
    "source_document",
    "source_page",
    "catalog_year",
    "verification_status",
]

VALID_ROW = {
    "year": "2015",
    "make": "TOYOTA",
    "model": "CAMRY",
    "engine": "L4 2.5L",
    "filter_category": "oil",
    "brand": "WIX",
    "part_number": "51515",
    "source_document": "microgard_2024.pdf",
    "source_page": "12",
    "catalog_year": "2024",
    "verification_status": "verified",
}


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class BackfillTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmp_dir, "backfill.csv")
        self.db_path = os.path.join(self.tmp_dir, "supplemental.db")


class TestValidationAndImport(BackfillTestCase):
    def test_valid_row_imports_with_expected_schema(self):
        write_csv(self.csv_path, [VALID_ROW])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertEqual(rc, 0)

        conn = sqlite3.connect(self.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(backfill_entries)").fetchall()]
        for expected_col in [
            "year",
            "make",
            "model",
            "engine",
            "filter_category",
            "brand",
            "part_number",
            "source_document",
            "source_page",
            "catalog_year",
            "verification_status",
            "created_at",
            "updated_at",
        ]:
            self.assertIn(expected_col, cols)

        rows = conn.execute("SELECT year, make, model, part_number FROM backfill_entries").fetchall()
        conn.close()
        self.assertEqual(rows, [(2015, "TOYOTA", "CAMRY", "51515")])

    def test_blank_verification_status_defaults_to_unverified(self):
        row = dict(VALID_ROW)
        row["verification_status"] = ""
        write_csv(self.csv_path, [row])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db_path)
        status = conn.execute("SELECT verification_status FROM backfill_entries").fetchone()[0]
        conn.close()
        self.assertEqual(status, "unverified")

    def test_duplicate_row_within_same_csv_is_deduped(self):
        write_csv(self.csv_path, [VALID_ROW, dict(VALID_ROW)])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM backfill_entries").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_reimporting_same_csv_is_idempotent(self):
        write_csv(self.csv_path, [VALID_ROW])
        bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])

        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM backfill_entries").fetchone()[0]
        created_ats = [r[0] for r in conn.execute("SELECT created_at FROM backfill_entries").fetchall()]
        conn.close()
        self.assertEqual(count, 1)
        self.assertEqual(len(set(created_ats)), 1)

    def test_different_engine_is_a_distinct_row_same_year_make_model(self):
        row_a = dict(VALID_ROW)
        row_b = dict(VALID_ROW)
        row_b["engine"] = "L4 2.0L"
        row_b["part_number"] = "51616"
        write_csv(self.csv_path, [row_a, row_b])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM backfill_entries").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)


class TestValidationRejection(BackfillTestCase):
    def test_invalid_category_rejects_entire_file_by_default(self):
        row = dict(VALID_ROW)
        row["filter_category"] = "engine_oil"
        write_csv(self.csv_path, [row])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(self.db_path), "no db should be created on validation failure")

    def test_bad_year_rejected(self):
        row = dict(VALID_ROW)
        row["year"] = "not-a-year"
        write_csv(self.csv_path, [row])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(self.db_path))

    def test_year_out_of_plausible_range_rejected(self):
        row = dict(VALID_ROW)
        row["year"] = "1899"
        write_csv(self.csv_path, [row])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertNotEqual(rc, 0)

    def test_missing_required_field_rejected(self):
        row = dict(VALID_ROW)
        row["part_number"] = ""
        write_csv(self.csv_path, [row])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertNotEqual(rc, 0)

    def test_missing_csv_column_rejected(self):
        with open(self.csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=[c for c in CSV_HEADER if c != "verification_status"])
            writer.writeheader()
            row = dict(VALID_ROW)
            del row["verification_status"]
            writer.writerow(row)
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(self.db_path))

    def test_allow_partial_imports_valid_rows_and_skips_invalid(self):
        good = dict(VALID_ROW)
        bad = dict(VALID_ROW)
        bad["filter_category"] = "engine_oil"
        bad["part_number"] = "99999"
        write_csv(self.csv_path, [good, bad])
        rc = bi.main(
            ["--csv", self.csv_path, "--supplemental-db", self.db_path, "--allow-partial"]
        )
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM backfill_entries").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_dry_run_makes_no_writes(self):
        write_csv(self.csv_path, [VALID_ROW])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path, "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.db_path))


class TestCanonicalDatabaseGuard(BackfillTestCase):
    def setUp(self):
        super().setUp()
        if not CANONICAL_DB.exists():
            self.skipTest("canonical database not present in this checkout")
        self.before_hash = hash_file(CANONICAL_DB)

    def tearDown(self):
        after_hash = hash_file(CANONICAL_DB)
        self.assertEqual(
            self.before_hash, after_hash, "data/partadex.db must never be modified by the backfill importer"
        )

    def test_refuses_exact_canonical_path(self):
        write_csv(self.csv_path, [VALID_ROW])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", str(CANONICAL_DB)])
        self.assertNotEqual(rc, 0)

    def test_refuses_relative_canonical_path(self):
        write_csv(self.csv_path, [VALID_ROW])
        cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            rc = bi.main(["--csv", self.csv_path, "--supplemental-db", "data/partadex.db"])
        finally:
            os.chdir(cwd)
        self.assertNotEqual(rc, 0)

    def test_refuses_any_path_named_partadex_db(self):
        write_csv(self.csv_path, [VALID_ROW])
        alias_path = os.path.join(self.tmp_dir, "partadex.db")
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", alias_path])
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(alias_path))

    def test_normal_supplemental_path_is_allowed(self):
        write_csv(self.csv_path, [VALID_ROW])
        rc = bi.main(["--csv", self.csv_path, "--supplemental-db", self.db_path])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
