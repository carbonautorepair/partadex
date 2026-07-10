#!/usr/bin/env python3
"""Extract the BrakeBest drums & rotors application catalogs into brakes.db.

Layout (both catalogs share it): index pages first, then application pages
with columns Years (x~74) / Product (x~165) / Part Notes-Qualifiers (x~266) /
Part Number (x~507). Bold 13pt lines are 'MAKE - MODEL' or position
('Front'/'Rear') headers. Rows without a year inherit the previous row's
year range. Qualifier text may wrap to a line of its own (no product/part):
append it to the previous row.
"""
import re
import sqlite3

import fitz

SOURCES = [
    ('data/brakebest_drums_rotors.pdf', 'standard'),
    ('data/brakebest_premium_drums_rotors.pdf', 'premium'),
]
DB = 'data/brakes.db'
YEAR_RE = re.compile(r'^(\d{4})(?:-(\d{4}))?$')


def extract(pdf, line_name):
    doc = fitz.open(pdf)
    rows = []
    make = model = position = None
    cur_years = None

    for pno in range(len(doc)):
        text_head = doc[pno].get_text()[:100]
        if 'APPLICATION INDEX' in text_head or pno == 0:
            continue
        lines = {}
        origin = None
        for b in doc[pno].get_text('dict')['blocks']:
            for l in b.get('lines', []):
                for s in l['spans']:
                    t = s['text'].strip()
                    if not t:
                        continue
                    if t == 'Years' and 'Bold' in s['font']:
                        origin = s['bbox'][0]        # pages alternate origins
                    if re.match(r'^Page\s+\d+$', t):     # running footer
                        continue
                    y = round(s['bbox'][1], 1)
                    lines.setdefault(y, []).append(
                        (round(s['bbox'][0], 1), t, s['font'], round(s['size'], 1)))
        if origin is None:
            continue
        for y in sorted(lines):
            spans = sorted((s[0] - origin + 75.0,) + s[1:] for s in lines[y])
            x0, t0, font0, sz0 = spans[0]
            if y < 80:                                   # column header row
                continue
            if 'Bold' in font0 and sz0 >= 12:
                if t0 in ('Front', 'Rear'):
                    position = t0
                elif ' - ' in t0:
                    make, model = (p.strip() for p in t0.split(' - ', 1))
                    model = re.sub(r"\s*\(cont'd\.?\)\s*$", '', model)
                    position = None
                # any other bold text (section banners) is ignored
                continue
            year_sp = [s for s in spans if s[0] < 160]
            prod_sp = [s for s in spans if 160 <= s[0] < 260]
            qual_sp = [s for s in spans if 260 <= s[0] < 500]
            part_sp = [s for s in spans if s[0] >= 500]

            if year_sp:
                m = YEAR_RE.match(year_sp[0][1])
                if m:
                    cur_years = (int(m.group(1)),
                                 int(m.group(2) or m.group(1)))
            if prod_sp and part_sp:
                if not (make and model and position and cur_years):
                    continue
                rows.append({
                    'make': make, 'model': model, 'position': position,
                    'year_start': cur_years[0], 'year_end': cur_years[1],
                    'product': ' '.join(s[1] for s in prod_sp),
                    'qualifiers': ' '.join(s[1] for s in qual_sp),
                    'part_number': ' '.join(s[1] for s in part_sp),
                    'line': line_name, 'page': pno + 1, 'y': y})
            elif (qual_sp or prod_sp) and not part_sp and rows:
                # wrapped continuation: product names ("Disc Brake Rotor and
                # / Hub Assembly") and long qualifiers spill onto a line of
                # their own below the row
                if prod_sp:
                    rows[-1]['product'] = (rows[-1]['product'] + ' ' +
                                           ' '.join(s[1] for s in prod_sp)).strip()
                if qual_sp:
                    rows[-1]['qualifiers'] = (rows[-1]['qualifiers'] + ' ' +
                                              ' '.join(s[1] for s in qual_sp)).strip()
    return rows


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript("""
    DROP TABLE IF EXISTS rotor_applications;
    CREATE TABLE rotor_applications (
      id INTEGER PRIMARY KEY,
      make TEXT NOT NULL, model TEXT NOT NULL, position TEXT NOT NULL,
      year_start INTEGER NOT NULL, year_end INTEGER NOT NULL,
      product TEXT NOT NULL, qualifiers TEXT,
      part_number TEXT NOT NULL, line TEXT NOT NULL,
      source_page INTEGER NOT NULL, source_y REAL NOT NULL
    );
    CREATE INDEX idx_rotors_lookup
      ON rotor_applications(make, model, year_start, year_end);
    CREATE INDEX idx_rotors_part ON rotor_applications(part_number);
    """)
    for pdf, line_name in SOURCES:
        rows = extract(pdf, line_name)
        for r in rows:
            cur.execute("""INSERT INTO rotor_applications
                (make, model, position, year_start, year_end, product,
                 qualifiers, part_number, line, source_page, source_y)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (r['make'], r['model'], r['position'], r['year_start'],
                 r['year_end'], r['product'], r['qualifiers'] or None,
                 r['part_number'], r['line'], r['page'], r['y']))
        print(f'{line_name}: {len(rows)} rows')
    con.commit()
    for row in cur.execute("""select line, count(*), count(distinct make),
            count(distinct make||'|'||model), min(year_start), max(year_end),
            group_concat(distinct product) from rotor_applications group by line"""):
        print(row)


if __name__ == '__main__':
    main()
