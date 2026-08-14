# Silicon Dominoes — System Architecture
Version: 1.0 · Status: Draft for review · Companion to: SYSTEM_PROMPT.md, methodology docs 01–04

## 1. Design principles

The architecture follows directly from the publication model in system-prompt §5. Because the public product is five JSON contracts emitted on a weekly batch cycle, the reader-facing site is a static frontend over versioned data artifacts — no live queries are required to render the map. All operational complexity (collection, coding, review, scoring, immutability enforcement) lives in a private pipeline behind that boundary.

Three invariants shape every component below:

1. **Immutability.** Published scores are never edited (§2.3 "no single-run rewrites"). The database is append-only for anything that has been published; corrections are new rows flagged `correction: true`. This is what makes score history free.
2. **Traceability.** Every number decomposes to dated, sourced, coded events (§0). Every citation must survive link rot, so the system archives every source URL at capture time.
3. **Cadence separation.** Capture is continuous; coding, scoring, and publication are weekly; each score class recomputes only on its own cadence (§4), with Tier-4/5 events and reversals triggering out-of-cycle recomputes.

## 2. Component overview

```
 ┌────────────────────────────  PRIVATE  ────────────────────────────┐
 │                                                                   │
 │  Collection workers ──► Raw archive ──► Weekly pipeline ──► DB    │
 │        │                (S3 + URL          │      ▲        │      │
 │        ▼                 snapshots)        ▼      │        ▼      │
 │  Feed health monitor              Review/Coding UI      Publisher │
 │        │                          (admin app, auth)        │      │
 │        ▼                                                   ▼      │
 │     Alerts                                     Versioned artifacts│
 └────────────────────────────────────────────────────────────│──────┘
                                                              ▼
 ┌────────────────────────────  PUBLIC  ─────────────────────────────┐
 │  Static frontend (map + charts)  ◄──  CDN  ◄──  JSON contracts    │
 │  Read-only API (drill-down, history, search)  ◄──  DB (read)      │
 │  Export endpoints (JSON/CSV) · Report generator (PDF)             │
 └───────────────────────────────────────────────────────────────────┘
```

## 3. Collection layer

Collection runs continuously and independently of the weekly cycle, per system-prompt §3.1: polling is cheap and events keep their true dates.

**Workers.** Python scripts orchestrated by a scheduler. Two viable deployments: (a) a small always-on host running Celery beat or systemd timers, or (b) serverless via GitHub Actions scheduled workflows if the project should start with zero managed servers. Each feed class from §3.1 gets its own worker module: RSS/Atom pollers, GDELT DOC 2.0 Boolean queries built from the versioned facet files (§3.2 — synonym lists are loaded from the facet files at runtime, never hard-coded), structured data pulls (Comtrade HS codes, TED/UNGM tenders, IEA/EMBER, cable databases, filings), and named-entity watchlist monitors including the sub-state entity watch.

**Raw archive.** Every captured payload is written verbatim to object storage (S3 or Backblaze B2) under a content-addressed key, with a `raw_captures` index row in Postgres recording `feed_id`, `retrieved_at`, `url`, `sha256`, and parse status. Nothing is ever deleted from the archive; it is the ground truth the coding step and any future backfill reprocess from.

**URL snapshotting.** At capture time, every source URL is submitted to a self-hosted ArchiveBox instance (with the Wayback Machine Save API as a secondary). The snapshot ID is stored alongside the capture. This is a hard requirement, not an optimization: the traceability guarantee in §0 fails silently as sources rot, and rot rates for regional press and tender portals are high. Citations in every published contract carry both the live URL and the archived snapshot reference.

## 4. Feed health monitoring

A dead feed silently starves a country of evidence and biases scores toward zero movement, so feed health is a first-class subsystem, not an ops afterthought.

The monitor maintains, per feed, a rolling baseline of capture rate (items/day) and computes deviation each day. Alerts fire on: capture rate below a configured fraction of baseline for N days; HTTP or parse failures above threshold; a feed returning success but zero new items beyond its historical maximum gap; and staleness of "verify at every run" items (WAICO and Pax Silica membership checks must show a successful verification within the last cycle — a missed verification is itself an alert).

Feed health also feeds the analytic layer: per-country **coverage metrics** (evidence_count trends, null_coverage_rate from doc 01 §2.3) are published in `countries.json`, and the weekly changelog notes any country whose evidence flow dropped materially — so readers can distinguish "nothing happened" from "we stopped hearing." Alert delivery is email plus a status panel in the admin app. A dead feed opens a `research_gap` record automatically.

## 5. Database

PostgreSQL, one instance, two logical roles (read-write for pipeline/admin, read-only for the public API).

Core tables mirror the spec's schemas: `events` (§3.3 fields, including `bundle_id`, `dyad`, `sub_state_actor`, announced vs. verified values), `edges` (dyadic exposure edges per doc 01 §3.1, cascade edges with mechanism strings and ledger references), `country_scores`, `ledger` (doc 04 entry schema), `sources` (with source-tier, snapshot refs), `research_gaps`, `raw_captures`, `feeds`, and `review_queue`.

**Immutability is enforced in the schema, not by convention.** Published rows are guarded by triggers that reject `UPDATE` and `DELETE`; corrections insert new rows referencing the corrected `event_id`. `country_scores` is a temporal table: every recompute inserts a new row keyed by `(iso3, score_class, computed_at, methodology_version, weight_vector_stamp)` — the current published state is simply the latest row per key, and history is a query, not a feature to build later. Ledger entries are append-only with supersession references per doc 04.

PostGIS is unnecessary at this stage; the choropleth joins on ISO3 against a static boundary file shipped with the frontend.

## 6. Coding and review UI (internal admin app)

This is the second product, and it is budgeted as such: expect it to consume as much build effort as the public site, because it is where the evidence standards of §2 are actually enforced.

**Flow.** The weekly pipeline dedupes captures and runs LLM-assisted extraction to produce *candidate* events — pre-filled with proposed `country_iso3`, `layer`, `instrument_tier`, `source_tier`, `direction`, `depth_dimensions`, and extracted values. Candidates land in a review queue. **Nothing scores without human approval.** The reviewer screen shows the candidate beside the archived source snapshot, with one-click access to the corroboration status.

**Guardrails built into the form, not the training manual.** The UI mechanically enforces: the corroboration rule (a Tier ≥ 3 event or ≥ 5-point delta cannot be approved without two independent S3+ sources including one S1/S2, and two S4 state announcements cannot corroborate each other); the S4/S5 handling rules; mandatory `opacity_reason` when a P sub-indicator is coded null; mandatory mechanism strings on cascade edges with rejection of "neighbors correlate" phrasings (doc 04); `analyst_inference` tagging rendered as a distinct field; `bundle_id` linkage prompts when an event resembles a package; and `posture_override` requiring a written rationale (doc 01 §4.3). Definition conflicts per §1 get a "flag to changelog" action.

**Other admin surfaces.** Ledger management (create/resolve predictions against the doc 04 schema, with resolution requiring the S1/two-source criterion), the quarterly renewal-cliff enumeration workflow (frozen test sets, doc 04 Class B), feed health dashboard, research-gap triage, and the changelog editor.

**Stack.** Same FastAPI backend as the public API but on authenticated routes; frontend either React (shared components with the public site) or, pragmatically for v1, an admin framework like Django admin or Retool to ship faster. Recommendation: start with the pragmatic option; the review queue's ergonomics matter more than its polish.

## 7. Weekly pipeline

A single orchestrated batch job (Prefect or plain Python with explicit stages), run weekly plus on-demand for Tier-4/5 out-of-cycle triggers:

1. **Ingest & dedupe** the week's captures; emit candidate events to the review queue (continuously through the week, so reviewers aren't slammed on publication day).
2. **Gate:** only human-approved events proceed.
3. **Recompute by cadence class** (§4): T weekly; D/E only in quarterly windows or on Tier-4/5 triggers; P on evidence; H/salience semi-annually; then derived network scores, posture clustering (versioned cluster→label mapping), contest scores, and cascade susceptibility — all in the same run so every publication is internally consistent.
4. **Apply guards:** instrument-ladder weights with Tier-1/2 decay, the 2-point Goodhart cap, hedging-prior checks on new cascade predictions.
5. **Resolve ledger entries** whose resolution dates have passed; recompute Brier scores and baseline comparisons.
6. **Validate** every output against the JSON Schema files for the five contracts. Schema validation is CI: a contract that fails schema does not publish. (These schemas are listed as still-to-build in the README; they should be written before the pipeline, because they double as its test suite.)
7. **Publish:** write the five artifacts to versioned, dated paths (`/data/2026-08-07/countries.json` … plus `/data/latest/` aliases), stamped with `methodology_version` and the weight vector; render `changelog.md`; invalidate the CDN.
8. **Regression checks** from doc 02 run automatically each cycle: P–D correlation across countries, and week-over-week D/E variance outside quarterly windows (should be ~zero). Violations alert and appear in the changelog.

## 8. Backfill and reprocessing (methodology version changes)

The rule from doc 01 — a methodology change never retroactively alters published scores — is implemented as follows:

Published artifacts and published `country_scores` rows are frozen forever; each carries the `methodology_version` and `weight_vector_stamp` it was computed under. When the methodology increments (say v2.0 → v2.1), the pipeline may **recompute forward** from the immutable raw archive and approved-events table under the new version, writing new rows tagged v2.1 — the old v2.0 rows remain queryable and the artifacts they produced remain on disk.

If a maintainer wants to know what history *would have looked like* under the new version (useful for validating a change), the pipeline supports a **shadow backfill**: replaying approved events through the new scoring code into rows flagged `shadow: true`, never published as the record, but comparable side-by-side in the admin app. The public time-series API always serves the scores as originally published, with the version stamp visible, so a discontinuity at a version boundary is disclosed rather than smoothed. Version changes, per doc 01, are announced in the changelog; the ledger is unaffected because entries are append-only and score under the version that created them.

## 9. Public API

FastAPI, read-only, backed by the read replica role. It exists for what static files can't do:

`GET /countries/{iso3}` (current record) · `GET /countries/{iso3}/history?class=T&from=&to=` (time series from the temporal table, with version stamps per point) · `GET /countries/{iso3}/events` and `GET /events/{event_id}` (drill-down: score → citing events → sources → archived snapshots) · `GET /edges?country=&controller=` (exposure decomposition — doc 02 regression test 2 requires one-tap access from any `alignment_index` to this) · `GET /ledger` and `GET /ledger/brier` · `GET /search?q=` (full-text over events and sources) · `GET /exports/...` (see §11).

Responses embed citation objects everywhere a number appears. Rate limiting and caching at the CDN; no auth on any of these routes.

## 10. Public frontend

React + MapLibre GL for the choropleth, Recharts or Observable Plot for charts, statically hosted (Cloudflare Pages / Netlify) and reading `/data/latest/` contracts directly, falling back to the API only for drill-downs and history.

The UI requirements from §6 of the system prompt and doc 02's regression tests are treated as acceptance criteria: layer order (alignment → contest → exposure asymmetry → readiness → cascade overlay off-by-default and watermarked as projection); the two-axis US×PRC exposure view as a first-class layer; `hedged_active` and `inert` never sharing a color; contested vs. committed visually distinct; "insufficient data" rendered as its own state, never a neutral midpoint; Brier scores rendered adjacent to the cascade toggle; tier-composition visible on every Trajectory display; parameter sliders; and the DIMEFIL reporting view generated at render time from DEPTH-N with the lossy-projection watermark and divergence flags (doc 03).

Every score element is clickable through to its evidence trail: score → events → source (live + archived). Score history renders as sparkline/time-series panels per country, with methodology-version boundaries marked on the axis.

## 11. Exports and report generation

**Data export.** The five contracts are themselves the canonical export, downloadable per dated cycle or as `latest`. The API additionally serves CSV flattenings of `countries.json`, `events.json`, and `edges.json`. Every export embeds `methodology_version`, `weight_vector_stamp`, generation timestamp, and a citation manifest. Bulk historical export ships as a per-cycle archive.

**Reports.** A server-side generator (Jinja2 templates → WeasyPrint PDF) produces: the weekly changelog brief; per-country deep-dive reports (full DEPTH-N record, evidence list with archived links, history charts); and the DIMEFIL briefing export, watermarked per doc 03 with expandable DEPTH-N sourcing bundled as an appendix. Report generation is on-demand via the API and cached.

## 12. Authentication and security

The public side is fully anonymous — no accounts, no tracking beyond standard CDN logs. The admin app and all write paths sit behind authentication: an identity provider (Auth0, or self-hosted Keycloak/Authelia) with mandatory 2FA, and role separation between *reviewer* (approve/edit candidate events), *maintainer* (methodology versions, weights, cluster mappings, ledger administration), and *operator* (feeds, infrastructure). Every approval and override is written to an audit log with the acting user — this is the human half of the traceability guarantee. The database is not internet-exposed; the pipeline and admin app reach it over a private network; the public API uses a read-only role that physically cannot write.

## 13. Deployment and operations

A deliberately small footprint: one VM or small managed container service runs the API, admin app, pipeline scheduler, and ArchiveBox; managed Postgres (with PITR backups); object storage for the raw archive and published artifacts; CDN in front of both the static site and the API. Infrastructure as code (Terraform or a docker-compose that is honest about being production) so the whole system is rebuildable. Backups: continuous for Postgres, versioned object storage for the archive — and because the archive plus approved events can regenerate every score, the raw archive is the asset to protect hardest.

## 14. Build order

1. JSON Schemas for the five contracts (they are the test suite for everything downstream).
2. Postgres schema with immutability triggers and the temporal `country_scores` design.
3. Collection workers + raw archive + URL snapshotting + feed health monitor for a pilot feed set.
4. Review UI (pragmatic v1) and the candidate-event extraction step.
5. Scoring pipeline with cadence classes, guards, and regression checks; publication of the five artifacts.
6. Public frontend: map, layers, drill-down, history.
7. Public API surface, exports, report generator.
8. Backfill/shadow-replay tooling (needed by the first methodology revision, not by launch).

## 15. Open decisions

Clustering algorithm choice for posture assignment (doc 01 §4.3 versions the mapping but leaves the algorithm open); GDELT vs. licensed aggregator for structured news retrieval; admin framework (custom React vs. Django admin/Retool for v1); serverless vs. always-on collection workers; and whether the LLM-assisted extraction runs on a hosted API or locally — a cost/volume question to resolve after the pilot feed set produces real numbers.
