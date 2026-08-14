# DOC 04 — PREDICTION LEDGER SPECIFICATION
Status: Active · Read when: writing any forecast, resolving predictions, computing published Brier scores, or evaluating whether the index is working.

## Purpose
The ledger is the falsification surface for the whole system. It answers the standing critique that an index without out-of-sample tests is a coding scheme. Two prediction classes are maintained; both produce published Brier scores on the dashboard.

## Class A — Cascade predictions
Every cascade edge emitted with the projection overlay generates a ledger entry:
```
{ prediction_id, created_at, class: "cascade",
  claim: "<country> will <specific observable> by <date>",
  mechanism: "<concrete channel — interconnector, creditor, bundle, sub-state actor>",
  probability: 0.0–1.0,
  resolution_criterion: "<the S1/S2-verifiable event that resolves this>",
  resolution_date, resolved?, outcome?, brier_contribution? }
```
Rules: no prediction without a dated, source-verifiable resolution criterion; probabilities respect the hedging prior (a mid-readiness state's modal outcome is multi-sourcing, not flipping); mechanism strings that amount to "neighbors correlate" are rejected at coding time.

## Class B — Index predictions (pre-registered, standing)
The index itself is tested against **vendor choice at contract-renewal cliffs** — the cleanest recurring natural experiment: a real decision, a datable outcome, an S1-verifiable award.

Standing protocol:
1. Each quarter, enumerate all known renewal/refresh cliffs in the next 18 months (RAN contracts, cloud tenders, DC phases, financing rollovers) from the E-dimension tenor data. This is the **test set**; it is frozen at enumeration.
2. For each cliff, the model emits a probability distribution over outcomes {incumbent retained, switch to US pole, switch to PRC pole, switch to sovereign/third, split award, cancellation/delay} derived mechanically from the country's DEPTH-N state (E, chokepoint-P, T composition, contest_score, active competing offers). The derivation formula is versioned; analysts may add a separate judgment forecast, logged as its own entry, so model skill and analyst skill are scored apart.
3. Resolution requires an S1 or two-source S2/S3 award record, per the corroboration rule.
4. Quarterly: publish Brier score, calibration curve, and skill relative to two baselines — (a) "incumbent always retained" and (b) the hedging-prior base rates. **If the model cannot beat baseline (a) over four consecutive quarters, that finding is published and triggers a methodology review** — this commitment is the anti-domino discipline with teeth.

## Class C (lightweight) — Chokepoint exercise watch
Farrell-Newman distinguishes holding a chokepoint from using one. Log every observed *exercise* event (license denial, cutoff, forced divestment, data-access order) as a ledger observation (not a forecast). These calibrate how much weight `p_license`/`p_update` deserve: chokepoints never exercised in a domain argue for discounting them; a wave of exercises argues the opposite. Review at each methodology revision.

## Publication
`ledger.json` per system-prompt §5: all open predictions, resolutions this period, running Brier by class, calibration data, and the baseline comparisons. The dashboard renders Brier scores adjacent to the cascade overlay toggle — a reader turning on projections sees the track record first.

## Immutability
Ledger entries are append-only. A prediction may be superseded (new entry referencing the old) but never edited or deleted. Superseded-before-resolution entries still resolve and still score.
