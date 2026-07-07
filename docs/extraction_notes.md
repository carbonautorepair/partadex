# microgard.pdf Air & Cabin Air extraction notes

Source: `data/microgard.pdf` (O'Reilly 2023 All-Makes Filter Catalog, 922
pages, page size 603x783pt). Use PyMuPDF (`fitz`).

## Section map (0-indexed fitz pages)

- Pages 2–4: footnote definitions (`FN #` → English/Spanish/French text).
  Parse the English text for a footnote lookup table.
- Pages 5–~410: oil filter applications (already in `data/oilfilter.db`).
- Pages 419–915: **Air & Cabin Air applications**, identified by the string
  `Air-Cabin Air Section` on each page. Make → 0-indexed page ranges:

```
ACURA 419-426, ALFA ROMEO 427-428, AUDI 429-449, BMW 450-484,
BUICK 485-492, CADILLAC 493-503, CHEVROLET 504-541, CHRYSLER 542-548,
DODGE (ALSO SEE RAM) 549-563, FIAT 564-566, FORD 567-598,
FREIGHTLINER 599-601, GENESIS 602-602, GMC LIGHT TRUCKS 603-623,
HONDA 624-636, HUMMER 637-637, HYUNDAI 638-650, INFINITI 651-659,
ISUZU 660-660, JAGUAR 661-668, JEEP 669-677, KIA 678-691,
LAND ROVER 692-698, LEXUS 699-717, LINCOLN 718-724, LOTUS 725-725,
MASERATI 726-726, MAZDA 727-734, MERCEDES BENZ 735-776,
MERCURY 777-779, MINI 780-785, MITSUBISHI 786-792,
MOBILITY VENTURES 793-793, NISSAN 794-811, PONTIAC 812-815,
PORSCHE 816-826, RAM 827-834, SAAB 835-836, SATURN 837-838,
SCION 839-841, SMART 842-842, SUBARU 843-853, SUZUKI 854-855,
TESLA 856-857, TOYOTA 858-888, VOLKSWAGEN 889-902, VOLVO 903-914,
VPG 915-915
```

Note: a make's first page may start mid-page after the previous make ends —
detect the in-body make header (`MAKE` or `MAKE (Cont'd/Suite)` line) rather
than trusting page boundaries blindly.

## Page geometry (from page 858, stable template — re-derive per page from
the header words at y≈80 if anchors drift)

- Sidebar letters at x<40 ("ALL MAKES AIR CABIN AIR SECTION" vertically):
  ignore everything x<40.
- Running head at y<50; column header block ends ~y<88 (header words
  `Microgard`,`WIX`,`K&N`,`Microgard`,`Wix`,`K&N` sit at y≈80). Footer
  ("See pages 2-4 for footnotes", page number) near bottom: ignore.
- Left block x≈65–190:
  - **Model** lines: font `Arial-Black`, size 9, x≈71.3.
  - **Engine** lines: font `ArialNarrow`, x≈73.3 (may wrap to a continuation
    line, e.g. `Electric/Gas`, which appends to the same row's engine text).
  - **Engine code** column: x≈147–190 (e.g. `FA24D`, `G16E-GTS`).
  - **Year** markers: 4-digit (`2023`), centered around x≈100–140, possibly
    followed by `(Cont'd/Suite)`. Year state carries across pages.
- **VIN** column: x≈205–240 (single letters/digits).
- Part columns (values left-aligned at stable x):
  | x start | source_column | brand | typical pattern |
  |---|---|---|---|
  | ≈244 | microgard_engine_air | MICROGARD | `MGA\d+` |
  | ≈294 | wix_engine_air | WIX | `WA\d+` or `\d+` |
  | ≈343 | kn_engine_air | K&N | `33-\d+`, `E-\d+` |
  | ≈393 | microgard_cabin_air | MICROGARD (or MICROGARD HEPA when the number ends in `HP`) | `\d{4,5}(HP)?` |
  | ≈442 | wix_cabin_air | WIX | `WP\d+` or `24\d{3}` |
  | ≈492 | kn_cabin_air | K&N | `VF\d+` |

  The `kn_cabin_air` column was called `right_cabin_air_column` /
  `UNKNOWN_CABIN_COLUMN` in run 1; `VF####` are K&N cabin filters — new runs
  should use `kn_cabin_air` / `K&N`.

## Row model

- A new application row is anchored per engine line; the current Arial-Black
  line above it is the model; wrapped engine text joins with a space.
- Part numbers stack vertically below their first line until the next row
  anchor; collect all part words in each column x-bin between this anchor's y
  and the next anchor's y, **dedupe** per (column, part).
- Some rows repeat the same model with different engine codes → separate rows.
- `N/A` / `N/R` mean no filter offered — skip as parts.

## Footnotes

Numeric footnote ids are typeset as superscripts glued to the part number in
word extraction, e.g. `WP1032079` = part `WP10320` + footnote `79`,
`4915061` = `49150` + footnote `61`. In `get_text("dict")` the superscript is
a separate span (smaller size / superscript flag) — use span-level extraction
to split reliably rather than guessing from digits. Store: `raw_part_number`
(glued), `normalized_part_number` (clean), `footnote`.
Footnotes can also attach to the model/engine (row_footnote).

## Staging DB conventions (`data/filter_catalog_staging.db`)

- One `extraction_runs` row per script run; `application_rows.source_page` is
  **1-indexed** (fitz index + 1); `source_y` = row anchor y.
- Run 1 (2026-05-22) covers HONDA (pages 624–637 1-indexed), LEXUS (700–718),
  TOYOTA (859–889): 1,265 rows / 8,848 parts, all
  `confidence='parsed'`, rows `status='needs_spot_check'`, parts `status='ok'`.
- **Validation harness**: re-extract those three makes and diff against run 1
  on (make, year, model, engine, engine_code) rows and their part sets
  (treat run-1 `UNKNOWN_CABIN_COLUMN` as `K&N`/`kn_cabin_air`). Target ≥99%
  agreement; investigate every diff in the PDF — some may be run-1 bugs, which
  is fine if documented.

## Validation result (2026-07-07, scripts/validate_air_cabin.py)

99.84% of run-1 rows and 98.02% of their part sets reproduced exactly. Every
residual diff was checked against the PDF; all favor the new extractor:

- Make sections start **mid-page**: the `MAKE_PAGES` ranges above are
  top-of-page running heads, so scan from `first - 1` and attribute by the
  in-body make headers. Run 1 missed the first block of each section this way
  (e.g. LEXUS 2023 ES 250–LC 500, TOYOTA 2023 4 Runner–Corolla Cross).
- Run 1 dropped part lines near page bottoms (y ≳ 705) and rows continuing
  across page breaks; keep rows open until the next anchor.
- Run 1 glued superscript footnotes into part numbers (`3041175`,
  `WP10369322`, `WA10408541`) and made a part out of `N/A` + footnote
  (`NA1`); split via superscript spans and skip `N/`-prefixed tokens.
- Run 1 truncated the two-line model "Clarity Plug-in Hybrid" to
  "Clarity Plug-in".
- Row-split rule (reproduces run 1 exactly elsewhere): a left-column line
  with parts on its own line is a NEW application unless its text is a pure
  engine modifier (`Electric/Gas` etc., see `WRAP_VOCAB`); part-less left
  lines are wrapped engine text. MIRAI's two lines are genuinely separate
  applications (different cabin filters per line).
- New runs also capture the VIN column (`application_rows.vin`, added by the
  extractor via ALTER TABLE) and clone shared engine text onto
  code-variant rows instead of leaving engine empty.
