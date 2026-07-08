# Brake pads & rotors — plan (awaiting source material)

## Status / blocker

All web access from this environment is denied by the network policy
(every host 403s, including partsouq.com and the catalog sites), so brake
application data cannot be fetched from here. Working channel: the owner
drops catalog PDFs into Google Drive (as with `microgard.pdf`) or adds the
catalog domains to the environment allowlist.

**Wanted, in order of usefulness** (any one is enough to start):

1. O'Reilly **BrakeBest Select brake pads** application catalog and
   **BrakeBest Select drums & rotors** catalog
   (firstcallonlinecatalogs.com / oreillyautocatalogs.com flipbooks — a
   browser "print to PDF" of the flipbook works too).
2. Raybestos **Application Guide: Pads and Shoes / Drums and Rotors /
   Calipers** (brakepartsinc.com, direct PDF).
3. Any Wagner / Centric / Power Stop application guide PDF.

## Why pads interchange cleanly: FMSI numbers

Friction pads are standardized on **FMSI D-numbers** (e.g. `D1210`): every
brand's pad for a given position/vehicle carries the same D-number stem, so
one column of FMSI numbers gives cross-brand interchange for free
(BrakeBest, Wagner, Raybestos, Akebono … all publish it). Rotors have no
FMSI equivalent — interchange is per-brand part number matched on
application (and dimensions where the catalog lists them).

## Schema draft (mirrors the filter pattern)

```
brake_pad_sets(id, fmsi, position,           -- 'front'/'rear'
               brakebest, brakebest_select, wagner, raybestos, ...)
brake_rotor_sets(id, position, brakebest, raybestos, ...,
                 diameter_mm, thickness_mm)   -- if the catalog lists specs
vehicles(id, make, year, model, engine, eng_code, vin,
         front_pad_set_id, rear_pad_set_id,
         front_rotor_set_id, rear_rotor_set_id)
```

Staging first, same as filters: `application_rows` + `application_parts`
with page/y provenance, `position` on parts, validate a known-good make by
hand before bulk extraction, then promote. Reuse the geometric-extraction
approach from `scripts/extract_air_cabin.py` — brake application guides use
the same year/model/engine row grid with brand part columns.
