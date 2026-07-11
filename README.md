# partadex

Ultimate automotive parts interchange catalogue.

## What's here

| File | Contents |
|---|---|
| `data/oilfilter.db` | Oil filter applications, 48 makes, 2004–2023 (~11,500 vehicles). Brands: Microgard, Microgard Select, WIX, WIX XP, Mobil 1, K&N. |
| `data/aircabin.db` | Engine air + cabin air filter applications (Microgard / Microgard HEPA / WIX / K&N). Growing make-by-make; see `docs/ROADMAP.md` for coverage. |
| `data/brakes.db` | Brake pads (10,462 applications, 73 makes, FMSI D-numbers) and rotors/drums (21,389 applications, 82 makes, 1951–2026, standard + premium lines). |
| `data/filter_catalog_staging.db` | Raw extraction staging with page/position provenance for every row. |
| `data/microgard.pdf` | Source: O'Reilly 2023 All-Makes Filter Catalog (922 pp). |
| `data/brakebest_ceramic_pads.pdf` | Source: BrakeBest Select ceramic pads application guide (179 pp). |
| `scripts/` | Deterministic extract / validate / promote pipeline. |
| `docs/` | Roadmap, extraction spec, schema design. |

## Quick lookup

```sh
python3 scripts/lookup.py vehicle HONDA CIVIC 2015   # all filters for a vehicle
python3 scripts/lookup.py part WP10320               # cross-brand interchange
```

## Example queries

```sh
# What oil filter fits a 2015 Honda Civic?
python3 -c "
import sqlite3; con = sqlite3.connect('data/oilfilter.db')
for r in con.execute('''
  SELECT v.engine, f.microgard, f.wix, f.mobil1, f.kn
  FROM vehicles v JOIN filter_sets f ON f.id = v.filter_set_id
  WHERE v.make='HONDA' AND v.model LIKE 'CIVIC%' AND v.year=2015'''):
    print(r)"

# What interchanges with WIX cabin filter WP10320?
python3 -c "
import sqlite3; con = sqlite3.connect('data/aircabin.db')
for r in con.execute('''
  SELECT microgard, microgard_hepa, wix, kn FROM cabin_filter_sets
  WHERE wix LIKE '%WP10320%' '''):
    print(r)"
```

Part numbers may carry a glued catalog footnote suffix (e.g. `WP1032079` =
`WP10320` + footnote 79); footnote meanings are in `aircabin.db`'s
`footnotes` table.

## Pipeline

extract (`scripts/extract_air_cabin.py`, geometric PDF parsing) → stage with
provenance → validate against known-good subset → promote
(`scripts/promote_air_cabin.py`) → final per-category DB. Work is committed
piecemeal so every commit is a usable partial index.
