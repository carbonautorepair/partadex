#!/usr/bin/env python3
"""
report_coverage_gaps.py
========================

Read-only coverage-gap report for the Partadex canonical database
(``data/partadex.db``).

For every (make, model) pair, and separately for every
(make, model, engine) triple, this script computes the observed year
range and any *internal* missing years -- years strictly between the
observed minimum and maximum year for that grouping that have no row
in the ``vehicles`` table.

Two grouping levels are reported because a single model frequently
spans multiple engine generations (e.g. an Acura MDX built on a 3.7L
V6 through 2013 and a 3.5L V6 from 2014 onward). A "gap" at the model
level means the vehicle itself has no catalog coverage for that model
year, regardless of engine. A "gap" at the model+engine level means a
*specific* engine variant has a hole in its own observed range, which
can help pinpoint a missing catalog page or extraction defect for
that particular power-plant.

This script NEVER opens the database for writing. It connects using
SQLite's ``mode=ro`` URI flag, which raises an error rather than
silently creating or mutating the file.

Usage
-----
    python3 scripts/report_coverage_gaps.py [--db PATH] [--make MAKE]
        [--model MODEL] [--format {text,json}] [--output PATH]
        [--only-gaps]

Examples
--------
    # Human readable report for the whole database
    python3 scripts/report_coverage_gaps.py

    # JSON report for a single make, written to a file
    python3 scripts/report_coverage_gaps.py --make TOYOTA \\
        --format json --output toyota_gaps.json

    # Only show groupings that actually have missing internal years
    python3 scripts/report_coverage_gaps.py --only-gaps
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_DB_PATH = "data/partadex.db"


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open ``db_path`` strictly read-only.

    Uses the SQLite URI ``mode=ro`` flag so that a missing file or an
    attempted write raises ``sqlite3.OperationalError`` instead of
    creating/mutating the database file.
    """
    resolved = Path(db_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Database not found: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON;")
    return conn


def fetch_vehicle_rows(
    conn: sqlite3.Connection,
    make: Optional[str] = None,
    model: Optional[str] = None,
) -> list[tuple[str, int, str, str]]:
    """Return distinct (make, year, model, engine) tuples.

    Rows with a NULL year or NULL/blank model are excluded -- a
    missing year makes range/gap math meaningless, and a missing
    model can't be grouped meaningfully.
    """
    sql = (
        "SELECT DISTINCT make, year, model, COALESCE(engine, '') "
        "FROM vehicles "
        "WHERE year IS NOT NULL AND model IS NOT NULL AND TRIM(model) != ''"
    )
    params: list[str] = []
    if make:
        sql += " AND make = ?"
        params.append(make)
    if model:
        sql += " AND model = ?"
        params.append(model)
    cur = conn.execute(sql, params)
    return cur.fetchall()


def _year_range_and_gaps(years: Iterable[int]) -> dict:
    years_sorted = sorted(set(years))
    year_min = years_sorted[0]
    year_max = years_sorted[-1]
    present = set(years_sorted)
    missing = [y for y in range(year_min, year_max + 1) if y not in present]
    return {
        "year_min": year_min,
        "year_max": year_max,
        "years_present": years_sorted,
        "missing_years": missing,
    }


def build_report(rows: list[tuple[str, int, str, str]]) -> dict:
    """Turn raw (make, year, model, engine) rows into the nested report."""
    # make -> model -> set(years)
    model_years: dict[tuple[str, str], set[int]] = defaultdict(set)
    # make -> model -> engine -> set(years)
    engine_years: dict[tuple[str, str, str], set[int]] = defaultdict(set)

    for make, year, model, engine in rows:
        model_years[(make, model)].add(year)
        engine_years[(make, model, engine)].add(year)

    makes: dict[str, dict] = {}
    for (make, model), years in sorted(model_years.items()):
        make_entry = makes.setdefault(make, {"make": make, "models": []})
        model_report = {"model": model, **_year_range_and_gaps(years), "engines": []}

        engines_for_model = sorted(
            (eng, yrs)
            for (m_make, m_model, eng), yrs in engine_years.items()
            if m_make == make and m_model == model
        )
        for engine, eng_years in engines_for_model:
            engine_report = {
                "engine": engine or None,
                **_year_range_and_gaps(eng_years),
            }
            model_report["engines"].append(engine_report)

        make_entry["models"].append(model_report)

    ordered_makes = [makes[m] for m in sorted(makes)]

    total_model_groups = sum(len(m["models"]) for m in ordered_makes)
    total_model_gaps = sum(
        1
        for m in ordered_makes
        for model in m["models"]
        if model["missing_years"]
    )
    total_engine_groups = sum(
        len(model["engines"]) for m in ordered_makes for model in m["models"]
    )
    total_engine_gaps = sum(
        1
        for m in ordered_makes
        for model in m["models"]
        for engine in model["engines"]
        if engine["missing_years"]
    )

    return {
        "makes": ordered_makes,
        "summary": {
            "total_makes": len(ordered_makes),
            "total_model_groups": total_model_groups,
            "total_model_groups_with_gaps": total_model_gaps,
            "total_engine_groups": total_engine_groups,
            "total_engine_groups_with_gaps": total_engine_gaps,
        },
    }


def filter_only_gaps(report: dict) -> dict:
    """Return a copy of ``report`` keeping only groupings with gaps."""
    filtered_makes = []
    for make_entry in report["makes"]:
        filtered_models = []
        for model in make_entry["models"]:
            filtered_engines = [e for e in model["engines"] if e["missing_years"]]
            if model["missing_years"] or filtered_engines:
                new_model = dict(model)
                new_model["engines"] = filtered_engines
                filtered_models.append(new_model)
        if filtered_models:
            new_make = dict(make_entry)
            new_make["models"] = filtered_models
            filtered_makes.append(new_make)
    return {"makes": filtered_makes, "summary": report["summary"]}


def to_json(report: dict, db_path: str) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(Path(db_path).resolve()),
        **report,
    }
    return json.dumps(payload, indent=2)


def to_text(report: dict, db_path: str) -> str:
    lines = []
    lines.append("Partadex Coverage Gap Report")
    lines.append(f"Source DB: {Path(db_path).resolve()}")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    summary = report["summary"]
    lines.append(
        f"Makes: {summary['total_makes']} | "
        f"Model groups: {summary['total_model_groups']} "
        f"({summary['total_model_groups_with_gaps']} with gaps) | "
        f"Engine groups: {summary['total_engine_groups']} "
        f"({summary['total_engine_groups_with_gaps']} with gaps)"
    )
    lines.append("")

    for make_entry in report["makes"]:
        lines.append(f"== {make_entry['make']} ==")
        for model in make_entry["models"]:
            gap_note = (
                f"MISSING: {model['missing_years']}"
                if model["missing_years"]
                else "no internal gaps"
            )
            lines.append(
                f"  {model['model']}: {model['year_min']}-{model['year_max']} "
                f"({gap_note})"
            )
            for engine in model["engines"]:
                eng_gap_note = (
                    f"MISSING: {engine['missing_years']}"
                    if engine["missing_years"]
                    else "no internal gaps"
                )
                engine_label = engine["engine"] or "(engine unspecified)"
                lines.append(
                    f"    - {engine_label}: "
                    f"{engine['year_min']}-{engine['year_max']} ({eng_gap_note})"
                )
        lines.append("")

    return "\n".join(lines)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to the canonical Partadex database (default: {DEFAULT_DB_PATH}). "
        "Always opened read-only; never modified.",
    )
    parser.add_argument("--make", default=None, help="Restrict to a single make (exact match).")
    parser.add_argument("--model", default=None, help="Restrict to a single model (exact match).")
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write report to this file instead of stdout.",
    )
    parser.add_argument(
        "--only-gaps",
        action="store_true",
        help="Only include model/engine groupings that have missing internal years.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    try:
        conn = open_readonly(args.db)
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        rows = fetch_vehicle_rows(conn, make=args.make, model=args.model)
    finally:
        conn.close()

    if not rows:
        print("error: no matching vehicle rows found", file=sys.stderr)
        return 1

    report = build_report(rows)
    if args.only_gaps:
        report = filter_only_gaps(report)

    rendered = to_json(report, args.db) if args.format == "json" else to_text(report, args.db)

    if args.output:
        Path(args.output).write_text(rendered + ("\n" if not rendered.endswith("\n") else ""))
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
