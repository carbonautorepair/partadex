# Partadex first release and next work

Public first release: https://partadex.enixone.chatgpt.site (September 4, 2026).

The initial product is a free, installable web app using the existing Sites project. No paid service, custom domain, native store account, app account, billing backend or database hosting is required. Source-controlled app code is maintained in the existing Partadex phone app repository; this repository remains the catalog and public issue tracker.

The release focuses on part-number lookup, direct catalog-row evidence, conditions, vehicle/engine selection, offline operation and useful correction reports. It contains 568 oil-filter numbers across seven brands and 11,518 normalized vehicle configurations after source review, including six verified older Luber-Finer records. Main extracted coverage remains 2004–2023; earlier coverage is sparse.

## Priorities after launch

1. Repair and source-verify the exact records held in the public app's quarantine. Preserve the canonical database; approved additions or repairs need a documented supplemental/override mechanism and regression cases.
2. Source-verify coverage gaps in small useful batches. The refreshed canonical report still has 176 model groups and 593 engine groups with internal candidate gaps. These are candidates, including possible production hiatuses, not missing fits to manufacture.
3. Complete the source-rights review described in SOURCE_REGISTER.md before commercial or broader data distribution.
4. Triage public correction reports and improve the free oil-filter lookup. Keep the app usable on phones and desktops with no required account.
5. Inspect the separate interchange branch for reusable air/cabin/brake work. Reconcile schemas and provenance before integrating it; keep categories out of the UI until their data and function are ready.
6. Reconsider native Android after actual PWA use. An app-store package and its current testing/compliance obligations are not initial launch dependencies.
7. Consider optional paid expansion only after free validation. Protected backend, authentication, payments, deletion, privacy and store obligations belong to that separately scoped stage. Never paywall the existing oil-filter lookup.

Operational progress is tracked under the existing Command Center Partadex project, with nine detailed work items. Avoid a second Partadex project or reviving the archived Quota Overflow duplicate.

Release source: app commit `0bd9e20b586387a7148713025f2b839d1c07d635`, Sites saved version 5. Release checks cover exporter fixtures, original-row matching, static artifacts, lint/typecheck, and ten desktop/phone browser cases including offline reload and navigation. The first public-host test exposed redirected cached pages; the fix preserves offline navigation on static hosts.
