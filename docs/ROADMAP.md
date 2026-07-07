# Partadex Roadmap

Goal: the most complete automotive parts interchange digital catalog, built
piecemeal so any partial state is still a usable index.

## Current state (2026-07)

| Category | Status | Data |
|---|---|---|
| Oil filters | complete 2004–2023, verified + backfilled vs source | `data/oilfilter.db` — ~11,500 vehicles, 48 makes (Microgard / Microgard Select / WIX / WIX XP / Mobil1 / K&N) |
| Engine air filters | **complete 2004–2023, all 48 makes** | `data/aircabin.db` — 13,124 vehicles, 753 air sets (Microgard / WIX / K&N) |
| Cabin air filters | **complete 2004–2023, all 48 makes** | `data/aircabin.db` — 523 cabin sets (Microgard / Microgard HEPA / WIX / K&N) |
| Brake pads | blocked on source material | see task notes: BrakeBest/Raybestos catalogs are network-blocked from this env; user to supply PDFs (Google Drive) or allowlist domains |
| Brake rotors | blocked on source material | same as brake pads |

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
