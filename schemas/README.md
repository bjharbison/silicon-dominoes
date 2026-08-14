# Silicon Dominoes — Contract Schemas
Build-order step 1 (ARCHITECTURE.md §14). These schemas are the test suite for everything downstream: pipeline step 6 validates every publication cycle against them, and a contract that fails does not publish.

## Files
`common.schema.json` holds the shared `$defs` (ISO3, layers, poles, tiers, citations with mandatory archived URLs, the evidenced-or-null P sub-indicator, the tier-composition object, the publication envelope with methodology/weight stamps). The four contract schemas — `countries.schema.json`, `edges.schema.json`, `events.schema.json`, `ledger.schema.json` — reference it. `changelog.md` is prose and has no schema. `validate.py` is the CI gate: JSON Schema validation (draft 2020-12) plus the cross-field checks schema can't express. `fixtures/` is a minimal internally-consistent publication cycle used for self-testing and as a worked example of every contract.

## Running validation
```
pip install "jsonschema>=4.18"
python validate.py fixtures            # self-test
python validate.py /data/2026-08-07    # validate a real cycle
```
Exit code 0 publishes; non-zero blocks. Wire this into the pipeline before the publish step and into CI on any schema change.

## What the schemas enforce mechanically
Corroboration for Tier ≥ 3 events (two S3+ sources, at least one S1/S2); no S5-only sourcing; reversal events naming target and reversed tier; analyst inference tagged with rationale; corrections referencing the corrected event; every P sub-indicator evidenced-with-refs or explicit-null-with-`opacity_reason`; `alignment_index` structurally flagged `derived: true`; insufficient-data countries as a distinct record shape that cannot carry scores; posture overrides requiring written rationale; mandatory tier composition on every published T; cascade edges structurally flagged `projection: true` with a mechanism string (pattern-rejecting "neighbors correlate" phrasings) and a mandatory ledger reference; predictions requiring dated resolution criteria; resolved predictions requiring outcome, Brier contribution, and resolution evidence; frozen test sets; model and analyst Class B forecasts as separate scored entries; archived snapshot URLs on every citation.

## What stays in the pipeline and review UI
Cross-field referential integrity and arithmetic live in `validate.py` (D-share sums, distribution sum-to-1, evidence refs resolving to real events, one bundle anchor per bundle, cascade→ledger linkage, the S4-cannot-corroborate independence rule). Beyond that, some rules are not validatable at publish time at all and belong to the review UI and scoring code: source *independence* judgment, the Goodhart 2-point cap, Tier-1/2 decay math, the ≥5-point-delta corroboration trigger, hedging-prior consistency, append-only immutability (DB triggers), and doc 02's regression tests (P–D correlation, off-cycle D/E variance).

## Versioning
`schema_version` in each artifact tracks these files, independently of `methodology_version`. Additive changes bump minor; breaking changes bump major and require the frontend and exports to be updated in the same release.
