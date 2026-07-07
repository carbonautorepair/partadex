#!/usr/bin/env python3
"""Validate extract_air_cabin.py against extraction run 1 (HONDA/LEXUS/TOYOTA).

Dry-run re-extraction, no DB writes. Rows are grouped by (make, year, model)
and paired by engine/code/VIN token sets — run 1 was inconsistent about which
column engine codes and VIN letters landed in, and left engine text empty on
code-variant rows, so exact string equality is the wrong bar. A ref row
matches a new row when the ref tokens are a subset of the new tokens.
Part sets compare as (category, brand, normalized part, footnote), with
run-1's UNKNOWN_CABIN_COLUMN mapped to K&N per docs/extraction_notes.md.
"""
import re
import sqlite3
import sys
from collections import defaultdict

sys.argv = [sys.argv[0]]
import extract_air_cabin as ex
import fitz

MAKES = ['HONDA', 'LEXUS', 'TOYOTA']


def tokens(*texts):
    out = set()
    for t in texts:
        for tok in re.split(r'[\s,/]+', t or ''):
            if tok:
                out.add(tok.upper())
    return frozenset(out)


def norm_model(m):
    return ' '.join((m or '').split()).upper()


def load_run1(con):
    groups = defaultdict(list)
    for rid, make, year, model, engine, code in con.execute(
            "select id, make, year, model, engine, engine_code from application_rows where run_id=1"):
        parts = frozenset(
            ('K&N' if brand == 'UNKNOWN_CABIN_COLUMN' else brand, cat, part, fn or '')
            for cat, brand, part, fn in con.execute(
                "select filter_category, brand, normalized_part_number, footnote "
                "from application_parts where application_row_id=?", (rid,))
            if part not in ('NA61',))   # run-1 artifact: 'N/A' + footnote 61
        groups[(make, year, norm_model(model))].append(
            {'tok': tokens(engine, code), 'parts': parts,
             'raw': (engine, code)})
    return groups


def new_rows():
    doc = fitz.open(ex.PDF)
    groups = defaultdict(list)
    for mk in MAKES:
        first, last = ex.MAKE_PAGES[mk]
        for r in ex.extract_pages(doc, max(first - 1, 419), last):
            if r.make != mk:
                continue
            seen, parts = set(), set()
            for col, cat, _, part, fn, qual in r.parts:
                k = (col, part, fn)
                if k in seen:
                    continue
                seen.add(k)
                parts.add((ex.brand_for(col, part), cat, part, fn or ''))
            groups[(r.make, r.year, norm_model(r.model))].append(
                {'tok': tokens(' '.join(r.engine), r.code, r.vin),
                 'parts': frozenset(parts), 'page': r.page, 'y': r.y,
                 'raw': (' '.join(r.engine), r.code, r.vin), 'used': False})
    return groups


def main():
    con = sqlite3.connect(ex.DB)
    ref = load_run1(con)
    new = new_rows()

    total_ref = sum(len(v) for v in ref.values())
    total_new = sum(len(v) for v in new.values())
    row_match = 0
    unmatched_ref, part_diffs = [], []

    for key, refs in sorted(ref.items()):
        cands = new.get(key, [])
        for rr in refs:
            hit = None
            for c in cands:
                if not c['used'] and (rr['tok'] <= c['tok'] or rr['tok'] == c['tok']):
                    hit = c
                    break
            if hit is None:   # second chance: ignore token containment
                for c in cands:
                    if not c['used'] and rr['parts'] == c['parts']:
                        hit = c
                        break
            if hit is None:
                unmatched_ref.append((key, rr['raw']))
                continue
            hit['used'] = True
            row_match += 1
            if rr['parts'] != hit['parts']:
                part_diffs.append((key, hit['page'], hit['y'],
                                   sorted(rr['parts'] - hit['parts']),
                                   sorted(hit['parts'] - rr['parts'])))

    unmatched_new = [(k, c['raw'], c['page'], c['y'])
                     for k, v in new.items() for c in v if not c['used']]

    print(f'rows: ref={total_ref} new={total_new} matched={row_match} '
          f'({100*row_match/total_ref:.2f}% of ref)')
    exact = row_match - len(part_diffs)
    print(f'part-sets on matched rows: exact={exact} diff={len(part_diffs)} '
          f'({100*exact/row_match:.2f}%)')
    print(f'\nunmatched run-1 rows ({len(unmatched_ref)}):')
    for key, raw in unmatched_ref:
        print('  ', key, raw)
    print(f'\nunmatched new rows ({len(unmatched_new)}):')
    for key, raw, page, y in sorted(unmatched_new):
        print(f'   {key} {raw} p{page} y={y}')
    print(f'\npart-set diffs ({len(part_diffs)}):')
    for key, page, y, ref_only, new_only in part_diffs[:60]:
        print(f'  {key} p{page} y={y}')
        if ref_only:
            print(f'    run1 only: {ref_only}')
        if new_only:
            print(f'    new only:  {new_only}')


if __name__ == '__main__':
    main()
