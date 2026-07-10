#!/usr/bin/env python3
"""Extract the BrakeBest Select ceramic pads application guide into brakes.db.

Layout (see docs/brake_plan.md): per-page grid anchored at the 'PART TYPE'
header x (pages alternate between two origins). Relative x offsets:
  make/model +0 (make sz 8.5, model sz 7.5), part type +3,
  year +109.9, application +148.8,
  brakebest_ceramic +323.8, bendix +373.5, semi_metallic +427.7.
FMSI D-numbers derive from the part numbers (C1058 -> D1058,
MKD184FM -> D184), giving cross-brand interchange.
"""
import re
import sqlite3

import fitz

PDF = 'data/brakebest_ceramic_pads.pdf'
DB = 'data/brakes.db'

COLS = [('brakebest_ceramic', 323.8), ('bendix', 373.5), ('semi_metallic', 427.7)]
YEAR_RE = re.compile(r'^(\d{4})(?:-(\d{2,4}))?$')
FMSI_RE = re.compile(r'^[A-Z]*?D?(\d+)')


def fmsi_of(part):
    m = re.match(r'^(?:C|D|MKD|SMD|TD|XD)?(\d+)', part)
    return f'D{m.group(1)}' if m else None


def parse_year(text):
    m = YEAR_RE.match(text)
    if not m:
        return None
    end = int(m.group(1))
    if not m.group(2):
        return end, end
    s = m.group(2)
    start = int(s) if len(s) == 4 else (end // 100) * 100 + int(s)
    if start > end:
        start -= 100
    return start, end


def lines_of(page):
    rows = {}
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                t = s['text'].strip()
                if not t:
                    continue
                y = round(s['bbox'][1], 1)
                rows.setdefault(y, []).append(
                    (round(s['bbox'][0], 1), t, round(s['size'], 1)))
    return {y: sorted(v) for y, v in sorted(rows.items())}


def strip_contd(t):
    return re.sub(r"\s*\((Cont'd\.?|cont'd\.?)\)\s*$", '', t).strip()


def extract():
    doc = fitz.open(PDF)
    rows_out = []
    make = model = part_type = None
    cur = None

    def close():
        nonlocal cur
        if cur:
            rows_out.append(cur)
        cur = None

    for pno in range(len(doc)):
        page = doc[pno]
        lines = lines_of(page)
        origin = None
        for y, spans in lines.items():
            for x, t, sz in spans:
                if t == 'PART TYPE':
                    origin = x
        if origin is None:
            continue
        pend_app, pend_parts = [], []
        for y, spans in lines.items():
            if y < 75:
                continue
            rel = [(x - origin, t, sz) for x, t, sz in spans]
            left = [r for r in rel if r[0] < 100]
            year_sp = [r for r in rel if 100 <= r[0] < 140]
            app_sp = [r for r in rel if 140 <= r[0] < 320]
            part_sp = [r for r in rel if r[0] >= 320]

            if left:
                relx, t, sz = left[0]
                close()
                if sz >= 8.4:
                    make, model, part_type = strip_contd(t), None, None
                    pend_app, pend_parts = [], []
                elif sz >= 7.4:
                    model = strip_contd(t)
                    pend_app, pend_parts = [], []
                else:
                    # part-type labels share their line with the block's
                    # first year row, whose head text may already be buffered
                    part_type = strip_contd(re.sub(r'\s*\(cont\'d\)\s*$', '', t))
                # NO continue: a part-type label often shares its visual
                # line with the block's first year row
            if year_sp:
                yr = parse_year(year_sp[0][1])
                if yr is None:
                    continue
                close()
                # multi-line cells are vertically centered: application text
                # starts on the line ABOVE the year, so buffered app-only
                # lines are a PREFIX of this row's application
                app = ' '.join(pend_app + [t for _, t, _ in app_sp]).strip()
                pos, sep, rest = app.partition('-')
                cur = {'make': make, 'model': model, 'part_type': part_type,
                       'year_start': yr[0], 'year_end': yr[1],
                       'position': pos if sep and pos in ('Front', 'Rear') else None,
                       'application': rest.strip() if sep and pos in ('Front', 'Rear') else app,
                       'brakebest_ceramic': None, 'bendix': None,
                       'semi_metallic': None,
                       'page': pno + 1, 'y': y}
                part_sp = pend_parts + part_sp
                pend_app, pend_parts = [], []
            elif app_sp or part_sp:
                # two-line cells centre the year: head line sits ~3pt ABOVE
                # the year line (buffer as prefix), tail line ~3pt BELOW it
                # (append to the current row)
                if cur is not None and y - cur['y'] <= 5.0:
                    cur['application'] = (cur['application'] + ' ' +
                                          ' '.join(t for _, t, _ in app_sp)).strip()
                else:
                    pend_app += [t for _, t, _ in app_sp]
                    pend_parts += part_sp
                    continue
            if cur:
                for relx, t, _ in part_sp:
                    col = min(COLS, key=lambda c: abs(relx - c[1]))
                    if abs(relx - col[1]) > 26:
                        continue
                    cur[col[0]] = (cur[col[0]] + ', ' + t) if cur[col[0]] else t
    close()
    return rows_out


def main():
    rows = extract()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript("""
    DROP TABLE IF EXISTS pad_applications;
    CREATE TABLE pad_applications (
      id INTEGER PRIMARY KEY,
      make TEXT NOT NULL, model TEXT NOT NULL, part_type TEXT NOT NULL,
      year_start INTEGER NOT NULL, year_end INTEGER NOT NULL,
      position TEXT, application TEXT,
      brakebest_ceramic TEXT, bendix TEXT, semi_metallic TEXT,
      fmsi TEXT,
      source_page INTEGER NOT NULL, source_y REAL NOT NULL
    );
    CREATE INDEX idx_pads_lookup ON pad_applications(make, model, year_start, year_end);
    CREATE INDEX idx_pads_fmsi ON pad_applications(fmsi);
    """)
    n = 0
    for r in rows:
        if not (r['make'] and r['model'] and r['part_type']):
            continue
        parts = [p for p in (r['brakebest_ceramic'], r['bendix'],
                             r['semi_metallic']) if p]
        if not parts:
            continue
        fmsi = fmsi_of(parts[0].split(',')[0].strip())
        cur.execute("""INSERT INTO pad_applications
            (make, model, part_type, year_start, year_end, position,
             application, brakebest_ceramic, bendix, semi_metallic, fmsi,
             source_page, source_y) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r['make'], r['model'], r['part_type'], r['year_start'],
             r['year_end'], r['position'], r['application'],
             r['brakebest_ceramic'], r['bendix'], r['semi_metallic'], fmsi,
             r['page'], r['y']))
        n += 1
    con.commit()
    print(f'inserted {n} pad applications '
          f'({len(rows) - n} rows skipped without part numbers)')
    for row in cur.execute("""select count(distinct make),
            count(distinct make||'|'||model), min(year_start), max(year_end)
            from pad_applications"""):
        print('makes, models, year range:', row)


if __name__ == '__main__':
    main()
