# DOC 07 — BUILD REVIEW: FINDINGS AND REMEDIATIONS
Version: 1.1 · Status: Active · Supersedes: v1.0 (2026-08-16, Project-knowledge only, never committed)
Read when: planning build-order work (ARCHITECTURE.md §14), reviewing a schema change, or deciding whether a publication cycle is fit to ship.

Companion to: doc 01 (methodology), doc 02 (lineage and standing critiques), doc 03 (DIMEFIL crosswalk), doc 04 (prediction ledger), doc 06 (DIMEFIL provenance — **not yet in this repository as of v1.1; references retained, see §8**), ARCHITECTURE.md, `schemas/README.md`.

> **v1.1 note.** This document existed only in the Claude Project knowledge store until 2026-09-08 and is committed here for the first time. F-7 is new and records the design decisions taken for the Phase 2 breaking schema pass; it is authoritative for the F-2 implementation where the two overlap. All v1.0 text is retained unchanged below except where marked.

---

## 0. Scope and summary

This document reviews the v2.0 methodology set against the contract schemas, the architecture, and the map prototype, and records findings where a design commitment made in prose is not yet enforced anywhere in the system. None is a disagreement with the methodology. Each is a case where a defence the methodology relies on — the Provenance differentiator, the anti-taxonomy edge structure, the bundle discipline, the falsification surface, the divergence flags, the closure rule, the pole semantics — currently depends on analyst diligence rather than on a gate, and would therefore fail silently rather than loudly.

Every remediation below is written to be implementable: schema fragments, `validate.py` check contracts, and predicate definitions in the form the pipeline already uses. F-2, F-3 (at approval) and F-7 block publication once implemented; the rest are corrections, additions, or disclosures.

---

## 1. Findings at a glance

| ID | Finding | Artifact touched | Blocks publish? |
|---|---|---|---|
| **F-1** | Evidenced-P weight share is unmeasured; P can thin to nothing without tripping doc 02 regression test 1 | `countries.schema.json`, `validate.py`, UI | Yes, below floor |
| **F-2** | `direction` is coded beside the controller instead of derived from it | `edges.schema.json`, `events.schema.json`, new `controllers.json` | Yes |
| **F-3** | `bundle_id` linkage is prompted in the review UI, not required | `events.schema.json`, review UI | No — approval gate |
| **F-4** | Class B derivation formula unspecified; review trigger has no owner and omits baseline (b) | doc 04, `ledger.schema.json` | No |
| **F-5** | Doc 03 rule 4 has no computable predicate, so divergence flags will not exist | doc 03, `countries.schema.json`, exports | No |
| **F-6** | Closure rule already broken once (`strategic_salience`); `sovereign_pull` undefined; `alignment_index` `f` unspecified | docs 01 and 06 | No |
| **F-7** *(v1.1)* | `sovereign` is relational but was treated as a registry-level pole; incorporation conflated with control; reversals of pre-collection facts uncodeable; fixture data indistinguishable from evidence | `controllers.schema.json`, `edges.schema.json`, `events.schema.json`, new `pole_map.json`, `validate.py`, docs 01 and 06 | Yes |

---

## 2. Findings and remediations

### F-1 — Provenance can degrade to nothing without tripping the test that exists to catch it

**Finding.** Doc 01 §2.3 states honestly that most sub-indicators will be null for most countries, and §3.2 builds edge weight from D, E, *and the chokepoint P sub-indicators*. `exposure_us` and `exposure_prc` are described as the load-bearing published numbers. If P nulls dominate, those numbers are D+E composites carrying a P label. Doc 02's standing regression test — P–D correlation approaching 1 — cannot detect this, because a correlation computed over the evidenced subset says nothing about how much of the weight mass came from evidence at all. Nobody proxies vendor nationality; the differentiator simply thins out, and the failure is invisible.

**Why it bites here specifically.** The published `null_coverage_rate` in doc 01 §7 is a count-based coverage measure. Counts and weight mass diverge exactly where it matters: a country can be 60% evidenced on low-weight Applications-layer sub-indicators and 0% evidenced at Silicon and Networks, where the default layer weighting puts most of the mass.

**Remediation.**
1. Add `p_evidence_share` ∈ [0,1] to the published country record: the fraction of *edge-weight mass contributing to that country's exposure scores* that derives from evidenced (non-null) chokepoint P sub-indicators, not from imputation or from D and E alone.
2. Publish the same figure globally in the publication envelope as `p_evidence_share_global`.
3. Define `P_EVIDENCE_FLOOR` (proposed initial value **0.20**, versioned with `methodology_version`). Below the floor, the country's exposure scores render with an explicit label — *D+E composite; provenance evidence below floor* — and the P-derived component of edge weight is set to zero rather than imputed. The score still publishes; the claim it makes is narrowed.
4. Add to doc 02 as a standing regression test alongside test 1.

**Verification.** `validate.py` check `check_p_evidence_share` (§4). Publication proceeds; the labelled degradation is the enforcement, not a hard failure — except that a missing `p_evidence_share` field fails schema.

---

### F-2 — `direction` should be derived from the controller, not coded beside it

**Finding.** Doc 01 §3.1 gives the right primitive: controllers are the entities that actually hold chokepoints — a state pole, a specific creditor such as China Exim, a cable consortium, an EU regulatory regime. But ARCHITECTURE.md §6 lists `direction` among the fields the extraction step pre-fills and the reviewer approves, which makes us / prc / sovereign an independently asserted three-bin taxonomy of actors sitting next to the graph structure that exists to avoid one. Doc 06 §5.5 records the concrete instance: the first machine-coded candidate event did not populate `controller_name`. *(v1.1: two further instances are recorded in STATUS.md, 2026-09-08 — captures 1274 and 1242, both mis-directed from counterparty nationality.)*

**Why it bites here specifically.** The mixed case — a Singapore-controlled operator financed by a Chinese policy bank running US silicon — is resolved by an analyst picking one bin at coding time. That is resolution by fiat, and it is unrecoverable downstream because the edge that would have preserved the ambiguity was never written.

**Remediation.**
1. Introduce a sixth contract, `controllers.json`: a versioned registry keyed by `controller_id`, carrying `name`, `controller_type` (state | creditor | consortium | regulatory_regime | firm), a jurisdiction of control, and `evidence_refs`. *(v1.1: pole is no longer asserted per entry; see F-7 for the registry shape that supersedes the v1.0 field list.)*
2. Make `controller_id` **required** on every exposure edge, with no null branch. There is no such thing as an exposure to an unidentified controller; if the controller is unknown, the event is not codeable and belongs in `research_gaps`.
3. Remove `direction` as a stored, coded field. Emit it as `direction_derived` with `derived: true`, computed from the edge's controller per F-7. The mixed case then produces three edges with three controllers and three derived directions, and the reader sees all three.
4. Where the controlling entity is genuinely distinct from any pole — a hedged joint venture, a consortium with mixed membership — it gets its own registry entry and its mixed ownership recorded structurally (F-7 §4), rather than being forced toward a pole.

**Verification.** `check_controller_resolution` and `check_direction_derived` (§4). Both hard-fail. This is a breaking change: bump `schema_version` major, per `schemas/README.md` versioning, and update frontend and exports in the same release.

---

### F-3 — `bundle_id` linkage is prompted, not required

**Finding.** Doc 01 §2.5 asserts that layer independence is a display convenience rather than a modelling assumption. That assertion is true only to the extent that bundles are actually linked. ARCHITECTURE.md §6 describes `bundle_id` linkage *prompts* when an event resembles a package. A prompt is a suggestion, and unlinked layer edges are indistinguishable downstream from genuinely independent ones — at which point renewal and cascade analysis treat as separable what was contracted as one decision. This is the precise mechanism by which the Zweibelson objection (doc 06 §3.3) returns through the back door.

**Remediation.**
1. Add required field `bundle_disposition` to every event: `anchor` | `member` | `standalone`.
2. `standalone` requires `standalone_rationale` (free text, rendered in the UI). Asserting independence becomes an act with a name on it, like `posture_override`.
3. The review UI cannot submit an approval with `bundle_disposition` unset. This is an approval gate, not a publish gate — but a published event lacking the field fails schema.
4. Retain the existing `validate.py` rule of one anchor per `bundle_id`.

**Verification.** `check_bundle_disposition` (§4).

---

### F-4 — The Class B derivation formula is the empirical content, and it does not exist yet

**Finding.** Doc 04 Class B step 2 states that the model emits a probability distribution over renewal outcomes "derived mechanically" from the country's DEPTH-N state, with the derivation formula versioned. That formula is what makes this an index rather than a coding scheme; it appears in neither docs 01–04 nor the schemas. Everything else in the ledger is scaffolding around a function that has not been written.

Two related gaps. The four-quarter methodology-review trigger names no owner and no dated declaration point, which is the difference between a commitment and an intention. And it attaches only to baseline (a), "incumbent always retained" — while baseline (b), the hedging-prior base rates, is the analytically demanding one: a model that merely reproduces its own hedging prior will beat persistence while adding nothing.

**Remediation.**
1. Specify the derivation as a pure, versioned function `classb_formula_version` over *published* country state only — `E`, chokepoint-P composite, T tier composition, `contest_score`, `lock_in`, and active competing offers. Purity over published state is the auditability requirement: a third party holding `countries.json` must be able to reproduce every Class B distribution without access to the private pipeline.
2. Require the emitted distribution to cover all six outcomes and sum to 1 within tolerance.
3. Keep the analyst judgment forecast as a separate ledger entry with its own Brier track, per doc 04 step 2.
4. Extend the trigger: add to `ledger.json` a `methodology_review_trigger` object carrying `owner_role: maintainer`, `evaluation_date` (the dated point at which the four-quarter window is assessed each cycle), `baselines_failed: []`, and `status`. Fire on failure against **either** baseline for four consecutive quarters, and publish the finding on failure whether or not the review has happened yet.

**Verification.** `check_classb_distribution` and `check_review_trigger_present` (§4).

---

### F-5 — Doc 03 rule 4 has no computable predicate

**Finding.** Rule 4 requires a divergence flag when a reader decision *would differ* between the DIMEFIL projection and the DEPTH-N measurement. That condition cannot be implemented as written, so it will not be implemented, and doc 06 §5.4 — the projection quietly becoming the system of record — arrives by omission rather than by decision. The prototype has no DIMEFIL tab yet, which makes this the cheapest possible moment to fix.

**Remediation.** Rule 4 already names two known cases. Both are computable. Ship them as the initial flag set and let the logged flags suggest the third.

- **DF-1 — diplomatic warmth without installed dependence.** Flag country *c* toward pole *p* where the percentile rank of the D-pillar (diplomatic) value toward *p* exceeds the percentile rank of `exposure_p` by ≥ `DF1_THRESHOLD` (proposed **20** percentile points). Emit `{predicate: "DF-1", pole, d_pillar_pct, exposure_pct, delta}`.
- **DF-2 — identical economics, opposite licence status.** Within each E-pillar decile, flag every country pair at the same layer whose `p_license` is evidenced and differs in restriction status. Emit `{predicate: "DF-2", layer, peer_iso3, e_pillar_decile}`. Null `p_license` on either side does not flag; it is an evidence gap, not a divergence.

Add `divergence_flags: []` as a required array on every country record whenever the DIMEFIL view is generated — required, and permitted to be empty, so that absence of the field is distinguishable from absence of divergence. Flags render in the web view **and** in the PDF briefing export (ARCHITECTURE.md §11); the report generator refuses to produce a DIMEFIL export whose source record lacks the field. Log all flags for analysis, per doc 03 rule 4.

**Verification.** `check_divergence_flags_present` (§4).

---

### F-6 — Closure-rule honesty, one undefined field, and one unspecified function

**Finding.** Three text-level items, none structural, all worth correcting before doc 06 is briefed externally.

1. **The closure rule has already been broken once.** Doc 06 §3.7 claims DEPTH-N dimensions are defined by measurement type, so new domains enter as layers or edges and never as dimensions. But v2.0 shipped `strategic_salience` — multilateral votes, basing, minerals, cable landings — which is subject matter, not a measurement type, appended precisely because the existing dimensions could not see something that mattered. It is handled well: own field, own optional UI layer, never folded into alignment. It is still the DIMEFIL pattern appearing at the first revision. Doc 06 should say so rather than be caught saying otherwise.
2. **`sovereign_pull` is undefined.** It appears in the doc 01 §7 published record and nowhere in §2 through §6. Define it or remove it; a field in the contract with no definition in the methodology is exactly the "means whatever the briefer needs" problem doc 06 §1.2 levels at DIMEFIL.
3. **`alignment_index`'s `f` is unspecified.** Doc 01 §3.2 gives `f(exposure_us − exposure_prc)` without defining `f`. Since both inputs are published, specifying `f` makes the metric fully reconstructable by any reader — which converts a display-only number from something to be trusted into something to be checked. If `f` is the identity clipped to [−100, +100], say so.

---

### F-7 — Pole resolution: `sovereign` is a derivation, not a pole *(new in v1.1)*

**Finding.** The system prompt §1 and doc 01 §2.1 name the supplier poles as {US, PRC, sovereign/third, other}, and the v1.0 F-2 remediation carried that enum into the controller registry as a per-entity `pole` field. But `sovereign` is *relational*: Viettel is sovereign to Vietnam and a third-country supplier to Laos. Baking a country-relative property into a country-independent registry object forces the same entity to be one thing everywhere, which is false, and it invites the residual reading the system prompt forbids — "sovereign/third" as whatever is neither US nor PRC.

Four further problems surfaced when the F-2 design was tested against the two 2026-09-08 fixtures (STATUS.md, sixth session):

1. **Incorporation was being read as control.** A hyperscaler's Singapore subsidiary is incorporated in SGP and controlled from CHN or USA. A jurisdiction match on incorporation would derive `sovereign` for the normal case of a regional subsidiary.
2. **Pole and jurisdiction were both asserted.** Two independently coded fields either duplicate each other or disagree; neither outcome is auditable.
3. **Reversals of pre-collection facts were uncodeable.** Requiring a reversal to reference a coded event means every cancellation of something built before August 2026 — nearly all reversals in year one — drops into `research_gaps`. The system prompt says reversals are the highest-information class and must never be underweighted.
4. **Fixture data was indistinguishable from evidence.** Test fixtures carrying real entity names and plausible-looking `evidence_refs` are a fabricated citation sitting in the repository, and the project has already recorded one agent-fabricated link.

**Remediation — registry shape (supersedes the v1.0 F-2 field list).**

`controllers.json` entries carry:

| Field | Required | Meaning |
|---|---|---|
| `controller_id` | yes | `^[a-z][a-z0-9_]*$` |
| `name` | yes | |
| `controller_type` | yes | `state` \| `creditor` \| `consortium` \| `regulatory_regime` \| `firm` |
| `control_jurisdiction` | yes | Jurisdiction of **ultimate control**, ISO3 or `EU`. Load-bearing. |
| `incorporation` | no | Jurisdiction of incorporation, ISO3. Descriptive only. |
| `parent_id` | no | `controller_id` of the controlling parent, where the chain matters. |
| `pole_override` | no | `us` \| `prc` \| `third`. The **only** asserted pole field. |
| `override_rationale` | iff override | Free text, rendered in the UI, like `posture_override`. |
| `members[]` | consortium only | `{controller_id, role: landing_party \| operator \| investor, share?: [0,1]}` |
| `evidence_refs[]` | yes, ≥1 | |

**Rules.**

1. **Pole enum is `us | prc | third`.** There is no `sovereign` pole. `third` is acknowledged to be a residual *at pole level*; the third-pole vector is recovered at aggregation from `control_jurisdiction`, which carries full granularity. Doc 01 §2.1 owes a sentence saying so (F-6 text pass).
2. **Pole is derived, not asserted.** `effective_pole = pole_override ?? pole_map[control_jurisdiction] ?? pole_map.default`. `schemas/pole_map.json` is versioned with `methodology_version`; initial content `{USA: us, CHN: prc, HKG: prc, MAC: prc, EU: third, TWN: third, default: third}`. An override is the one place a pole is an analytic assertion, and it is where evidence is required.
3. **`sovereign` is an edge-level derivation.** `direction_derived.value = "sovereign"` iff `controller.control_jurisdiction == edge.country_iso3`, else `effective_pole`. Always emitted with `derived: true`.
4. **Incorporation informs P, not direction.** `incorporation == edge.country_iso3` with `control_jurisdiction` elsewhere is a reviewer prompt to evidence `p_jurisdiction` (data under local legal process despite foreign control). It never changes `direction_derived`.
5. **No exposure field is a residual of another.** `exposure_sovereign` is computed from edges whose derived direction is `sovereign`; it is never `100 − exposure_us − exposure_prc`. `validate.py` must **not** enforce any sum across exposure fields, and the fixture set carries a passing record whose exposures sum to less than 100.
6. **Consortium = one registry entry.** Malaysia is exposed to the consortium, not to each member. The consortium's pole derives from a documented controlling member if one exists, else `third`. Edges to a consortium lacking a documented controlling member carry `mixed_control: true`, so the scoring pipeline can split weight by evidenced shares or disclose "unsplit." This is lossy (doc 07 v1.0 §7 Q2); the loss is now flagged rather than silent. *(Answers Q2.)*
7. **Controller ≠ counterparty.** Events carry `controller_id` (required) and optional `counterparty_ids[]`. A tenancy that commits a foreign tenant to a locally-operated facility is **two events, one `bundle_id`**: operator at Facilities, tenant at Cloud/Models — because a committed tenant holds a panopticon position (`p_jurisdiction`) that is a real exposure. The review UI needs "split event / promote counterparty to controller at layer X."
8. **Reversals are never uncodeable.** `reversal_of` is a `oneOf`: a reference to a coded event or edge, **or** an inline `reversal_target {controller_id, country_iso3, layer, tier_reversed}` — the same shape as doc 04 L-8's `exercise_target`. `controller_id` on a reversal event is the controller of the thing reversed; the acting entity goes in `actor_id`. A blanket regulatory action (a nationwide construction pause) is coded as a **P event** with the regulator as controller — the state holds and exercised a deny capability — with per-operator T reversals following as operators are named. It is a candidate `chokepoint_exercise` under L-8.
9. **Fixture containment is mechanical.** Every URL in `schemas/fixtures/` uses the RFC 2606 reserved TLD `https://fixture.invalid/`; every fixture id matches `^test_`; every fixture name contains `(fixture)`. No real entity names appear in fixtures. `validate.py` `check_no_fixture_artifacts` hard-fails the publish path on any of the three markers. `CLAUDE.md` states the rule for agents. A hallucinated reference has nowhere to live.

**Verification.** `check_pole_derivation`, `check_direction_derived` (amended), `check_consortium_members`, `check_reversal_target`, `check_no_fixture_artifacts` (§4). All hard fail. Ships in the same `schema_version` major bump as F-2.

**Fixtures (2026-09-08).** Capture 1274 (Firmus / OpenAI, Malaysia): extractor coded `us` from the tenant's nationality; under F-7 it is operator (control AUS, derived `third`) at Facilities plus tenant (control USA, `us`) at Cloud, one bundle. Capture 1242 (Thailand pauses 49 DCs): extractor coded T1 `sovereign` intent plus a hallucinated T5 `us`; under F-7 it is a P event, regulator as controller, derived `sovereign`, `chokepoint_exercise` candidate, with a research gap for operator identities.

---

## 3. Proposed additions to doc 02's standing regression tests

To be appended to the standing-critique list, in the same form as the existing entries:

- **Under critique 1 (P-collapse), second test.** Evidenced-P weight share: `p_evidence_share_global` below `P_EVIDENCE_FLOOR` means the Provenance differentiator has failed in practice regardless of what the P–D correlation says. Alerts and appears in the changelog.
- **New critique 9 — Controller resolution.** If exposure edges can be written without an identified controller, the graph degenerates into the three-bin actor taxonomy the edge structure exists to avoid. Standing answer: `controller_id` required, `direction` derived. Regression test: count of edges failing controller resolution must be zero at every publish.
- **New critique 10 — Bundle closure.** If events can be approved without a bundle disposition, layer independence becomes a modelling assumption by default. Standing answer: `bundle_disposition` required, `standalone` requires rationale. Regression test: the share of approved events dispositioned `standalone` is published each cycle; a sharp rise means reviewers are defaulting.
- **New critique 11 — Residual poles** *(v1.1)*. If `sovereign` can be asserted at registry level, or any exposure field computed as a residual, the third-pole vector becomes "everything else." Standing answer: F-7 rules 1–5. Regression tests: zero registry entries with a non-enum pole; `exposure_sovereign` recomputed from edges equals the published value; the share of controllers resolving via `pole_map.default` is published each cycle — a rising share means `control_jurisdiction` is being coded lazily.

---

## 4. `validate.py` check contracts

Signatures follow the existing convention: each returns a list of violation records; non-empty means non-zero exit and no publish, except where noted.

```python
def check_p_evidence_share(cycle) -> list[Violation]:
    """Every country record carries p_evidence_share; envelope carries the global figure.
    Below P_EVIDENCE_FLOOR, exposure scores must carry the degraded-label flag.
    Violation only on a missing field or a missing label — a low share is disclosed, not blocked."""

def check_controller_resolution(cycle) -> list[Violation]:
    """Every edge and event controller_id, every parent_id, member controller_id,
    counterparty_id and actor_id resolves to controllers.json. Hard fail."""

def check_pole_derivation(cycle) -> list[Violation]:               # v1.1
    """effective pole == pole_override if present else pole_map[control_jurisdiction]
    else pole_map.default. pole_override requires non-empty override_rationale.
    No registry entry carries a pole outside {us, prc, third}. Hard fail."""

def check_direction_derived(cycle) -> list[Violation]:              # amended v1.1
    """No stored direction anywhere. Where direction_derived is present it is flagged
    derived: true and its value is 'sovereign' iff controller.control_jurisdiction ==
    edge.country_iso3, else the controller's effective pole. Hard fail."""

def check_consortium_members(cycle) -> list[Violation]:             # v1.1
    """members[] present only when controller_type == consortium. If any share is
    present, shares sum to <= 1.0. Edges to a consortium with no documented
    controlling member carry mixed_control: true. Hard fail."""

def check_reversal_target(cycle) -> list[Violation]:                # v1.1
    """Every reversal-class event carries reversal_of as a resolvable event/edge
    reference or a complete inline reversal_target. Hard fail."""

def check_no_fixture_artifacts(cycle) -> list[Violation]:           # v1.1
    """Outside schemas/fixtures/: no URL under the .invalid TLD, no id matching ^test_,
    no name containing '(fixture)'. Hard fail on the publish path."""

def check_bundle_disposition(cycle) -> list[Violation]:
    """Every event carries bundle_disposition; standalone carries a non-empty rationale;
    exactly one anchor per bundle_id (existing rule retained). Hard fail."""

def check_classb_distribution(cycle) -> list[Violation]:
    """Class B entries cover all six outcomes, sum to 1 within tolerance, and carry
    classb_formula_version. Analyst forecasts are separate entries. Hard fail."""

def check_review_trigger_present(cycle) -> list[Violation]:
    """ledger.json carries methodology_review_trigger with owner_role, evaluation_date,
    baselines_failed, status. Hard fail on absence; a fired trigger publishes, it does not block."""

def check_divergence_flags_present(cycle) -> list[Violation]:
    """When the DIMEFIL projection is generated, every country record carries
    divergence_flags (possibly empty). Hard fail on absence of the array."""
```

---

## 5. Schema deltas

- `common.schema.json` — add `$defs` for `controller_id`, `pole` (`us | prc | third`), `jurisdiction` (`^[A-Z]{3}$|^EU$`), `controller_type`, `derived_direction` (`us | prc | third | sovereign`), `member_role`, `reversal_target`, `bundle_disposition`, `divergence_flag`, and an `evidence_share` numeric in [0,1].
- **New** `controllers.schema.json` — the registry described in F-7. Sixth contract; add to the publication envelope and the export set.
- **New** `pole_map.json` — versioned jurisdiction→pole table (F-7 rule 2).
- `edges.schema.json` — `controller_id` required, no null branch; stored `direction` forbidden; optional `direction_derived {value, derived: true}`; optional `mixed_control`.
- `events.schema.json` — `controller_id` required; `counterparty_ids[]?`; `actor_id?`; `reversal_of` (oneOf ref | inline target) required for the reversal class; `bundle_disposition` required (F-3); `standalone_rationale` conditionally required; `direction` removed as a coded field; L-8 fields (`chokepoint_exercise`, `exercise_type`, `exercise_target`) per doc 04.
- `countries.schema.json` — `p_evidence_share` required; `divergence_flags` required when the DIMEFIL projection is generated; the degraded-label flag on exposure scores; no sum constraint across exposure fields (F-7 rule 5).
- `ledger.schema.json` — `classb_formula_version` on Class B entries; `methodology_review_trigger` object.
- `schemas/fixtures/README.md` — states fixtures are synthetic, use `.invalid`, and are barred from publish.
- `CLAUDE.md` — one line: agents never invent URLs or citations; test data uses `https://fixture.invalid/` exclusively.

`schema_version` **major** bump: F-2 and F-7 are breaking, and frontend and exports move in the same release.

---

## 6. Suggested order

1. **F-2 + F-7 together, first.** They are one breaking change to the same files; F-7 defines the registry F-2 introduces.
2. F-3 + doc 04 L-8/L-9 next, since they share `events.schema.json` and the approval gate belongs in the review UI being built in the same build-order step (ARCHITECTURE.md §14.4).
3. F-1 with the scoring pipeline (§14.5), since evidence share is computed from the same weight assembly.
4. F-5 with the frontend (§14.6), before the DIMEFIL tab exists rather than after.
5. F-4 is independent and can run in parallel; it is the largest analytic task in the list and the one most likely to slip.
6. F-6 is a text pass on docs 01 and 06 and can be done in an hour; it now also carries the F-7 rule-1 disclosure about `third`.

---

## 7. Open questions for the maintainer

1. Is `P_EVIDENCE_FLOOR = 0.20` the right initial value, or should the floor be layer-specific — a higher bar at Silicon and Networks, where the default weighting concentrates mass?
2. ~~Does a consortium controller get one registry entry with `pole: third`, or one edge per member?~~ **Resolved v1.1 (F-7 rule 6):** one entry, roled members, `mixed_control` flag on edges.
3. Should `strategic_salience` remain a country field, or become a non-stack layer? The latter would restore the closure rule at the cost of a v3 event.
4. Who is `owner_role: maintainer` in practice, while the project has one contributor? The trigger needs a name attached to it before the first four-quarter window closes, not after.
5. *(v1.1)* Should `pole_map.json` distinguish `HKG` from `CHN` for entities whose `control_jurisdiction` is HKG but whose ultimate control is contested (dual-listed conglomerates)? Current answer: no — HKG→prc, with `pole_override` available where evidence says otherwise.
6. *(v1.1)* Where a consortium's shares are evidenced, should the scoring pipeline split exposure by share automatically, or only when a reviewer approves the split? Current lean: automatic when every member resolves and shares sum to 1.0 ± tolerance; otherwise `mixed_control` and unsplit.

---

## 8. Document dependencies

Doc 06 (DIMEFIL provenance) is cited in F-3, F-5, F-6 and F-7 but is not in `project-knowledge/` as of this version. Its references are retained because the arguments depend on them; the doc itself is a recovery item (STATUS.md). Doc 05 (Simple-mode lexicon) is likewise absent from the repository despite STATUS.md recording it as committed; it is not a dependency of this document.

---

## Changelog
- **v1.1 (2026-09-08)** — First commit to the repository (v1.0 existed only in Project knowledge). Added F-7 (pole resolution, incorporation vs. control, reversal codeability, fixture containment) with a registry shape that supersedes the v1.0 F-2 field list; `pole_map.json` introduced; five new `validate.py` checks; `check_direction_derived` amended. Added doc 02 critique 11. Resolved §7 Q2; added Q5–Q6. §6 order revised to run F-2 and F-7 as one pass. Added §8 recording that docs 05 and 06 are not in the repository.
- **v1.0 (2026-08-16)** — Initial. Six findings recorded against the v2.0 methodology set, the contract schemas, ARCHITECTURE.md, and the map prototype. Two proposed hard-fail gates (F-2, F-3 at approval), one disclosed degradation path (F-1), one required-array gate (F-5), one analytic gap (F-4), one text pass (F-6). Three additions proposed to doc 02's standing regression tests.
