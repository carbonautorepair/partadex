#!/usr/bin/env python3
"""Geometric extractor for the Air & Cabin Air section of data/microgard.pdf.

Layout knowledge lives in docs/extraction_notes.md. Coordinates are handled
relative to a per-page origin (the x of the footer "See pages 2-4" line,
which equals the model-column left edge) because odd/even pages shift the
whole grid by ~8.3pt.
"""
import argparse
import re
import sqlite3
import sys
from collections import defaultdict

import fitz

PDF = 'data/microgard.pdf'
DB = 'data/filter_catalog_staging.db'
EXTRACTOR_VERSION = '2026-07-07.1'

# 0-indexed inclusive page ranges per make (running-head scan; in-body make
# headers are still honored, this just bounds the scan).
MAKE_PAGES = {
    'ACURA': (419, 426), 'ALFA ROMEO': (427, 428), 'AUDI': (429, 449),
    'BMW': (450, 484), 'BUICK': (485, 492), 'CADILLAC': (493, 503),
    'CHEVROLET': (504, 541), 'CHRYSLER': (542, 548),
    'DODGE (ALSO SEE RAM)': (549, 563), 'FIAT': (564, 566),
    'FORD': (567, 598), 'FREIGHTLINER': (599, 601), 'GENESIS': (602, 602),
    'GMC LIGHT TRUCKS': (603, 623), 'HONDA': (624, 636), 'HUMMER': (637, 637),
    'HYUNDAI': (638, 650), 'INFINITI': (651, 659), 'ISUZU': (660, 660),
    'JAGUAR': (661, 668), 'JEEP': (669, 677), 'KIA': (678, 691),
    'LAND ROVER': (692, 698), 'LEXUS': (699, 717), 'LINCOLN': (718, 724),
    'LOTUS': (725, 725), 'MASERATI': (726, 726), 'MAZDA': (727, 734),
    'MERCEDES BENZ': (735, 776), 'MERCURY': (777, 779), 'MINI': (780, 785),
    'MITSUBISHI': (786, 792), 'MOBILITY VENTURES': (793, 793),
    'NISSAN': (794, 811), 'PONTIAC': (812, 815), 'PORSCHE': (816, 826),
    'RAM': (827, 834), 'SAAB': (835, 836), 'SATURN': (837, 838),
    'SCION': (839, 841), 'SMART': (842, 842), 'SUBARU': (843, 853),
    'SUZUKI': (854, 855), 'TESLA': (856, 857), 'TOYOTA': (858, 888),
    'VOLKSWAGEN': (889, 902), 'VOLVO': (903, 914), 'VPG': (915, 915),
}
MAKE_NAMES = set(MAKE_PAGES)

# Column value anchors relative to page origin (origin = model column x).
PART_COLS = [
    ('microgard_engine_air', 'engine_air', 'MICROGARD', 173.0),
    ('wix_engine_air', 'engine_air', 'WIX', 222.5),
    ('kn_engine_air', 'engine_air', 'K&N', 272.0),
    ('microgard_cabin_air', 'cabin_air', 'MICROGARD', 321.5),
    ('wix_cabin_air', 'cabin_air', 'WIX', 371.0),
    ('kn_cabin_air', 'cabin_air', 'K&N', 420.5),
]
LEFT_MAX = 72.0     # rel-x: model/engine text
CODE_MAX = 133.0    # rel-x: engine-code column
VIN_MAX = 163.0     # rel-x: VIN column
PART_SNAP = 27.0    # max distance from a part-column anchor

# A left-column ArialNarrow line starts a NEW application row when it leads
# with a cylinder configuration, displacement, or the "w /" idiom.
NEW_ROW_RE = re.compile(r'^([LVHWIR]\d|\d+\s*[Cc]yl|w\s*/|0\s|\d+(\.\d+)?\s*(L\b|kWh))')
# Pure engine-descriptor modifiers: when one of these appears alone on a
# wrapped line it extends the current row's engine text even if variant part
# numbers stack on its line (e.g. COROLLA Hybrid "Electric/Gas" with the HEPA
# and late-production parts). Lines with parts and OTHER text are their own
# application (e.g. MIRAI "Electric/GasHydrogen" carries different parts than
# the "w / Electric/Hydrogen Eng." line above it).
WRAP_VOCAB = {'Electric/Gas', 'Turbo/Intercooled', 'Turbo', 'Diesel',
              'Turbo Diesel', 'Supercharged', 'Electric/Gas;'}
YEAR_RE = re.compile(r'^(19|20)\d{2}$')
SKIP_VALUES = {'N/A', 'N/R', 'N/S'}


def page_lines(page):
    """Yield (y, [span,...]) sorted by y; span=(x0, text, font, size, super)."""
    d = page.get_text('dict')
    rows = defaultdict(list)
    for b in d['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                t = s['text'].strip()
                if not t:
                    continue
                x0, y0 = s['bbox'][0], s['bbox'][1]
                if x0 < 40 or x0 > 560:      # vertical sidebar letters
                    continue
                sup = bool(s['flags'] & 1)
                rows[0].append((x0, t, s['font'], s['size'], sup, y0,
                                s['bbox'][2]))
    spans = rows[0]
    bases = sorted((s for s in spans if not s[4]), key=lambda s: (s[5], s[0]))
    sups = [s for s in spans if s[4]]
    # attach each superscript to the base span it immediately follows:
    # same visual line (baseline within ~5pt) and starting at that span's
    # right edge (gap < 8pt).
    attach = defaultdict(list)
    for sp in sups:
        # a superscript's bbox top sits 0-4pt below its (larger) base span's
        # top, immediately right of the base text
        cands = [b for b in bases
                 if -0.5 <= sp[5] - b[5] <= 4.0 and -1.0 <= sp[0] - b[6] < 8.0]
        if cands:
            b = min(cands, key=lambda b: abs(sp[5] - b[5] - 1.7))
            attach[(b[5], b[0])].append(sp)
    # cluster bases into visual lines (y can drift a few tenths of a pt
    # within one line, e.g. 169.1 vs 169.3)
    cluster, cy = [], None
    for b in bases:
        if cy is not None and b[5] - cy > 1.5:
            yield cy, sorted(cluster, key=lambda p: p[0][0])
            cluster = []
        if not cluster:
            cy = b[5]
        cluster.append((b, attach.get((b[5], b[0]), [])))
    if cluster:
        yield cy, sorted(cluster, key=lambda p: p[0][0])


def page_origin(page):
    """x of the model column: use the footer 'See pages 2-4' line."""
    for w in page.get_text('words'):
        if w[4] == 'See' and w[1] > 700:
            return w[0]
    # fallback: leftmost Arial-Black span
    xs = [s['bbox'][0] for b in page.get_text('dict')['blocks']
          for l in b.get('lines', []) for s in l['spans']
          if 'Black' in s['font'] and s['bbox'][0] < 110]
    return min(xs) if xs else 71.3


class Row:
    __slots__ = ('make', 'year', 'model', 'engine', 'code', 'vin', 'fn',
                 'page', 'y', 'parts')

    def __init__(self, make, year, model, page, y):
        self.make, self.year, self.model = make, year, model
        self.engine, self.code, self.vin, self.fn = [], '', '', None
        self.page, self.y = page, y
        self.parts = []          # (source_column, category, brand, part, fn)


def clean_make(text):
    return re.sub(r"\s*\(Cont'd/Suite\)\s*$", '', text).strip()


def extract_pages(doc, first, last, state=None):
    """Extract rows from 0-indexed pages [first,last]. Returns rows list."""
    rows = []
    make = year = model = None
    cur = None

    def close():
        nonlocal cur
        if cur is not None and cur.parts:
            rows.append(cur)
        cur = None

    for pno in range(first, last + 1):
        page = doc[pno]
        origin = page_origin(page)
        for y, pairs in page_lines(page):
            if y < 50 or y > 736:
                continue
            base = [p[0] for p in pairs]
            text_all = ' '.join(s[1] for s in base)
            # column headers block
            if y < 90 and re.search(r'Year/Año|Model/Modelo|Engine/Motor|'
                                    r'Eng\.|Código|VIN|NIV|^Microgard|^Wix$|^WIX$|^K&N$',
                                    text_all):
                continue
            # in-body make header (centered)
            if base[0][0] - origin > 100 and clean_make(text_all) in MAKE_NAMES:
                new_make = clean_make(text_all)
                if new_make != make:
                    close()
                    make, year, model = new_make, None, None
                continue
            left = [p for p in pairs if p[0][0] - origin < LEFT_MAX]
            codes = [p for p in pairs if LEFT_MAX <= p[0][0] - origin < CODE_MAX]
            vins = [p for p in pairs if CODE_MAX <= p[0][0] - origin < VIN_MAX]
            parts = [p for p in pairs if p[0][0] - origin >= VIN_MAX]
            left_b = [p[0] for p in left]
            codes_b = [p[0] for p in codes]

            # year marker (sits in the left/eng-code zone, bare 4 digits)
            nonpart = left_b + codes_b
            if nonpart and YEAR_RE.match(nonpart[0][1]) and not parts:
                close()
                year = int(nonpart[0][1])
                continue
            if left_b and "(Cont'd/Suite)" in left_b[0][1] and not parts:
                continue

            if left:
                ltxt = ' '.join(' '.join(s[1].split()) for s in left_b)
                new_row = (NEW_ROW_RE.match(ltxt) or cur is None
                           or (parts and ltxt not in WRAP_VOCAB))
                if 'Black' in left_b[0][2]:          # model line
                    close()
                    model = ltxt
                elif new_row:                         # new application row
                    close()
                    if make is None or year is None or model is None:
                        continue
                    cur = Row(make, year, model, pno + 1, y)
                    cur.engine.append(ltxt)
                else:                                 # wrapped engine text
                    cur.engine.append(ltxt)
            elif codes and cur is not None and cur.parts and not YEAR_RE.match(codes_b[0][1]):
                # engine-code-only line: same engine, next code variant
                prev = cur
                close()
                cur = Row(prev.make, prev.year, prev.model, pno + 1, y)
                cur.engine = list(prev.engine)

            if cur is None:
                continue
            if codes and not YEAR_RE.match(codes_b[0][1]):
                cur.code = (cur.code + ' ' + ' '.join(s[1] for s in codes_b)).strip()
            if vins:
                cur.vin = (cur.vin + ',' + ','.join(p[0][1] for p in vins)).strip(',')
            # engine/code-line superscript footnote
            for p in left + codes:
                for sp in p[1]:
                    if sp[1].isdigit():
                        cur.fn = sp[1]

            for s, ssups in parts:
                text = s[1].strip()
                # position/variant qualifiers: either glued ("49257 - Left")
                # or a standalone span (" - Right") that drifts into the next
                # column's bin — attach those to the preceding part.
                qual = None
                if text.startswith('-'):
                    if cur.parts:
                        p = cur.parts[-1]
                        cur.parts[-1] = p[:5] + (text.lstrip('- ').strip(),)
                    continue
                if ' - ' in text:
                    text, qual = (t.strip() for t in text.split(' - ', 1))
                relx = s[0] - origin
                col = min(PART_COLS, key=lambda c: abs(relx - c[3]))
                if abs(relx - col[3]) > PART_SNAP:
                    col = ('unknown_column', 'cabin_air', 'UNKNOWN', relx)
                fn = next((sp[1] for sp in ssups if sp[1].isdigit()), None)
                for token in text.split(','):
                    token = token.strip()
                    # 'N/A'/'N/R'/'N/S' = not offered (sometimes with a glued
                    # footnote digit when the superscript flag is lost)
                    if not token or token.startswith('N/'):
                        continue
                    cur.parts.append((col[0], col[1], col[2], token, fn, qual))
        # page break: keep cur open only if next page continues same make
    close()
    return rows


def brand_for(col, part):
    if col == 'microgard_cabin_air' and part.endswith('HP'):
        return 'MICROGARD HEPA'
    return dict((c[0], c[2]) for c in PART_COLS).get(col, 'UNKNOWN')


def write_run(con, rows, note):
    cur = con.cursor()
    cur.execute("insert into extraction_runs (source_pdf_path, extractor_version, notes) values (?,?,?)",
                (PDF, EXTRACTOR_VERSION, note))
    run_id = cur.lastrowid
    cols = [r[1] for r in cur.execute('pragma table_info(application_rows)')]
    if 'vin' not in cols:
        cur.execute('alter table application_rows add column vin TEXT')
    n_parts = 0
    for r in rows:
        cur.execute("""insert into application_rows
            (run_id, make, year, model, engine, engine_code, row_footnote,
             source_pdf_path, source_page, source_y, raw_row_text, confidence,
             status, notes, vin)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, r.make, r.year, r.model, ' '.join(r.engine),
             r.code or None, r.fn, PDF, r.page, r.y, None, 'parsed',
             'needs_spot_check', None, r.vin or None))
        row_id = cur.lastrowid
        seen = set()
        for col, cat, _, part, fn, qual in r.parts:
            key = (col, part, fn)
            if key in seen:
                continue
            seen.add(key)
            raw = part + (fn or '')
            cur.execute("""insert into application_parts
                (application_row_id, filter_category, brand, source_column,
                 raw_part_number, normalized_part_number, footnote,
                 confidence, status, notes)
                values (?,?,?,?,?,?,?,?,?,?)""",
                (row_id, cat, brand_for(col, part), col, raw, part, fn,
                 'parsed', 'ok', qual))
            n_parts += 1
    con.commit()
    return run_id, n_parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('makes', nargs='*', help='make names (default: all)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--note', default='Air/cabin geometric extraction')
    args = ap.parse_args()

    doc = fitz.open(PDF)
    targets = args.makes or list(MAKE_PAGES)
    all_rows = []
    for mk in targets:
        first, last = MAKE_PAGES[mk]
        # sections start mid-page: scan one page early, attribution comes
        # from the in-body make headers.
        first = max(first - 1, 419)
        rows = [r for r in extract_pages(doc, first, last) if r.make == mk]
        print(f'{mk}: {len(rows)} rows, {sum(len(r.parts) for r in rows)} parts',
              file=sys.stderr)
        all_rows.extend(rows)
    if args.dry_run:
        print(f'DRY RUN: {len(all_rows)} rows total', file=sys.stderr)
        return all_rows
    con = sqlite3.connect(DB)
    run_id, n_parts = write_run(con, all_rows,
                                f'{args.note}: {", ".join(targets)}')
    print(f'run {run_id}: {len(all_rows)} rows, {n_parts} parts')


if __name__ == '__main__':
    main()
