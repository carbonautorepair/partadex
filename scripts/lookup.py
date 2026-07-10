#!/usr/bin/env python3
"""Unified partadex lookup.

  lookup.py vehicle MAKE MODEL [YEAR]     filters for a vehicle
  lookup.py part NUMBER                   cross-brand interchange for a part

Searches data/oilfilter.db and data/aircabin.db. MODEL matches as a prefix;
part numbers match ignoring case and glued footnote suffixes.
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OIL = os.path.join(HERE, '..', 'data', 'oilfilter.db')
AIRCABIN = os.path.join(HERE, '..', 'data', 'aircabin.db')
BRAKES = os.path.join(HERE, '..', 'data', 'brakes.db')

OIL_BRANDS = [('microgard', 'Microgard'), ('microgard_select', 'Microgard Select'),
              ('wix', 'WIX'), ('wix_xp', 'WIX XP'), ('mobil1', 'Mobil 1'),
              ('kn', 'K&N')]
AIR_BRANDS = [('microgard', 'Microgard'), ('wix', 'WIX'), ('kn', 'K&N')]
CABIN_BRANDS = [('microgard', 'Microgard'), ('microgard_hepa', 'Microgard HEPA'),
                ('wix', 'WIX'), ('kn', 'K&N')]


def fmt_set(row, brands):
    return ' | '.join(f'{label}: {row[col]}' for col, label in brands
                      if row[col] not in (None, '', 'N/A'))


def vehicle(make, model, year=None):
    make, model = make.upper(), model.upper()
    yr = f' AND year={int(year)}' if year else ''
    con = sqlite3.connect(OIL)
    con.row_factory = sqlite3.Row
    print('== OIL FILTERS ==')
    for r in con.execute(
            f"SELECT v.year, v.model, v.engine, f.* FROM vehicles v "
            f"JOIN filter_sets f ON f.id=v.filter_set_id "
            f"WHERE v.make=? AND upper(v.model) LIKE ?{yr} "
            f"ORDER BY v.year DESC, v.model", (make, model + '%')):
        print(f"  {r['year']} {r['model']} {r['engine']}")
        print(f"    {fmt_set(r, OIL_BRANDS)}")
    con = sqlite3.connect(AIRCABIN)
    con.row_factory = sqlite3.Row
    print('== AIR / CABIN FILTERS ==')
    for r in con.execute(
            f"SELECT v.year, v.model, v.engine, v.eng_code, "
            f"a.microgard a_mg, a.wix a_wx, a.kn a_kn, "
            f"c.microgard c_mg, c.microgard_hepa c_hp, c.wix c_wx, c.kn c_kn "
            f"FROM vehicles v "
            f"LEFT JOIN air_filter_sets a ON a.id=v.air_set_id "
            f"LEFT JOIN cabin_filter_sets c ON c.id=v.cabin_set_id "
            f"WHERE v.make=? AND upper(v.model) LIKE ?{yr} "
            f"ORDER BY v.year DESC, v.model", (make, model + '%')):
        print(f"  {r['year']} {r['model']} {r['engine']} {r['eng_code'] or ''}")
        air = ' | '.join(f'{l}: {r[k]}' for k, l in
                         [('a_mg', 'Microgard'), ('a_wx', 'WIX'), ('a_kn', 'K&N')] if r[k])
        cab = ' | '.join(f'{l}: {r[k]}' for k, l in
                         [('c_mg', 'Microgard'), ('c_hp', 'HEPA'),
                          ('c_wx', 'WIX'), ('c_kn', 'K&N')] if r[k])
        if air:
            print(f'    engine air: {air}')
        if cab:
            print(f'    cabin air:  {cab}')
    if os.path.exists(BRAKES):
        con = sqlite3.connect(BRAKES)
        con.row_factory = sqlite3.Row
        print('== BRAKE PADS ==')
        cond = ' AND ? BETWEEN year_start AND year_end' if year else ''
        args = [make.title(), model.title() + '%'] + ([int(year)] if year else [])
        for r in con.execute(
                f"SELECT * FROM pad_applications WHERE upper(make)=upper(?) "
                f"AND upper(model) LIKE upper(?){cond} "
                f"ORDER BY year_end DESC, position", args):
            parts = ' | '.join(f'{l}: {r[k]}' for k, l in
                               [('brakebest_ceramic', 'BrakeBest Ceramic'),
                                ('bendix', 'Bendix'),
                                ('semi_metallic', 'Semi-Metallic')] if r[k])
            print(f"  {r['year_start']}-{r['year_end']} {r['model']} "
                  f"{r['position']}: {r['application']}")
            print(f"    {parts} | FMSI: {r['fmsi']}")


def part(number):
    num = number.upper().replace(' ', '')

    def match(val):
        if not val:
            return False
        return any(num == v.strip().upper() or v.strip().upper().startswith(num)
                   for v in val.split(','))

    def scan(db, table, brands, vjoin, category):
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        for r in con.execute(f'SELECT * FROM {table}'):
            if any(match(r[c]) for c, _ in brands):
                n, apps = con.execute(vjoin, (r['id'],)).fetchone()
                print(f'{category} interchange set:')
                print(f'  {fmt_set(r, brands)}')
                print(f'  fits {n} vehicles ({apps})')

    scan(OIL, 'filter_sets', OIL_BRANDS,
         "SELECT count(*), group_concat(DISTINCT make) FROM vehicles WHERE filter_set_id=?",
         'OIL')
    scan(AIRCABIN, 'air_filter_sets', AIR_BRANDS,
         "SELECT count(*), group_concat(DISTINCT make) FROM vehicles WHERE air_set_id=?",
         'ENGINE AIR')
    scan(AIRCABIN, 'cabin_filter_sets', CABIN_BRANDS,
         "SELECT count(*), group_concat(DISTINCT make) FROM vehicles WHERE cabin_set_id=?",
         'CABIN AIR')
    if os.path.exists(BRAKES):
        con = sqlite3.connect(BRAKES)
        con.row_factory = sqlite3.Row
        seen = set()
        for r in con.execute(
                "SELECT * FROM pad_applications WHERE upper(brakebest_ceramic) LIKE ? "
                "OR upper(bendix) LIKE ? OR upper(semi_metallic) LIKE ? OR upper(fmsi)=?",
                (num + '%', num + '%', num + '%', num)):
            key = (r['brakebest_ceramic'], r['bendix'], r['semi_metallic'], r['fmsi'])
            if key in seen:
                continue
            seen.add(key)
            n, apps = con.execute(
                "SELECT count(*), group_concat(DISTINCT make) FROM pad_applications "
                "WHERE coalesce(fmsi,'')=coalesce(?,'')", (r['fmsi'],)).fetchone()
            print('BRAKE PAD interchange (via FMSI):')
            print(f"  FMSI {r['fmsi']} | BrakeBest Ceramic: {r['brakebest_ceramic']} | "
                  f"Bendix: {r['bendix']} | Semi-Metallic: {r['semi_metallic']}")
            print(f'  fits {n} applications ({apps})')


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == 'vehicle':
        vehicle(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    elif len(sys.argv) == 3 and sys.argv[1] == 'part':
        part(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)
