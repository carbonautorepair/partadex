# Partadex source register — September 4, 2026

## Software and catalog data are separate

`LICENSE` supplies the MIT terms for Partadex software. It is not evidence of a license from a catalog publisher. No third-party redistribution permission document was found in this repository during this audit. Public availability and a free price do not establish permission by themselves.

The first app renders factual part numbers, vehicle/engine labels, source references and necessary fitment conditions in its own interface. It does not ship the original catalog PDF, its page images, brand artwork or the raw databases. No conclusion that all catalog rights are cleared is made here. Before paid or broader catalog distribution, verify original acquisition terms and obtain any needed permission; record the grant and its scope here.

The U.S. Copyright Office distinguishes facts from their expression, and U.S. law separately addresses original compilation material. These general rules do not resolve a particular catalog license or contractual restriction:

- https://www.copyright.gov/help/faq/faq-protect.html
- https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title17-section103

## Source inventory

| Source | Evidence held | Status |
| --- | --- | --- |
| O’Reilly All-Makes Filter Catalog, June 2023, part 2023ORCAT | `data/microgard.pdf`, 922 PDF pages; oil section PDF 6–419; source-page IDs retained in `data/partadex.db` | Main catalog extraction. Original download location/terms are not recorded in the current checkout. Not every extracted row is individually verified. |
| Luber-Finer 2013 automotive applications | `chunks/lincoln_mark_viii_1993_1998.csv`, source pages 305–306; six verified supplemental records | The six records preserve the prior source verification. The PDF is not present in this checkout; a fresh independent reread was not claimed in this release. |
| Separate development branch `claude/partadex-interchange-catalog-tdgter` | Branch head 554a25e, including separate oil/air/cabin/brake files and extraction work | Future reuse candidate. Its “complete” claims have not been accepted as current release validation. It diverges from main; do not replace canonical data or merge blindly. |

The app's `docs/catalog-quarantine.json` contains canonical/PDF SHA-256 pins and exact held vehicle IDs. 126 original vehicle rows (578 oil-application rows) are excluded from public export: seven mixed-manufacturer boundaries, Mazda model-carry errors, and one incomplete Toyota record with mixed cartridge/spin-on options. These are exclusions of flawed extracted records, not claims that the source PDF is wrong. Source inputs remain unchanged.

The release also records original canonical row identity. Matching a normalized vehicle description alone must not create cross-references between separate source rows. Printed-number similarity, shared engines and transitive chains are not interchange evidence.

## Growth rule

Prefer a documented source record over an inferred equivalence. Preserve year, make, model, engine, part brand/number, source document/page and fitment conditions. Keep unresolved rows out of public results. Rights and source-quality review precede each new category; oil-filter lookup remains free.
