# DOC 02 — ACADEMIC LINEAGE AND STANDING CRITIQUES
Status: Reference · Read when: explaining methodology, briefing external audiences, or evaluating proposed methodology changes.

## Purpose
This document records where the framework comes from, what each source actually claims, and the known critiques the design must continue to answer. It exists so that methodology drift can be checked against the literature, and so briefings can cite honestly.

## Foundations
**Hirschman, *National Power and the Structure of Foreign Trade* (1945).**
Claim used: asymmetric trade relationships generate an "influence effect" — the party with lower exit costs gains leverage over the party with higher ones. Design consequence: the model's primitive is the dyadic exposure edge, and Exit cost (E) is measured relative to a specific controller, never in the abstract. Caution: Hirschman's mechanism is bilateral; national aggregates are summaries of dyads, and the model must always be able to decompose back to the dyad.

**Farrell & Newman, "Weaponized Interdependence: How Global Economic Networks Shape State Coercion" (*International Security*, 2019).**
Claims used: (a) power derives from *position* in network topology, not attributes of nodes; (b) hub position enables two distinct effects — the **panopticon effect** (information extraction from flows) and the **chokepoint effect** (denial of flows); (c) institutional capacity to exploit hub position varies. Design consequences: the exposure graph (doc 01 §3); the split of Provenance into panopticon and chokepoint sub-indicators that are never summed; network-position metrics feeding cascade susceptibility. Caution: the theory predicts *capability*, not use; a chokepoint held is not a chokepoint exercised, and the ledger should track exercise events (license denials, cutoffs) as a distinct, rare, high-information class.

**Kuik Cheng-Chwee and the hedging literature (Southeast Asia).**
Claim used: hedging is a deliberate strategy of mixed, contradictory signals under uncertainty — insurance-seeking, not indecision. States invest in illegibility. Design consequences: the hedging prior in cascade forecasting; posture as a clustering output over disaggregated vectors (a hedger is identified by the *structure* of its contradictions); the prohibition on collapsing `hedged_active` with `inert`. Caution: the literature also shows hedging portfolios are managed by domestic coalitions — which motivates the sub-state actor layer.

**Shapiro & Varian, *Information Rules* (1998).**
Claim used: switching costs and installed base determine lock-in; lock-in is an asset the incumbent invests in. Design consequences: the E dimension's components (tenor, certification, data portability, availability of a *financed and licensed* alternative); the lock-in composite; contract-renewal cliffs as the natural experiment set for the prediction ledger.

**Bradford, *The Brussels Effect* (2020).**
Claim used: the EU exports regulation through market access, generating de facto and de jure alignment without infrastructure. Design consequences: regulatory pull coded as sovereign-vector exposure at the Models/Applications layers, weak at Silicon; adequacy decisions and AI Act transposition as coded events.

**Domino theory (as negative example).**
The original doctrine failed by treating states as passive, ignoring nationalism, elite factional politics, and hedging. It is retained as branding and as the model's null hypothesis: every cascade forecast is implicitly a bet against the hedging prior, and the published Brier score is the running verdict.

## Standing critiques the design must keep answering
Recorded so future maintainers do not re-litigate or quietly regress:

1. **P-collapse risk.** If Provenance sub-indicators go unmeasured and get proxied by vendor nationality, P collapses into D and the framework loses its differentiator. Standing answer: explicit nulls with `opacity_reason`, published coverage rates, no holistic P score. Regression test: if P–D correlation across countries approaches 1, the measurement has failed.
2. **Scalar vs. topology.** Any country scalar discards relational structure. Standing answer: scalars are derived, flagged, and decomposable to edges; the two-axis exposure view is first-class. Regression test: the UI must never present `alignment_index` without one-tap access to the exposure decomposition.
3. **Commensurability.** US and PRC leverage differ in kind (license chokepoints vs. debt entanglement). The signed axis is a reading aid, not a claim of equivalence.
4. **Gaming/Goodhart.** A published, ministry-read index invites manipulation, concentrated in Trajectory. Standing answer: Tier-1/2 caps, tier-composition display, verified-vs-announced value split. Watch for: summit-timed announcement clustering.
5. **Unitary-state fallacy.** Standing answer: sub-state entities on edges; `contested_decision` events.
6. **Falsifiability.** An index with no out-of-sample test is a coding scheme. Standing answer: pre-registered renewal-cliff predictions, published Brier scores (doc 04).
7. **Bundle reality.** Layer independence is false at the point of decision. Standing answer: `bundle_id` linkage; bundles as the unit in renewal analysis.
8. **Cadence noise.** High-frequency recomputation of slow stocks manufactures narrative. Standing answer: weekly publication cycle with continuous capture, class-specific score cadences, and out-of-cycle recomputes reserved for Tier-4/5 events and reversals (system prompt §4). Regression test: week-over-week score variance in D/E outside quarterly recompute windows should be ~zero.
