# DOC 01 — METHODOLOGY: DEPTH-N v2.0
Status: Active · Supersedes: DIMEFIL-A v1.x (retained as reporting view, see doc 03)
Change protocol: any edit to this document increments the version, is announced in the changelog, and never retroactively alters published scores.

## 0. What changed from v1 and why (summary)
DIMEFIL-A was a taxonomy of what actors *do*; the question is what a country is *embedded in*. v2 replaces the additive pillar model with **DEPTH-N**: DEPTH dimensions measured per stack layer, formalized over a **network of dyadic exposures** rather than free-standing country scalars. The revision responds to five specific critiques, each addressed in the section noted:

1. Provenance (P) unmeasurable / collapses into vendor nationality → decomposed into observable sub-indicators with explicit nulls (§2.3).
2. Country scalars contradict the network-theoretic lineage → dyadic exposure graph; country scores are *derived* exposure measures (§3).
3. Multiplicative H-gate erases low-readiness states that matter → H becomes an interaction term; `strategic_salience` added (§4).
4. Unitary-state assumption fails empirically → sub-state actor layer (§5).
5. No out-of-sample test → index-level pre-registered predictions; renewal-cliff test set (§6, doc 04).

## 1. Intellectual lineage (operative, not decorative)
The framework is built directly on, and is accountable to, the following literature. Where the model and the literature conflict, the changelog must say so.

- **Hirschman (1945), *National Power and the Structure of Foreign Trade*.** Asymmetric trade dependence produces an "influence effect" via unequal exit costs. This is the model's core thesis, and it is *dyadic*: influence exists between a specific pair of actors. Consequence: the primitive object in DEPTH-N is the dyad, not the country (§3).
- **Farrell & Newman, "Weaponized Interdependence" (2019).** Power flows from position at network chokepoints, through two distinct effects: the **panopticon effect** (the hub sees flows) and the **chokepoint effect** (the hub can deny flows). Consequence: Provenance is split so that seeing and denying are scored separately — they have different coercive uses and different time constants (§2.3), and the model is topological: country scores are derived from graph position (§3).
- **Kuik (hedging literature, Southeast Asia).** Hedging is a deliberate portfolio of contradictory signals; states invest in illegibility. Consequence: no scalar is treated as a complete description of a hedger; posture is a *clustering output* over disaggregated data, not an analyst label on an ambiguous number (§4.3); the hedging prior governs all cascade forecasts (§6).
- **Shapiro & Varian (1998).** Switching costs and installed base. The Exit-cost dimension and the lock-in module.
- **Bradford, *The Brussels Effect*.** Regulatory export as power. Encoded as sovereign-vector exposure concentrated at the Models/Applications layers, weak at Silicon (§2.6).

The name "Silicon Dominoes" is kept for the audience; the analytics are anti-domino by construction. Domino theory's failure — treating states as passive tiles, ignoring nationalism, elite factionalism, and hedging-as-strategy — is the null hypothesis this model must beat, and the prediction ledger (doc 04) is the scoreboard.

## 2. The DEPTH dimensions, per layer
Seven layers: **Power, Facilities, Silicon, Networks, Cloud, Models, Applications.** Each layer in each country is scored on:

| Dim | Meaning | Type |
|---|---|---|
| **D** | Dependence — whose kit is installed, by share | stock |
| **E** | Exit cost — tenor, encumbered debt, retrained workforce, data portability | stock |
| **P** | Provenance & control — who can see, who can deny, under whose law | chokepoint (split, §2.3) |
| **T** | Trajectory — direction and instrument tier of new commitments | flow |
| **H** | Headroom — absorptive capacity | country-level interaction term (§4.2) |

**Stock/flow separation is mandatory.** D and E describe what is built; T describes what is being decided. They update on different cadences (system prompt §4) and are never averaged into a single "movement" number. The weekly product is a T instrument; D/E are quarterly, with Tier-4/5 events and reversals triggering out-of-cycle recomputes.

### 2.1 Dependence (D)
Per layer, per supplier-country-of-control: installed-base share. Sources: customs (HS codes), spectrum and type-approval registries, RAN/core vendor audits, cloud region maps, DC tenancy, model-deployment evidence. D is a share vector over supplier poles {US, PRC, sovereign/third, other}, not a signed number.

### 2.2 Exit cost (E)
Per layer: remaining contract tenor; debt secured against the asset and its creditor; certified-workforce depth on the incumbent stack; data-format and API portability; existence of an *available* alternative (a substitute that lacks export approval or financing is not an alternative). E ∈ [0,100] per incumbent pole.

### 2.3 Provenance & control (P) — decomposed
P is the framework's differentiator and its hardest measurement problem, because control terms live in confidential contracts. It is therefore never scored holistically. It is scored as five observable sub-indicators, each of which is either evidenced or **null with an `opacity_reason`** — never proxied by vendor nationality (that is D, and using it here recreates the collinearity v2 exists to remove).

| Sub-indicator | Effect type (Farrell-Newman) | Observable evidence |
|---|---|---|
| `p_license` — is operation contingent on a revocable export license or end-use agreement? | Chokepoint | License grants/denials in official registers, entity-list status, published license conditions |
| `p_update` — does the stack depend on a remote update/attestation channel the supplier controls? | Chokepoint | Product architecture documentation, attestation requirements in published terms, incident reporting |
| `p_spares` — maintenance, parts, and consumables dependency and stockpile depth | Chokepoint (slow) | Trade flows in parts HS codes, service-contract awards |
| `p_jurisdiction` — under whose legal process does the data/telemetry sit? | Panopticon | Cloud-region jurisdiction, localization law, published lawful-access regimes, adequacy decisions |
| `p_telemetry` — does the supplier see operational data by design? | Panopticon | Architecture disclosures, regulator findings, documented incidents |

Chokepoint and panopticon sub-scores are published separately and never summed into one P number: the ability to switch a thing off and the ability to watch it are different powers with different escalation ladders. A country on amortized, unlicensed hardware and one on identical licensed hardware have the same D and categorically different `p_license` — this distinction is the model's reason to exist and must survive into the UI.

Expected coverage is honest: for most countries most sub-indicators will be null. Publish the coverage rate. A P built on 20% evidence and 80% nulls displayed as nulls is decision-grade; the same number with nulls silently imputed is disinformation.

### 2.4 Trajectory (T)
Flow of new commitments per layer, signed by pole, weighted by the instrument ladder (system prompt §2.1) including decay and the Goodhart cap on Tier-1/2 events. T is always published with its instrument-tier composition.

### 2.5 Bundles
Because both blocs contract in packages (American AI Exports Program; DSR project bundles), events sharing a `bundle_id` are scored per layer but the exposure edges they create carry the bundle reference, and cascade/renewal analysis treats the bundle as the unit of decision. Layer independence is a display convenience, not a modeling assumption.

### 2.6 The regulatory (Brussels-effect) coefficient
GDPR adequacy, AI Act alignment, and localization mandates are encoded as sovereign-vector exposure concentrated at the Models and Applications layers, weak at Silicon (no substitute at scale). An EU state running Mistral on Nvidia scores high sovereign exposure at Models, US-pole D at Silicon, and clusters `sovereign_seeking` — never coded as a US win.

## 3. The network formalization
### 3.1 Primitive object: the exposure edge
The model's primitive is a **dyadic exposure edge**: (country, controller, layer) → {D share, E, applicable P sub-indicators, T flow, bundle refs, evidence refs}. Controllers are the entities that actually hold chokepoints: this is usually a state pole (US, PRC) but can be a specific creditor (China Exim), a specific hub (a cable consortium, a regional cloud), or an EU regulatory regime. The graph is bipartite-ish: countries × controllers, with layer-typed edges.

### 3.2 Derived country scores
Country-level numbers are *computed from the graph*, not asserted:
- `exposure_us`, `exposure_prc`, `exposure_sovereign` ∈ [0,100] — layer-weighted aggregation of edge weights toward each pole, where edge weight combines D, E, and the chokepoint P sub-indicators. These are the load-bearing published numbers.
- `alignment_index` ∈ [−100,+100] — **display-only derived metric**, `f(exposure_us − exposure_prc)`, retained because map readers expect it. It is flagged `derived: true` in every contract, and the UI must offer the two-axis exposure view (US-exposure × PRC-exposure) as a first-class layer, because the mechanisms of the two poles' leverage are *not commensurable* — export-license chokepoints and concessional-debt entanglement are different kinds of grip, and the signed scalar imposes commensurability by fiat. The asymmetry view (which pole holds *which kind* of chokepoint) is layer 3 in the UI.
- `lock_in` ∈ [0,100] — max over poles of pole-specific E·chokepoint-P composite.
- Network-position metrics: exposure concentration (HHI over controllers), dependence on single financed hubs, shared-controller centrality. These feed cascade susceptibility (§6) and give the Farrell-Newman topology real computational content.

### 3.3 Aggregation form
Within a pole, across layers: weighted sum of edge composites, layer weights published and slider-adjustable, default weighting Silicon/Networks/Cloud above Models/Applications above Power/Facilities for *exposure* purposes (chokepoints concentrate mid-stack). No multiplicative gating (see §4.2). Measurement error is propagated: every derived score carries a confidence band from its edges' source-tier mix and null rates.

## 4. Headroom, salience, and posture
### 4.1 Readiness (H)
Unchanged in content from v1 §2.3: six sub-indices (Power, Compute, Connectivity, Human capital, Capital/macro, Institutional), 0–100, published with the discrete Tier T0–T5. Power remains the binding constraint and a signed MOU never raises readiness.

### 4.2 H as interaction, not gate
v1's multiplicative gate compounded measurement error and rendered low-H states analytically invisible — wrongly, since low-readiness states supply ITU votes, basing, minerals, and cable landings (Cuba is T0 and a WAICO founder). v2:
- H **scales the interpretation of T** (a Tier-3 DC contract in a T1 country is coded but its projected D/E impact is discounted by an H-derived realization probability, published as `realization_prob_est`).
- H **never multiplies** D, E, or P: installed dependence is real regardless of readiness.
- New field `strategic_salience` ∈ [0,100]: non-stack strategic assets — multilateral voting weight, basing/overflight, critical-mineral endowment, cable-landing geography. Ensures low-H, high-salience states remain visible. Rendered as its own optional layer; never folded into alignment.

### 4.3 Posture as clustering output
The posture enum (`committed_us`, `committed_prc`, `leaning_us`, `leaning_prc`, `hedged_active`, `sovereign_seeking`, `excluded`, `inert`) is assigned by **unsupervised clustering over the disaggregated per-layer DEPTH vectors**, with the cluster→label mapping reviewed and versioned. Analysts may override with `posture_override: true` plus a written rationale rendered in the UI. This respects the Kuik point: a hedger's signature is *in the structure of its contradictions*, which a scalar destroys but a cluster over layer-level vectors can detect. `hedged_active` and `inert` remain visually distinct without exception.

### 4.4 Contest score
`contest_score` combines: high H or high salience; low exposure concentration; low lock-in; presence of active competing offers; upcoming renewal cliffs. It is UI layer 2, immediately after alignment.

## 5. Sub-state actors
The unitary-state assumption fails where it matters most: vendor selection in much of the Global South is predicted better by ministry factional interests, telco ownership, and rent distribution than by national posture. Therefore:
- Exposure edges may attach to **sub-state entities** (a state telco, a ministry, a sovereign fund, a province) via `sub_state_actor`, rolled up to country level for the map but preserved in `edges.json`.
- The watchlist layer tracks these entities directly (system prompt §3.1).
- Cascade mechanisms may cite sub-state channels ("incumbent telco's Huawei-certified engineering corps" is a valid mechanism; "the government leans PRC" is not).
- Where reporting indicates intra-government contestation over a procurement, code it as a `contested_decision` event: these are among the highest-information signals the system ingests.

## 6. Cascade module and falsification discipline
Mechanisms modeled: shared interconnectors and cable routes, standards harmonization, bundled financing, cross-border vendor interoperability, ministerial peer emulation, regional reference deployments. `cascade_susceptibility` now draws on the network metrics of §3.2 (shared-controller centrality, single-creditor concentration) plus elections/transitions and 18-month renewal cliffs.

Hard rules, unchanged in force, extended in scope:
1. Every cascade edge carries a mechanism string naming a concrete channel.
2. **The prediction ledger now covers the index itself, not only cascade forecasts** (doc 04): a standing, pre-registered prediction set — primarily *vendor choice at contract-renewal cliffs* — resolves against observed awards, and Brier scores for both cascade and index predictions are published on the dashboard. An index that cannot be scored is a coding scheme, not a model; this is the falsification surface.
3. Hedging prior: absent strong evidence, a mid-readiness state multi-sources rather than flips.
4. Projections are toggleable overlays with confidence bands, never co-rendered with observations.

## 7. Published country record (schema summary)
`iso3, exposure_us, exposure_prc, exposure_sovereign, alignment_index (derived), sovereign_pull, readiness, readiness_tier, strategic_salience, lock_in, posture, posture_override?, contest_score, cascade_susceptibility, per_layer: {D vector, E, p_license, p_update, p_spares, p_jurisdiction, p_telemetry, T + tier composition}, confidence, evidence_count, null_coverage_rate, methodology_version, weight_vector_stamp`.

Nulls are nulls. `_est` fields carry methods. Countries under the evidence threshold render "insufficient data."
