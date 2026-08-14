# Silicon Dominoes — Database Schema
Build-order step 2 (ARCHITECTURE.md §14). PostgreSQL 14+. Companion files: `schema.sql` (DDL) and `test_schema.sql` (guarantee tests).

## Quick start
With Docker, from this directory:
```
docker run --name sd-db -e POSTGRES_PASSWORD=dev -p 5432:5432 -d postgres:16
# wait ~5 seconds for startup, then:
psql postgresql://postgres:dev@localhost:5432/postgres -v ON_ERROR_STOP=1 -f schema.sql
psql postgresql://postgres:dev@localhost:5432/postgres -v ON_ERROR_STOP=1 -f test_schema.sql
```
The test script prints one `PASS` notice per guarantee and finishes with "All schema guarantee tests passed." It runs inside a transaction that rolls back, so it can be re-run any number of times and leaves the database unchanged. To reset entirely: `docker rm -f sd-db` and start over.

## What the schema enforces mechanically
Immutability is triggers, not convention: `events`, `event_sources`, `sources`, `raw_captures`, `exposure_edges`, `cascade_edges`, `country_scores`, `ledger`, `ledger_resolutions`, `test_sets`, `renewal_cliffs`, `exercise_observations`, `publications`, and `audit_log` all reject UPDATE, DELETE, and TRUNCATE. Corrections append (`correction: true` + `corrects_event_id`, both CHECK-enforced together). Ledger entries are never edited — resolution is a separate append-only row in `ledger_resolutions`, and supersession is a reference to a prior entry.

The corroboration rule runs at COMMIT via a deferred constraint trigger: every event needs at least one non-S5 source, and any Tier ≥ 3 event needs two S3-or-better sources including one S1/S2. An event whose sourcing doesn't clear the bar cannot exist in the table at all.

`country_scores` is temporal: each recompute inserts a row keyed by `(iso3, score_class, computed_at, methodology_version)`; the current published state is the `latest_country_scores` view (which excludes `shadow` backfill rows per ARCHITECTURE.md §8), and history is a query. Cascade edges require an existing ledger entry by foreign key and reject "neighbors correlate" mechanism strings by CHECK; `projection` is CHECK-pinned to true. Frozen renewal-cliff test sets cannot be modified after enumeration. The `sd_public_ro` role has SELECT only, and only on published surfaces — no raw archive, review queue, feed config, or audit log; it physically cannot write.

## What stays outside the schema, on purpose
Only `feeds`, `review_queue`, and `research_gaps` are mutable — candidates in review are not evidence yet, and operational config isn't a record. JSON Schema validation of the five contracts stays in `validate.py` (pipeline step 6). Source-independence judgment, the two-S4s-cannot-corroborate pairing rule, Goodhart caps, tier decay, hedging-prior checks, and the doc 02 regression tests belong to the review UI and scoring code, which don't exist yet — the schema is the floor those build on.

## Design notes
Event IDs are text (`ev-2026-0341` style) to match the publication contracts exactly. Nested structures that the contracts model as objects (P sub-indicators on edges, score payloads, Class B distributions) are `jsonb` — the JSON contracts are the canonical shapes and validate.py is their gate, so duplicating them as forty columns would create two sources of truth. `evidence_refs` arrays are not foreign keys (Postgres can't FK an array); referential integrity for those is a validate.py cross-field check. Full-text search over event summaries is a generated `tsvector` column with a GIN index, ready for the public API's `/search`. No PostGIS: the choropleth joins on ISO3 in the frontend.
