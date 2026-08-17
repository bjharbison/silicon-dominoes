# CLAUDE.md — Working agreement for agentic work in this repo

Silicon Dominoes is an open-source-intelligence dashboard measuring which AI technology
stack each country is embedded in. **The entire product claim is traceability**: every
number on the map decomposes to a dated, sourced, coded event. Code that runs but breaks
traceability is worse than code that does not run, because the failure is silent and the
published output still looks correct.

Read this file before any task. Where it conflicts with something you infer from the
codebase, this file wins. Where it conflicts with `SYSTEM_PROMPT.md` or the methodology
docs in `project-knowledge/`, those win — tell me about the conflict rather than resolving
it yourself.

---

## 1. Hard prohibitions

These are not style preferences. Each one is load-bearing for a guarantee the project has
already committed to publicly.

**Never write or apply DDL.** All 18 tables are owned by `postgres`; the service user
`dominoes` holds DML only. This is deliberate — it makes accidental or automated migrations
impossible. Schema changes are written as numbered `.sql` files in `collection/sql/` and
applied by hand. Do not run them. Do not add a migration runner. Do not "helpfully" apply
one to check that it works.

**Never invent a value.** Missing data is `null`. Not zero, not a midpoint, not a plausible
estimate. Modelled values live only in `_est` fields and carry a documented method. If a
Provenance sub-indicator is unknown it is null **with an `opacity_reason`** — never proxied
by vendor nationality, which is Dependence and recreates the exact collinearity the v2
methodology exists to remove.

**Never impute to make a test pass.** If a fixture fails because data is missing, the
fixture is telling the truth. Fix the code or report the gap; do not fill the hole.

**Never sum chokepoint and panopticon P sub-indicators.** `p_license`, `p_update`,
`p_spares` (chokepoint) and `p_jurisdiction`, `p_telemetry` (panopticon) are published
separately, always. The ability to switch a thing off and the ability to watch it are
different powers. There is no holistic P score anywhere in this system.

**Never average across the stock/flow boundary.** D and E describe what is built; T
describes what is being decided. They recompute on different cadences and are never
combined into a single "movement" number.

**`alignment_index` is derived and display-only.** It is `f(exposure_us − exposure_prc)`,
flagged `derived: true` in every contract, and is never an input to anything. Any UI showing
it must offer the two-axis exposure decomposition one tap away.

**`source_tier` is never inferred by a model.** It is a property of the feed, recorded in
`collection/feeds.yaml`. If you find code letting an LLM assign it, that is a bug.

**Never edit or delete a published row.** `raw_captures`, `events`, and ledger entries are
append-only, enforced by triggers. Corrections insert new rows referencing the corrected
`event_id` with `correction: true`. If a delete looks necessary, stop and ask.

**Never hard-code facet synonyms inline.** Actor, instrument, domain, and geo terms live in
versioned files under `collection/facets/`. Adding a term means editing YAML and bumping the
facets version.

**Anything carrying `synthetic: true` or `provisional: true` must fail validation** with its
own named error message. The demo datasets are unpublishable by design and that property is
a test, not an accident of strict schemas.

---

## 2. Scoring rules that constrain implementation

**Instrument ladder weights:** T1 0.15, T2 0.35, T3 0.70, T4 0.85, T5 1.00. Reversals score
at ×(−1) of the tier reversed. Tier-1/2 events decay as `weight · exp(−λ·age)`, λ set so a
Tier-1 retains ~50% at 12 months and ~0 at 24. Tier-4/5 never decay; they retire only by
explicit reversal.

**Goodhart cap:** Tier-1/2 events can never move any published score by more than 2 points
in aggregate, regardless of volume. Every Trajectory display shows its tier composition.

**Corroboration rule:** any Tier ≥ 3 event, or any score change ≥ 5 points, requires two
independent S3+ sources including at least one S1/S2. Two S4 state announcements cannot
corroborate each other. This is enforced mechanically in the review UI and in schema checks
— never as a reviewer instruction.

**Cadence:** T weekly. D/E quarterly, or on any Tier-4/5 event or reversal. P on evidence.
H and salience semi-annually. Derived network scores in the same run as T so every
publication is internally consistent. Do not manufacture weekly movement in slow variables.

**Confidence bands propagate.** Every derived score carries a band computed from its edges'
source-tier mix and null rates. A score without a band is incomplete.

---

## 3. Frontend constraints

**`map.html` is a single file with no build step.** No bundler, no npm, no external module
imports. This is a hard architectural constraint, not a preference. All edits must preserve
it.

**No browser storage APIs beyond what already exists.** The current localStorage use is the
mode toggle only.

**Simple mode contains no hand-written prose, ever.** All Simple-mode sentences are composed
mechanically by `simpleSummary()` from the embedded `SIMPLE_LEXICON` object plus record
fields, then escaped. The lexicon is the operative version; the markdown in
`project-knowledge/` is documentation. A wording change edits the code, bumps the lexicon
version field, and updates the markdown in the same commit. **If the two disagree, the code
is truth.** Never add free text to Simple mode — future context either extends the lexicon
mechanically or goes in Advanced mode tagged `analyst_inference`.

**Display rules that must survive every change:** `hedged_active` and `inert` never share a
colour. Insufficient-data renders hatched, never as a neutral midpoint. Contested
(high-alignment/low-lock-in) is visually distinct from committed. Cascade and projection
layers are toggleable overlays, off by default, labelled, with confidence bands, never
co-rendered with observations. Methodology version and weight-vector stamp appear in the
header.

---

## 4. Repository layout and ownership

The Windows checkout is the **sole author** of this repo. CT 109 (`dominoes` at
192.168.1.204) is a deploy target that only ever runs `git pull`. Do not write code intended
to be edited inside the container — that pattern already cost this project two sessions of
uncommitted work.

```
map.html                    # single-file dashboard, no build step
schemas/                    # JSON Schemas — the test suite for everything downstream
  validate.py               # CI gate: schema + cross-field checks
  fixtures/                 # must-pass
  fixtures/must-reject/     # must-FAIL; a file here that validates is a test failure
collection/
  collector/                # pollers, extractor
  facets/                   # versioned synonym lists
  feeds.yaml                # feed registry incl. source_tier
  sql/                      # numbered migrations, applied MANUALLY
project-knowledge/          # methodology docs 01–07
SYSTEM_PROMPT.md
ARCHITECTURE.md
STATUS.md                   # running record — update at end of every session
```

---

## 5. Verification

Nothing is done because it looks done. Every task ends with a command whose output proves it.

```bash
# Contract validation (from repo root, in the validate venv)
python schemas/validate.py fixtures        # → OK — all contracts valid
python schemas/validate.py --self-test     # → every must-reject file fails, by name

# Dashboard syntax
node --check map.html                      # after extracting the script block

# In CT 109 — pipeline venv is INSIDE collection/
cd /opt/silicon-dominoes/collection && .venv/bin/python -m collector.extract --dry-run

# Schema guarantees (manual, after any migration)
# T1–T11 must still pass; test transaction rolls back cleanly
```

Prefer Python string-splice edits with explicit anchor assertions over line-number edits
when touching `map.html`. Prototype any deterministic rendering logic against the real
embedded data before writing the JS — wording review should happen on exact final output.

End every session with `git status --short`. An uncommitted working tree is the single point
of failure this project has already hit.

---

## 6. When to stop and ask

Stop rather than proceed if:

- A task appears to require DDL, a delete, or an edit to an immutable row.
- A test can only be made to pass by filling in a value that is not sourced.
- A change would make `map.html` need a build step.
- A methodology doc and the code disagree about a definition. Flag it for the changelog;
  never silently redefine a term.
- You are about to assign a score, posture, exposure value, or D/E figure yourself. **You do
  not code events and you do not produce analytic values.** That work goes through the
  review queue with the corroboration rule enforced. Your job is the machinery, not the
  judgment.
- Scope is expanding into work the plan deferred: public API, CSV exports, PDF report
  generator, the DIMEFIL view and divergence flags, backfill/shadow-replay, the Class B
  formula, the facility layer, or moving the raw archive to the NAS. All post-launch.

---

## 7. Known traps

- Archive objects exist as `.html`, `.html.gz`, and `.json`. Pre-2026-08-16 objects are
  uncompressed and are not being rewritten. Every reader handles all three.
- `raw_captures` contains permanent historical duplicates — the immutability trigger blocks
  cleanup and that is the correct trade. Select with
  `DISTINCT ON (feed_id, url) ... ORDER BY feed_id, url, capture_id` (earliest wins).
- The URL index `idx_raw_captures_url` is deliberately **non-unique**; uniqueness is enforced
  in the poller, not the schema, because historical duplicates cannot be deleted.
- Prefilter matching is word-boundary and case-sensitive for short all-caps terms. Substring
  matching produced real false positives: "Digi" in "digital", "Intel" in "intelligence",
  "GIC" in "strategic".
- `trafilatura` is imported inside a bare `except Exception` returning `None`, so a missing
  install presents as every capture being unreadable rather than as an ImportError.
- Two dedup strategies by design: news feeds dedupe on **URL**; verify items (WAICO, Pax
  Silica membership) dedupe on **content hash**, because a changed page is precisely the
  signal. They never share a code path.
- GitHub Pages lags commits by up to ~10 minutes. Hard-refresh or append `?v=N` before
  troubleshooting a stale deploy.

---

## 8. Style

Decisions get documented with their rationale, not just their outcome — in commit messages,
in `STATUS.md`, and in comments where a future maintainer would otherwise "clean up" a
deliberate choice (the non-unique index is the canonical example).

Small, reviewable diffs. One concern per branch. A failed run should be recoverable with
`git branch -D`, not a cleanup session.
