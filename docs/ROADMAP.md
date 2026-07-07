# Partadex Roadmap

Goal: the most complete automotive parts interchange digital catalog, built
piecemeal so any partial state is still a usable index.

## Current state (2026-07)

| Category | Status | Data |
|---|---|---|
| Oil filters | ~complete 2004–2023 | `data/oilfilter.db` — 11,453 vehicles, 328 filter sets (Microgard / Microgard Select / WIX / WIX XP / Mobil1 / K&N) |
| Engine air filters | Toyota/Lexus/Honda staged | `data/filter_catalog_staging.db` run 1 |
| Cabin air filters | Toyota/Lexus/Honda staged | `data/filter_catalog_staging.db` run 1 |
| Brake pads | not started | — |
| Brake rotors | not started | — |

Primary source so far: `data/microgard.pdf` — O'Reilly 2023 All-Makes Filter
Catalog, 922 pages. Oil filter applications on pages 5–~410; **Air & Cabin Air
applications for 48 makes on pages 420–916 (1-indexed)** — this is the source
to finish air/cabin coverage before any external scraping.

## Phases

1. **Air + cabin filters (in progress)** — extract the remaining 45 makes of
   the PDF air/cabin section (geometric text extraction, see
   `docs/extraction_notes.md`), validate against the already-staged
   Toyota/Lexus/Honda rows, then promote staging → final queryable DB.
2. **Oil filter cleanup** — 3 corrupt `filter_sets` rows (ids 5/161/274 hold
   make-header text), 8 all-null sets, spot-audit.
3. **Brake pads & rotors** — needs an application/interchange source
   (brand catalog PDFs e.g. Wagner/Raybestos, or partsouq.com OEM data).
   Same staging → validate → promote pipeline, one make at a time.
4. **Lesser-used maintenance parts** — wipers, spark plugs, belts, fuel
   filters, transmission filters…

## Working principles

- Piecemeal: commit + push after every validated batch so progress is durable.
- Deterministic scripts over manual data entry; validation against known-good
  subsets before bulk runs.
- Delegate bulk/mechanical work to cheaper agents; reserve high-effort
  reasoning for planning, schema design, and validation review.
