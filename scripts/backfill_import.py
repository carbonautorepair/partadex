#!/usr/bin/env python3
"""
backfill_import.py
===================

Validate a source-tracked CSV of exact backfill rows and import them
into a *supplemental* SQLite database. This script never opens, and
refuses to target, the canonical ``data/partadex.db`` database.

Each backfill row is a single, exact application claim: this
year/make/model/engine used this brand's part number for this filter
category, according to a specific, citable source document/page. This
is meant to plug the coverage gaps identified by
``scripts/report_coverage_gaps.py`` with hand-verified data -- not to
re-run bulk extraction.

CSV schema (header row required, columns may appear in any order)
--------------------------------------------------------------------
    year                  4-digit model year, e.g. 2015
    make                  vehicle make, e.g. TOYOTA
    model                 vehicle model, e.g. CAMRY
    engine                engine description; may be blank
    filter_category       one of: oil, air, cabin
    brand                 filter brand, e.g. WIX
    part_number           exact part number
    source_document       citable source, e.g. "microgard_2024.pdf"
    source_page           1-based page number in the source; may be blank
    catalog_year          model year of the *catalog/source itself*; may be blank
    verification_status   one of: verified, unverified, pending (blank -> unverified)

Uniqueness / idempotency
-------------------------
A row is uniquely identified by:
    (year, make, model, engine, filter_category, brand, part_number)

Re-importing the same CSV is a no-op for rows that already exist
(``INSERT OR IGNORE`` against a UNIQUE constraint on that tuple) --
running the import twice does not create duplicate rows.

Validation is strict and all-or-nothing by default: if *any* row in
the CSV fails validation, no database writes happen at all and the
script exits non-zero with a list of every problem found. Pass
``--allow-partial`` to import only the valid rows and report the rest
as skipped.

Usage
-----
    python3 scripts/backfill_import.py --csv PATH --supplemental-db PATH
        [--allow-partial] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CANONICAL_DB_NAMES = {"partadex.db"}
CANONICAL_DB_PATH = Path("data/partadex.db")

REQUIRED_COLUMNS = [
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

VALID_CATEGORIES = {"oil", "air", "cabin"}
VALID_STATUSES = {"verified", "unverified", "pending"}
DEFAULT_STATUS = "unverified"

MIN_YEAR = 1980
MAX_YEAR = 2035

PART_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/. ]{1,29}$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS backfill_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT '',
    filter_category TEXT NOT NULL,
    brand TEXT NOT NULL,
    part_number TEXT NOT NULL,
    source_document TEXT NOT NULL,
    source_page INTEGER,
    catalog_year INTEGER,
    verification_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (year, make, model, engine, filter_category, brand, part_number)
);
CREATE INDEX IF NOT EXISTS idx_backfill_make_model
    ON backfill_entries(make, model);
CREATE INDEX IF NOT EXISTS idx_backfill_year
    ON backfill_entries(year);
"""


class BackfillValidationError(Exception):
    """Raised when the whole CSV must be rejected (structural error)."""


@dataclass
class RowError:
    row_number: int  # 1-based, counting header as row 1
    message: str


@dataclass
class ValidatedRow:
    year: int
    make: str
    model: str
    engine: str
    filter_category: str
    brand: str
    part_number: str
    source_document: str
    source_page: Optional[int]
    catalog_year: Optional[int]
    verification_status: str


@dataclass
class ValidationResult:
    valid_rows: list[ValidatedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _parse_optional_int(value: str, field_name: str, row_number: int, errors: list[RowError]) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        errors.append(RowError(row_number, f"{field_name} must be an integer, got {value!r}"))
        return None


def validate_csv_rows(rows: list[dict], header: list[str]) -> ValidationResult:
    missing_columns = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing_columns:
        raise BackfillValidationError(
            f"CSV is missing required column(s): {', '.join(missing_columns)}"
        )

    result = ValidationResult()

    for idx, raw in enumerate(rows):
        row_number = idx + 2  # +1 for 0-index, +1 for header row
        row_errors: list[RowError] = []

        year_str = _clean(raw.get("year"))
        make = _clean(raw.get("make")).upper()
        model = _clean(raw.get("model"))
        engine = _clean(raw.get("engine"))
        filter_category = _clean(raw.get("filter_category")).lower()
        brand = _clean(raw.get("brand"))
        part_number = _clean(raw.get("part_number"))
        source_document = _clean(raw.get("source_document"))
        source_page_str = _clean(raw.get("source_page"))
        catalog_year_str = _clean(raw.get("catalog_year"))
        verification_status = _clean(raw.get("verification_status")).lower()

        year: Optional[int] = None
        if not year_str:
            row_errors.append(RowError(row_number, "year is required"))
        else:
            try:
                year = int(year_str)
                if not (MIN_YEAR <= year <= MAX_YEAR):
                    row_errors.append(
                        RowError(row_number, f"year {year} out of plausible range {MIN_YEAR}-{MAX_YEAR}")
                    )
            except ValueError:
                row_errors.append(RowError(row_number, f"year must be an integer, got {year_str!r}"))

        if not make:
            row_errors.append(RowError(row_number, "make is required"))
        if not model:
            row_errors.append(RowError(row_number, "model is required"))

        if filter_category not in VALID_CATEGORIES:
            row_errors.append(
                RowError(
                    row_number,
                    f"filter_category must be one of {sorted(VALID_CATEGORIES)}, got {filter_category!r}",
                )
            )

        if not brand:
            row_errors.append(RowError(row_number, "brand is required"))

        if not part_number:
            row_errors.append(RowError(row_number, "part_number is required"))
        elif not PART_NUMBER_RE.match(part_number):
            row_errors.append(
                RowError(row_number, f"part_number {part_number!r} contains invalid characters or length")
            )

        if not source_document:
            row_errors.append(RowError(row_number, "source_document is required"))

        source_page = _parse_optional_int(source_page_str, "source_page", row_number, row_errors)
        if source_page is not None and source_page < 1:
            row_errors.append(RowError(row_number, f"source_page must be >= 1, got {source_page}"))

        catalog_year = _parse_optional_int(catalog_year_str, "catalog_year", row_number, row_errors)
        if catalog_year is not None and not (MIN_YEAR <= catalog_year <= MAX_YEAR):
            row_errors.append(
                RowError(row_number, f"catalog_year {catalog_year} out of plausible range {MIN_YEAR}-{MAX_YEAR}")
            )

        if not verification_status:
            verification_status = DEFAULT_STATUS
        elif verification_status not in VALID_STATUSES:
            row_errors.append(
                RowError(
                    row_number,
                    f"verification_status must be one of {sorted(VALID_STATUSES)}, got {verification_status!r}",
                )
            )

        if row_errors:
            result.errors.extend(row_errors)
            continue

        result.valid_rows.append(
            ValidatedRow(
                year=year,  # type: ignore[arg-type]
                make=make,
                model=model,
                engine=engine,
                filter_category=filter_category,
                brand=brand,
                part_number=part_number,
                source_document=source_document,
                source_page=source_page,
                catalog_year=catalog_year,
                verification_status=verification_status,
            )
        )

    return result


def load_and_validate_csv(csv_path: str) -> ValidationResult:
    path = Path(csv_path)
    if not path.exists():
        raise BackfillValidationError(f"CSV not found: {path}")

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    return validate_csv_rows(rows, header)


def guard_not_canonical(supplemental_db_path: str) -> None:
    """Refuse to target the canonical database under any name/path alias."""
    target = Path(supplemental_db_path).resolve()
    canonical = CANONICAL_DB_PATH.resolve()
    if target == canonical:
        raise BackfillValidationError(
            f"Refusing to write to canonical database path: {target}"
        )
    if target.name in CANONICAL_DB_NAMES:
        raise BackfillValidationError(
            f"Refusing to write to a database named like the canonical database: {target}"
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


@dataclass
class ImportStats:
    inserted: int = 0
    already_present: int = 0
    total_valid_rows: int = 0
    skipped_invalid: int = 0


def import_rows(conn: sqlite3.Connection, rows: list[ValidatedRow]) -> ImportStats:
    stats = ImportStats(total_valid_rows=len(rows))
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO backfill_entries (
                year, make, model, engine, filter_category, brand, part_number,
                source_document, source_page, catalog_year, verification_status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.year,
                row.make,
                row.model,
                row.engine,
                row.filter_category,
                row.brand,
                row.part_number,
                row.source_document,
                row.source_page,
                row.catalog_year,
                row.verification_status,
                now,
                now,
            ),
        )
        if cur.rowcount == 1:
            stats.inserted += 1
        else:
            stats.already_present += 1

    conn.commit()
    return stats


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, help="Path to the backfill CSV to validate and import.")
    parser.add_argument(
        "--supplemental-db",
        required=True,
        help="Path to the supplemental SQLite database to write to. "
        "Must NOT be data/partadex.db.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Import only the rows that pass validation; report the rest as skipped "
        "instead of rejecting the whole file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate (and report what would be imported) without writing anything.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    try:
        guard_not_canonical(args.supplemental_db)
    except BackfillValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = load_and_validate_csv(args.csv)
    except BackfillValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.errors and not args.allow_partial:
        print(f"error: {len(result.errors)} row(s) failed validation; no database writes performed.", file=sys.stderr)
        for err in result.errors:
            print(f"  row {err.row_number}: {err.message}", file=sys.stderr)
        print("Pass --allow-partial to import valid rows and skip invalid ones.", file=sys.stderr)
        return 1

    if result.errors:
        print(f"warning: {len(result.errors)} row(s) failed validation and will be skipped.", file=sys.stderr)
        for err in result.errors:
            print(f"  row {err.row_number}: {err.message}", file=sys.stderr)

    if not result.valid_rows:
        print("error: no valid rows to import.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"dry-run: {len(result.valid_rows)} valid row(s) would be considered for import "
            f"into {args.supplemental_db}; {len(result.errors)} row(s) skipped."
        )
        return 0

    conn = sqlite3.connect(args.supplemental_db)
    try:
        ensure_schema(conn)
        stats = import_rows(conn, result.valid_rows)
    finally:
        conn.close()

    print(
        f"Imported {stats.inserted} new row(s), "
        f"{stats.already_present} already present (skipped as duplicates), "
        f"{len(result.errors)} row(s) failed validation "
        f"into {args.supplemental_db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
