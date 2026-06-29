#!/usr/bin/env python3
"""
Assemble partadex.db from oil_rows.json and air_cabin_rows.json.
Normalizes into vehicles + filter_applications tables.
Validates part numbers and drops junk.
"""

import json
import re
import sqlite3
import sys

DB_PATH = "data/partadex.db"

VALID_PART_PATTERNS = {
    "microgard":        re.compile(r'^MGL\d{3,6}$'),
    "microgard_select": re.compile(r'^MSL\d{3,6}$'),
    "wix":              re.compile(r'^(WL)?\d{3,6}$'),
    "wix_xp":           re.compile(r'^(WL)?\d{3,6}XP$'),
    "mobil1":           re.compile(r'^M1C?-\d{3,4}[A-Z]?$'),
    "kn":               re.compile(r'^HP-\d{3,5}$'),
    "air_microgard":    re.compile(r'^MGA\d{3,6}$'),
    "air_wix":          re.compile(r'^(WA\d{3,6}|\d{2}-\d{3,5}|\d{3,6}(FR)?)$'),
    "air_kn":           re.compile(r'^\d{2}-\d{3,5}$'),
    "cabin_microgard":  re.compile(r'^MGA\d{3,6}$'),
    "cabin_wix":        re.compile(r'^(WP\d{3,6}|VF\d{3,6}|\d{3,6}XP|\d{3,6})$'),
    "cabin_kn":         re.compile(r'^VF\d{3,6}$'),
}

BRAND_MAP = {
    "microgard": "Microgard",
    "microgard_select": "Microgard Select",
    "wix": "WIX",
    "wix_xp": "WIX XP",
    "mobil1": "Mobil 1",
    "kn": "K&N",
    "air_microgard": "Microgard",
    "air_wix": "WIX",
    "air_kn": "K&N",
    "cabin_microgard": "Microgard",
    "cabin_wix": "WIX",
    "cabin_kn": "K&N",
}

CATEGORY_MAP = {
    "microgard": "oil",
    "microgard_select": "oil",
    "wix": "oil",
    "wix_xp": "oil",
    "mobil1": "oil",
    "kn": "oil",
    "air_microgard": "air",
    "air_wix": "air",
    "air_kn": "air",
    "cabin_microgard": "cabin",
    "cabin_wix": "cabin",
    "cabin_kn": "cabin",
}

JUNK_MODELS = {
    'DIESEL', 'GAS', 'FLEX', 'HYBRID', 'ELECTRIC', 'CNG', 'LPG',
    'TURBO', 'SUPERCHARGED', 'FFV', 'NATURAL GAS', 'ALL', 'MAKES',
    'OIL', 'FILTERS', 'AIR', 'CABIN', 'ENGINE', 'FILTER',
}


def is_valid_part(col_name, part_number):
    pattern = VALID_PART_PATTERNS.get(col_name)
    if not pattern:
        return len(part_number) >= 3
    return bool(pattern.match(part_number))


def is_valid_row(row):
    if not row.get('make'):
        return False
    model = (row.get('model') or '').upper().strip()
    if model in JUNK_MODELS:
        return False
    if 'footnote' in model.lower() or 'pages' in model.lower():
        return False
    if not row.get('parts'):
        return False
    return True


def main():
    print("Loading extraction data...", file=sys.stderr)
    with open('data/oil_rows.json') as f:
        oil_rows = json.load(f)
    with open('data/air_cabin_rows.json') as f:
        air_rows = json.load(f)

    print(f"Raw: {len(oil_rows)} oil, {len(air_rows)} air/cabin", file=sys.stderr)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript("""
        DROP TABLE IF EXISTS filter_applications;
        DROP TABLE IF EXISTS vehicles;

        CREATE TABLE vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            make TEXT NOT NULL,
            year INTEGER,
            model TEXT,
            engine TEXT,
            source_page INTEGER
        );

        CREATE TABLE filter_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
            filter_category TEXT NOT NULL,  -- oil, air, cabin
            brand TEXT NOT NULL,
            part_number TEXT NOT NULL,
            footnote TEXT,
            source_column TEXT NOT NULL
        );

        CREATE INDEX idx_vehicles_make ON vehicles(make);
        CREATE INDEX idx_vehicles_model ON vehicles(model);
        CREATE INDEX idx_vehicles_year ON vehicles(year);
        CREATE INDEX idx_fa_vehicle ON filter_applications(vehicle_id);
        CREATE INDEX idx_fa_part ON filter_applications(part_number);
        CREATE INDEX idx_fa_category ON filter_applications(filter_category);
    """)

    all_rows = oil_rows + air_rows
    vehicles_inserted = 0
    parts_inserted = 0
    parts_rejected = 0

    for row in all_rows:
        if not is_valid_row(row):
            continue

        valid_parts = []
        for col_name, part_data in row['parts'].items():
            pn = part_data.get('part_number', '').strip()
            if not pn:
                continue
            if is_valid_part(col_name, pn):
                valid_parts.append((col_name, pn, part_data.get('footnote')))
            else:
                parts_rejected += 1

        if not valid_parts:
            continue

        model = row.get('model') or None
        if model:
            model = re.sub(r"\s*\(Cont'd/Suite\)\s*", '', model).strip() or None

        cur.execute(
            "INSERT INTO vehicles (make, year, model, engine, source_page) VALUES (?, ?, ?, ?, ?)",
            (row['make'], row.get('year'), model, row.get('engine'), row.get('source_page'))
        )
        vid = cur.lastrowid
        vehicles_inserted += 1

        for col_name, pn, footnote in valid_parts:
            category = CATEGORY_MAP.get(col_name, 'unknown')
            brand = BRAND_MAP.get(col_name, col_name)
            cur.execute(
                "INSERT INTO filter_applications (vehicle_id, filter_category, brand, part_number, footnote, source_column) VALUES (?, ?, ?, ?, ?, ?)",
                (vid, category, brand, pn, footnote, col_name)
            )
            parts_inserted += 1

    conn.commit()

    # Stats
    print(f"\n=== DB BUILT ===", file=sys.stderr)
    print(f"Vehicles: {vehicles_inserted:,}", file=sys.stderr)
    print(f"Filter applications: {parts_inserted:,}", file=sys.stderr)
    print(f"Parts rejected (invalid format): {parts_rejected:,}", file=sys.stderr)

    # Category breakdown
    for cat in ['oil', 'air', 'cabin']:
        count = cur.execute("SELECT COUNT(*) FROM filter_applications WHERE filter_category=?", (cat,)).fetchone()[0]
        print(f"  {cat}: {count:,}", file=sys.stderr)

    # Make count
    makes = cur.execute("SELECT COUNT(DISTINCT make) FROM vehicles").fetchone()[0]
    print(f"Distinct makes: {makes}", file=sys.stderr)

    # GX 470 test
    print(f"\n=== GX 470 LOOKUP ===", file=sys.stderr)
    gx = cur.execute("""
        SELECT v.make, v.year, v.model, v.engine, fa.filter_category, fa.brand, fa.part_number, fa.footnote
        FROM vehicles v
        JOIN filter_applications fa ON fa.vehicle_id = v.id
        WHERE v.model LIKE '%GX 470%'
        ORDER BY v.year DESC, fa.filter_category, fa.brand
    """).fetchall()
    for r in gx[:12]:
        print(f"  {r[0]} {r[1]} {r[2]} ({r[3]}) {r[4]}/{r[5]}: {r[6]} [ftnt={r[7]}]", file=sys.stderr)

    conn.close()
    print(f"\nDatabase written to {DB_PATH}", file=sys.stderr)


if __name__ == '__main__':
    main()
