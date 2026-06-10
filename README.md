# partadex

Cross-brand automotive filter interchange catalogue (oil, engine air, cabin air) with a reproducible PDF extraction pipeline.

## Data

### data/oilfilter.db

Production oil filter interchange database. Contains 12,834 vehicles (2004–2023) across 55 makes, with 228 deduplicated filter sets. Brands covered: Microgard, Microgard Select, Wix, Wix XP, Mobil 1, and K&N.

**Schema:**
- `vehicles`: id, make, year, model, engine, eng_code, row_footnote, filter_set_id
- `filter_sets`: id, microgard, microgard_select, wix, wix_xp, mobil1, kn, qualifier
- `footnotes`: code, text (for row-level qualifiers)

**Example query** — find all oil filters for a 2015 Honda Civic:
```sql
SELECT f.* FROM filter_sets f
  JOIN vehicles v ON v.filter_set_id = f.id
  WHERE v.make = 'HONDA' AND v.year = 2015 AND v.model = 'CIVIC';
```

### data/aircabin.db

Production engine-air and cabin-air interchange database. Contains 12,854 vehicles (2004–2023) across 54 makes. Engine air brands: Microgard, Wix, K&N. Cabin brands: Microgard, Microgard HEPA, Wix, K&N.

**Schema:**
- `vehicles`: id, make, year, model, engine, eng_code, row_footnote, air_filter_set_id, cabin_filter_set_id
- `air_filter_sets`: id, microgard, wix, kn
- `cabin_filter_sets`: id, microgard, microgard_hepa, wix, kn
- `footnotes`: code, text

**Example query** — find both engine and cabin air filters for a 2018 Toyota Camry:
```sql
SELECT a.*, c.* FROM air_filter_sets a
  JOIN vehicles v ON v.air_filter_set_id = a.id
  JOIN cabin_filter_sets c ON v.cabin_filter_set_id = c.id
  WHERE v.make = 'TOYOTA' AND v.year = 2018 AND v.model = 'CAMRY';
```

### data/oilfilter_staging.db

Raw extraction staging database for oil filters with full provenance: source page, y-position, raw row text, and per-part footnote references.

### data/aircabin_staging.db

Raw extraction staging database for engine-air and cabin-air filters with full provenance: source page, y-position, raw row text, and per-part footnote references.

### data/microgard.pdf

Source catalog (922 pages):
- Oil section: pages 6–419
- Engine-air & cabin-air section: pages 420–916

## Pipeline

`tools/catalog_extract.py` — Generic extractor for the Microgard catalog PDF. Parses pdftohtml -xml output, separates footnote superscripts from part numbers by font size, and derives column bands per page.

**Usage:**
```bash
python3 tools/catalog_extract.py --section oil --pages 6-419 --db data/oilfilter_staging.db
python3 tools/catalog_extract.py --section aircabin --pages 420-916 --db data/aircabin_staging.db
```

## License

MIT — see [LICENSE](LICENSE).
