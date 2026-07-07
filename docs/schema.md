# Partadex final catalog schemas

## Design rules

- One SQLite file per part category (matches `oilfilter.db` precedent); each
  is independently usable — a partial index is still a shippable index.
- Interchange is represented as a *set row*: one row per unique cross-brand
  part combination; vehicles reference the set. Querying "what fits my car"
  and "what interchanges with part X" are both single joins.
- Part numbers keep the catalog's glued footnote digits (e.g. `5133453` =
  WIX 51334 + footnote 53), same as `oilfilter.db`. Footnote meanings live in
  the footnotes table extracted from catalog pages 2–4.
- Multiple alternative parts for the same brand+application are joined with
  `, ` inside the set column (e.g. cabin WIX `WP1032079, WP1032278` —
  early/late production).

## data/oilfilter.db (existing, unchanged)

- `filter_sets(id, microgard, microgard_select, wix, wix_xp, mobil1, kn)`
- `vehicles(id, make, year, model, engine, eng_code, filter_set_id)`
  - `eng_code` actually holds the catalog's VIN-position letter; the engine
    code is appended to `engine`. Kept as-is for compatibility.

## data/aircabin.db (to be produced by scripts/promote_air_cabin.py)

```sql
CREATE TABLE air_filter_sets (
  id INTEGER PRIMARY KEY,
  microgard TEXT,          -- MGA####
  wix TEXT,                -- WA#### / numeric
  kn TEXT                  -- 33-#### / E-####
);
CREATE TABLE cabin_filter_sets (
  id INTEGER PRIMARY KEY,
  microgard TEXT,          -- ####
  microgard_hepa TEXT,     -- ####HP
  wix TEXT,                -- WP#### / 24###
  kn TEXT                  -- VF####
);
CREATE TABLE vehicles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  make TEXT NOT NULL,
  year INTEGER NOT NULL,
  model TEXT NOT NULL,
  engine TEXT NOT NULL DEFAULT '',
  eng_code TEXT DEFAULT '',      -- engine code column from catalog
  vin TEXT DEFAULT '',           -- VIN-position column
  air_set_id INTEGER REFERENCES air_filter_sets(id),    -- NULL = not listed
  cabin_set_id INTEGER REFERENCES cabin_filter_sets(id) -- NULL = not listed
);
CREATE TABLE footnotes (
  fn INTEGER PRIMARY KEY,
  text_en TEXT NOT NULL
);
CREATE INDEX idx_vehicles_lookup ON vehicles(make, model, year);
```

Promotion rules (staging → aircabin.db):

1. Read all `application_rows` + `application_parts` from
   `data/filter_catalog_staging.db` (all runs; prefer the newest run when the
   same make appears in several runs).
2. Map run-1 `UNKNOWN_CABIN_COLUMN` / `right_cabin_air_column` →
   brand `K&N`, column `kn_cabin_air`.
3. Per application row, build the air triple and cabin quadruple by joining
   deduped raw part numbers per brand with `, `; find-or-create the set row;
   insert vehicle. Rows with no parts at all in a category get NULL set id.
4. Extract footnotes table from PDF pages 2–4 (English text).
5. Unlike oilfilter.db, keep engine code in `eng_code` and VIN in `vin`
   (don't replicate the legacy column-shift quirk).

## Future categories

- Brake pads: `pad_sets` with per-brand/per-position columns (front/rear are
  separate applications). Rotors similar (`rotor_sets`). Schema finalized
  when a source catalog is in hand.
