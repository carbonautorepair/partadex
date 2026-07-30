# partadex
Ultimate automotive parts interchange catalogue

## Coverage-gap report + supplemental backfill workflow

The canonical catalog lives in `data/partadex.db` (built by
`scripts/build_db.py`) and is treated as **read-only / immutable** by
everything described below. Nothing in this workflow ever opens that
file for writing.

Coverage in `data/partadex.db` isn't perfectly contiguous: a given
make/model (or a specific engine variant of that model) can be missing
one or more model years in the middle of its otherwise-observed range,
either because the vehicle was not produced in those years or because
the source catalog page was skipped, misread, or the row did not pass
extraction validation. The report therefore produces candidates, not
automatic insertions. This workflow finds those candidate gaps and
lets you fill only PDF-confirmed applications with hand-verified data,
**in small reviewable chunks**, without ever touching the canonical
database:

1. **Report** — run `scripts/report_coverage_gaps.py` against
   `data/partadex.db` to find make/model and make/model/engine year
   ranges and any missing internal years.
2. **Pick a chunk** — take one gap (or a related model-year block,
   e.g. "2011 Mazda 3, all filter categories") and research the exact,
   citable replacement data from a real source document (catalog PDF,
   OEM spec, etc.). Confirm that the model was actually produced in
   each proposed year. Do not fill legitimate production hiatuses,
   guess, or bulk-generate rows.
3. **Write a backfill CSV** — one row per exact
   year/make/model/engine/filter application, citing the source
   document and page.
4. **Validate + import** — run `scripts/backfill_import.py` to
   validate the CSV and import it into a **supplemental** SQLite
   database (never `data/partadex.db`). Validation is all-or-nothing
   by default, and re-running the same CSV is a no-op (idempotent).
5. **Repeat** for the next chunk. Because each step only ever adds a
   validated, source-cited row to a separate supplemental database,
   the whole process is reversible: delete the supplemental database
   file and you're back to exactly the canonical dataset, with zero
   risk of corrupting `data/partadex.db`.

### 1. Generate a coverage-gap report

```bash
# Human-readable report for the whole database, printed to stdout
python3 scripts/report_coverage_gaps.py

# Only show make/model/engine groupings that actually have gaps
python3 scripts/report_coverage_gaps.py --only-gaps

# Scope to one make (and optionally one model) while you work a chunk
python3 scripts/report_coverage_gaps.py --make TOYOTA --model CAMRY

# Machine-readable JSON, written to a file
python3 scripts/report_coverage_gaps.py --format json --only-gaps \
    --output coverage_gaps.json
```

The report groups vehicle rows two ways, because a model's engine
lineup usually changes mid-range and that can hide or manufacture
apparent gaps depending on which level you look at:

- **Model level** (`make` + `model`): the overall year range and any
  years in between where the model has *no* row at all, regardless of
  engine.
- **Engine level** (`make` + `model` + `engine`): the year range and
  internal gaps for one specific engine variant. A model can look
  fully covered at the model level while a specific engine still has
  a real hole in its own range (e.g. one generation's engine skips a
  facelift year that another engine covers instead).

The script connects to the database using SQLite's `mode=ro` URI flag
(`open_readonly()` in `scripts/report_coverage_gaps.py`), so an
attempted write raises an error instead of silently touching the file.

The first completed chunk is
`chunks/lincoln_mark_viii_1993_1998.csv`. Luber-Finer catalog pages
305–306 directly list the 1993–1998 Lincoln Mark VIII, 4.6L V8 VIN V,
with oil filter `PH820`. Those six verified rows are loaded into
`data/backfill_supplemental.db`; the Carbonaro lookup reads verified
supplemental entries before reporting that Partadex has no match.

### 2. Write a backfill CSV

Create a CSV with this exact header (column order doesn't matter):

```
year,make,model,engine,filter_category,brand,part_number,source_document,source_page,catalog_year,verification_status
```

- `year` — the vehicle model year being backfilled (required).
- `make`, `model` — vehicle identification (required).
- `engine` — engine description; may be blank for a model-level entry.
- `filter_category` — one of `oil`, `air`, `cabin` (required).
- `brand`, `part_number` — the filter being cited (required).
- `source_document`, `source_page` — where this exact row came from
  (`source_document` required; `source_page` optional).
- `catalog_year` — the model year *of the source catalog itself*, if
  different from the vehicle year (optional).
- `verification_status` — one of `verified`, `unverified`, `pending`;
  blank defaults to `unverified`.

### 3. Validate and import into a supplemental database

```bash
# Validate + import (all rows must pass, or nothing is written)
python3 scripts/backfill_import.py \
    --csv chunks/2011_mazda3.csv \
    --supplemental-db data/backfill_supplemental.db

# Preview what would happen without writing anything
python3 scripts/backfill_import.py \
    --csv chunks/2011_mazda3.csv \
    --supplemental-db data/backfill_supplemental.db \
    --dry-run

# Import only the rows that pass validation, and report the rest
python3 scripts/backfill_import.py \
    --csv chunks/2011_mazda3.csv \
    --supplemental-db data/backfill_supplemental.db \
    --allow-partial
```

`--supplemental-db` must point somewhere other than
`data/partadex.db` (or any path literally named `partadex.db`); the
script refuses to run otherwise. Rows are uniquely identified by
`(year, make, model, engine, filter_category, brand, part_number)`,
enforced with a `UNIQUE` constraint plus `INSERT OR IGNORE`, so
re-running the same CSV (or overlapping chunks) never creates
duplicate rows — only `created_at`/`updated_at` timestamps and the
first-write win.

### Running the tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The test suite covers gap detection (including cases where model-level
and engine-level gaps disagree), CSV validation rejection, idempotent
re-import, the canonical-database write guard, and asserts that
`data/partadex.db` is byte-for-byte unchanged after every test run.
