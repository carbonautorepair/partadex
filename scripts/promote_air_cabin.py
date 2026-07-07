#!/usr/bin/env python3
"""
Promote air and cabin filter data from staging DB to aircabin.db.

Reads application_rows and application_parts from filter_catalog_staging.db,
builds filter sets (deduping parts), and creates aircabin.db with vehicles,
air_filter_sets, cabin_filter_sets, and footnotes tables.
"""

import sqlite3
import os
from collections import defaultdict
import fitz  # PyMuPDF

# Paths
STAGING_DB = '/home/user/partadex/data/filter_catalog_staging.db'
OUTPUT_DB = '/home/user/partadex/data/aircabin.db'
PDF_PATH = '/home/user/partadex/data/microgard.pdf'


def extract_footnotes_from_pdf():
    """
    Extract footnotes from PDF pages 2-4 (0-indexed).
    Each footnote has: <number>, <English text>, <Spanish>, <French>.
    Returns dict: {fn_number: english_text}
    """
    doc = fitz.open(PDF_PATH)
    footnotes = {}

    # Pages 2-4 (0-indexed)
    for page_idx in [2, 3, 4]:
        page = doc[page_idx]
        text = page.get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        i = 0
        while i < len(lines):
            line = lines[i]

            # Skip headers and section markers
            if line in ['Footnotes', 'FN #', 'English', 'Spanish', 'French']:
                i += 1
                continue

            # Try to parse a footnote number
            try:
                fn_num = int(line)
                # Next line should be English text
                if i + 1 < len(lines):
                    english_text = lines[i + 1]
                    footnotes[fn_num] = english_text
                    i += 2
                else:
                    i += 1
                continue
            except ValueError:
                pass

            i += 1

    doc.close()
    return footnotes


def get_latest_run_for_each_make(cursor):
    """
    Returns dict: {make: run_id} for the newest run of each make.
    """
    cursor.execute("""
        SELECT make, run_id
        FROM application_rows
        ORDER BY make, run_id DESC
    """)

    make_to_run = {}
    for make, run_id in cursor.fetchall():
        if make not in make_to_run:
            make_to_run[make] = run_id

    return make_to_run


def load_application_data(cursor, make_to_run):
    """
    Load application_rows and application_parts, filtering by latest run per make.
    Returns:
      - rows: dict {row_id: {make, year, model, engine, eng_code}}
      - parts: dict {row_id: [(filter_category, brand, raw_part_number), ...]}
    """
    rows = {}
    parts = defaultdict(list)

    # Build the WHERE clause for latest runs per make
    valid_run_ids = set(make_to_run.values())

    # Load application_rows for valid runs
    cursor.execute("""
        SELECT id, run_id, make, year, model, engine, engine_code
        FROM application_rows
        WHERE run_id IN ({})
        ORDER BY id
    """.format(','.join('?' * len(valid_run_ids))), list(valid_run_ids))

    for row_id, run_id, make, year, model, engine, engine_code in cursor.fetchall():
        # Only include rows where run_id is the latest for this make
        if make_to_run[make] == run_id:
            rows[row_id] = {
                'make': make,
                'year': year,
                'model': model,
                'engine': engine or '',
                'engine_code': engine_code or '',
            }

    # Load application_parts for valid rows
    row_ids = set(rows.keys())
    if row_ids:
        cursor.execute("""
            SELECT application_row_id, filter_category, brand, raw_part_number, source_column
            FROM application_parts
            WHERE application_row_id IN ({})
            ORDER BY application_row_id, id
        """.format(','.join('?' * len(row_ids))), list(row_ids))

        for app_row_id, filter_cat, brand, raw_part, source_col in cursor.fetchall():
            # Handle UNKNOWN_CABIN_COLUMN -> K&N mapping
            if source_col == 'UNKNOWN_CABIN_COLUMN' or source_col == 'right_cabin_air_column':
                brand = 'K&N'

            parts[app_row_id].append((filter_cat, brand, raw_part))

    return rows, parts


def build_sets(parts_for_row):
    """
    Build air and cabin filter sets from parts.
    Dedupes parts per brand, joins with ', '.

    Args:
      parts_for_row: [(filter_category, brand, raw_part_number), ...]

    Returns:
      - air_set: {brand: joined_parts} (brands: microgard, wix, kn)
      - cabin_set: {brand: joined_parts} (brands: microgard, microgard_hepa, wix, kn)
    """
    air_set = {}
    cabin_set = {}

    for filter_cat, brand, raw_part in parts_for_row:
        if filter_cat == 'engine_air':
            # Air filter mapping: MICROGARD, WIX, K&N
            if brand == 'MICROGARD':
                key = 'microgard'
            elif brand == 'WIX':
                key = 'wix'
            elif brand == 'K&N':
                key = 'kn'
            else:
                continue

            if key not in air_set:
                air_set[key] = []
            air_set[key].append(raw_part)

        elif filter_cat == 'cabin_air':
            # Cabin filter mapping: MICROGARD (or MICROGARD HEPA), WIX, K&N
            if brand == 'MICROGARD HEPA':
                key = 'microgard_hepa'
            elif brand == 'MICROGARD':
                key = 'microgard'
            elif brand == 'WIX':
                key = 'wix'
            elif brand == 'K&N':
                key = 'kn'
            else:
                continue

            if key not in cabin_set:
                cabin_set[key] = []
            cabin_set[key].append(raw_part)

    # Dedupe per brand (preserving first-seen order) and join with ', '
    air_result = {}
    for key, parts_list in air_set.items():
        seen = set()
        deduped = []
        for part in parts_list:
            if part not in seen:
                deduped.append(part)
                seen.add(part)
        air_result[key] = ', '.join(deduped)

    cabin_result = {}
    for key, parts_list in cabin_set.items():
        seen = set()
        deduped = []
        for part in parts_list:
            if part not in seen:
                deduped.append(part)
                seen.add(part)
        cabin_result[key] = ', '.join(deduped)

    return air_result, cabin_result


def find_or_create_set(cursor, table_name, columns, values_dict):
    """
    Find existing row in set table or create new one.

    Args:
      cursor: DB cursor
      table_name: 'air_filter_sets' or 'cabin_filter_sets'
      columns: ['microgard', 'wix', 'kn'] or ['microgard', 'microgard_hepa', 'wix', 'kn']
      values_dict: {column_name: value or None}

    Returns:
      set_id (int)
    """
    # Build WHERE clause with NULL handling
    where_parts = []
    where_values = []
    for col in columns:
        val = values_dict.get(col)
        if val is None:
            where_parts.append(f"{col} IS NULL")
        else:
            where_parts.append(f"{col} = ?")
            where_values.append(val)

    where_clause = " AND ".join(where_parts)

    # Try to find existing
    cursor.execute(f"SELECT id FROM {table_name} WHERE {where_clause}", where_values)
    result = cursor.fetchone()

    if result:
        return result[0]

    # Insert new
    col_names = [col for col in columns if col in values_dict and values_dict[col] is not None]
    if not col_names:
        # All None - still create a row with all NULLs
        col_names = columns
        insert_values = [None] * len(columns)
    else:
        insert_values = [values_dict.get(col) for col in col_names]

    placeholders = ','.join('?' * len(col_names))
    col_list = ','.join(col_names)
    cursor.execute(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})", insert_values)

    return cursor.lastrowid


def main():
    # Remove existing output DB
    if os.path.exists(OUTPUT_DB):
        os.remove(OUTPUT_DB)

    # Connect to output DB
    out_conn = sqlite3.connect(OUTPUT_DB)
    out_cursor = out_conn.cursor()

    # Create tables
    out_cursor.execute("""
        CREATE TABLE air_filter_sets (
            id INTEGER PRIMARY KEY,
            microgard TEXT,
            wix TEXT,
            kn TEXT
        )
    """)

    out_cursor.execute("""
        CREATE TABLE cabin_filter_sets (
            id INTEGER PRIMARY KEY,
            microgard TEXT,
            microgard_hepa TEXT,
            wix TEXT,
            kn TEXT
        )
    """)

    out_cursor.execute("""
        CREATE TABLE vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            make TEXT NOT NULL,
            year INTEGER NOT NULL,
            model TEXT NOT NULL,
            engine TEXT NOT NULL DEFAULT '',
            eng_code TEXT DEFAULT '',
            vin TEXT DEFAULT '',
            air_set_id INTEGER REFERENCES air_filter_sets(id),
            cabin_set_id INTEGER REFERENCES cabin_filter_sets(id)
        )
    """)

    out_cursor.execute("""
        CREATE TABLE footnotes (
            fn INTEGER PRIMARY KEY,
            text_en TEXT NOT NULL
        )
    """)

    out_cursor.execute("""
        CREATE INDEX idx_vehicles_lookup ON vehicles(make, model, year)
    """)

    # Load staging data
    staging_conn = sqlite3.connect(STAGING_DB)
    staging_cursor = staging_conn.cursor()

    make_to_run = get_latest_run_for_each_make(staging_cursor)
    rows, parts = load_application_data(staging_cursor, make_to_run)

    staging_conn.close()

    # Extract footnotes from PDF
    footnotes = extract_footnotes_from_pdf()
    for fn_num, text_en in footnotes.items():
        out_cursor.execute("INSERT INTO footnotes (fn, text_en) VALUES (?, ?)", (fn_num, text_en))

    # Process each application row
    for row_id, row_data in rows.items():
        parts_list = parts[row_id]

        # Build filter sets
        air_set, cabin_set = build_sets(parts_list)

        # Find or create air set
        air_set_id = None
        if air_set:  # Only create if there are parts
            air_values = {
                'microgard': air_set.get('microgard'),
                'wix': air_set.get('wix'),
                'kn': air_set.get('kn'),
            }
            air_set_id = find_or_create_set(out_cursor, 'air_filter_sets',
                                             ['microgard', 'wix', 'kn'], air_values)

        # Find or create cabin set
        cabin_set_id = None
        if cabin_set:  # Only create if there are parts
            cabin_values = {
                'microgard': cabin_set.get('microgard'),
                'microgard_hepa': cabin_set.get('microgard_hepa'),
                'wix': cabin_set.get('wix'),
                'kn': cabin_set.get('kn'),
            }
            cabin_set_id = find_or_create_set(out_cursor, 'cabin_filter_sets',
                                              ['microgard', 'microgard_hepa', 'wix', 'kn'], cabin_values)

        # Insert vehicle
        out_cursor.execute("""
            INSERT INTO vehicles (make, year, model, engine, eng_code, vin, air_set_id, cabin_set_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row_data['make'],
            row_data['year'],
            row_data['model'],
            row_data['engine'],
            row_data['engine_code'],
            '',  # vin is always empty for staging data
            air_set_id,
            cabin_set_id,
        ))

    out_conn.commit()

    # Verification stats
    out_cursor.execute("SELECT COUNT(*) FROM vehicles")
    vehicle_count = out_cursor.fetchone()[0]

    out_cursor.execute("SELECT COUNT(*) FROM air_filter_sets")
    air_set_count = out_cursor.fetchone()[0]

    out_cursor.execute("SELECT COUNT(*) FROM cabin_filter_sets")
    cabin_set_count = out_cursor.fetchone()[0]

    out_cursor.execute("SELECT COUNT(*) FROM footnotes")
    footnote_count = out_cursor.fetchone()[0]

    out_cursor.execute("SELECT COUNT(*) FROM vehicles WHERE air_set_id IS NOT NULL AND cabin_set_id IS NOT NULL")
    both_sets_count = out_cursor.fetchone()[0]

    print("=== Verification Stats ===")
    print(f"Vehicle count: {vehicle_count}")
    print(f"Air filter set count: {air_set_count}")
    print(f"Cabin filter set count: {cabin_set_count}")
    print(f"Footnote count: {footnote_count}")
    print(f"Vehicles with both air_set_id and cabin_set_id: {both_sets_count}")

    # 3 sample joined rows for TOYOTA 2023
    print("\n=== Sample TOYOTA 2023 Vehicles ===")
    out_cursor.execute("""
        SELECT v.id, v.make, v.year, v.model, v.engine, v.eng_code,
               COALESCE(a.microgard, '') as air_microgard,
               COALESCE(a.wix, '') as air_wix,
               COALESCE(a.kn, '') as air_kn,
               COALESCE(c.microgard, '') as cabin_microgard,
               COALESCE(c.microgard_hepa, '') as cabin_microgard_hepa,
               COALESCE(c.wix, '') as cabin_wix,
               COALESCE(c.kn, '') as cabin_kn
        FROM vehicles v
        LEFT JOIN air_filter_sets a ON v.air_set_id = a.id
        LEFT JOIN cabin_filter_sets c ON v.cabin_set_id = c.id
        WHERE v.make = 'TOYOTA' AND v.year = 2023
        LIMIT 3
    """)

    for row in out_cursor.fetchall():
        (vid, make, year, model, engine, eng_code,
         air_mg, air_wix, air_kn,
         cabin_mg, cabin_mg_hp, cabin_wix, cabin_kn) = row
        print(f"  {make} {year} {model} (Engine: {engine}, Code: {eng_code})")
        print(f"    Air: MICROGARD={air_mg}, WIX={air_wix}, K&N={air_kn}")
        print(f"    Cabin: MICROGARD={cabin_mg}, HEPA={cabin_mg_hp}, WIX={cabin_wix}, K&N={cabin_kn}")

    # Sanity check: every vehicle's air_set_id and cabin_set_id must exist
    print("\n=== Sanity Check ===")
    out_cursor.execute("""
        SELECT COUNT(*) FROM vehicles v
        WHERE (v.air_set_id IS NOT NULL AND v.air_set_id NOT IN (SELECT id FROM air_filter_sets))
           OR (v.cabin_set_id IS NOT NULL AND v.cabin_set_id NOT IN (SELECT id FROM cabin_filter_sets))
    """)
    invalid_refs = out_cursor.fetchone()[0]
    print(f"Vehicles with invalid set references: {invalid_refs}")

    # TOYOTA 2023 COROLLA Hybrid check
    print("\n=== TOYOTA 2023 COROLLA Hybrid Check ===")
    out_cursor.execute("""
        SELECT v.id, v.make, v.year, v.model, v.engine, v.eng_code,
               COALESCE(a.microgard, '') as air_microgard,
               COALESCE(a.wix, '') as air_wix,
               COALESCE(a.kn, '') as air_kn,
               COALESCE(c.microgard, '') as cabin_microgard,
               COALESCE(c.microgard_hepa, '') as cabin_microgard_hepa,
               COALESCE(c.wix, '') as cabin_wix,
               COALESCE(c.kn, '') as cabin_kn
        FROM vehicles v
        LEFT JOIN air_filter_sets a ON v.air_set_id = a.id
        LEFT JOIN cabin_filter_sets c ON v.cabin_set_id = c.id
        WHERE v.make = 'TOYOTA' AND v.year = 2023 AND v.model = 'COROLLA Hybrid'
    """)

    rows = out_cursor.fetchall()
    if rows:
        for row in rows:
            (vid, make, year, model, engine, eng_code,
             air_mg, air_wix, air_kn,
             cabin_mg, cabin_mg_hp, cabin_wix, cabin_kn) = row
            print(f"{make} {year} {model}")
            print(f"  Engine: {engine}")
            print(f"  Air Set:")
            print(f"    MICROGARD: {air_mg} (expected: MGA10000)")
            print(f"    WIX: {air_wix} (expected: WA10000)")
            print(f"    K&N: {air_kn} (expected: 33-2485)")
            print(f"  Cabin Set:")
            print(f"    MICROGARD: {cabin_mg} (expected: 4113)")
            print(f"    MICROGARD_HEPA: {cabin_mg_hp} (expected: 4113HP)")
            print(f"    WIX: {cabin_wix} (expected: WP10320, WP10322)")
            print(f"    K&N: {cabin_kn} (expected: VF2054)")

            # Check if matches expected
            expected = {
                'air_mg': 'MGA10000',
                'air_wix': 'WA10000',
                'air_kn': '33-2485',
                'cabin_mg': '4113',
                'cabin_mg_hp': '4113HP',
                'cabin_wix': 'WP10320, WP10322',
                'cabin_kn': 'VF2054',
            }

            actual = {
                'air_mg': air_mg,
                'air_wix': air_wix,
                'air_kn': air_kn,
                'cabin_mg': cabin_mg,
                'cabin_mg_hp': cabin_mg_hp,
                'cabin_wix': cabin_wix,
                'cabin_kn': cabin_kn,
            }

            all_match = all(actual[k] == expected[k] for k in expected)
            print(f"\n  Match: {'PASS' if all_match else 'FAIL'}")
            if not all_match:
                for k in expected:
                    if actual[k] != expected[k]:
                        print(f"    {k}: got '{actual[k]}' expected '{expected[k]}'")
    else:
        print("COROLLA Hybrid not found!")

    # Print footnote samples
    print("\n=== Footnote Samples ===")
    out_cursor.execute("SELECT fn, text_en FROM footnotes WHERE fn IN (61, 561) ORDER BY fn")
    for fn, text in out_cursor.fetchall():
        print(f"FN {fn}: {text}")

    out_conn.close()
    print("\nDatabase created successfully at:", OUTPUT_DB)


if __name__ == '__main__':
    main()
