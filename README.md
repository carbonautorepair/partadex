# partadex

Cross-brand automotive filter interchange catalogue. Maps part numbers across filter brands so you can find equivalent oil, air, and cabin filters for any vehicle.

## Data

### `data/oilfilter.db`

Production oil filter interchange database.

| Table | Rows | Description |
|---|---|---|
| `vehicles` | 11,453 | Year / make / model / engine → filter set |
| `filter_sets` | 328 | Part numbers across brands for each unique filter |

Brands covered: Microgard, Microgard Select, Wix, Wix XP, Mobil 1, K&N.

**Example query** — find oil filter equivalents for a 2006 Acura CSX:

```sql
SELECT v.make, v.year, v.model, v.engine,
       f.microgard, f.wix, f.mobil1, f.kn
FROM vehicles v
JOIN filter_sets f ON v.filter_set_id = f.id
WHERE v.make = 'ACURA' AND v.year = 2006 AND v.model = 'CSX';
```

### `data/filter_catalog_staging.db`

Staging database for air and cabin filter data extracted from `data/microgard.pdf`. Rows are parsed but need spot-checking before promotion to production.

| Table | Rows | Description |
|---|---|---|
| `extraction_runs` | 1 | Metadata for each PDF extraction run |
| `application_rows` | 1,265 | One row per vehicle / filter-category combination |
| `application_parts` | 8,848 | Individual part numbers per brand per application row |

Current coverage: Honda, Toyota, Lexus (air and cabin filters).

### `data/microgard.pdf`

Source Microgard filter catalog. Used as input to the PDF extraction pipeline that populates `filter_catalog_staging.db`.

## Status

- Oil filters: production-ready
- Air / cabin filters: extracted, needs spot-check review before promotion

## License

MIT — see [LICENSE](LICENSE).
