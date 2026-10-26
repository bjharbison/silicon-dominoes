# Silicon Dominoes

**Open-source intelligence on which AI technology stack every country is embedded in — and what those dependencies cost to exit.**

Silicon Dominoes tracks, for every country and territory, its exposure to the American, Chinese, and sovereign/third-pole AI stacks across seven layers: Power, Facilities, Silicon, Networks, Cloud, Models, and Applications. The output is an interactive world map and dataset in which every number is traceable to a dated, sourced, coded event.

The name is deliberate irony. The original domino theory failed because it treated states as passive tiles and ignored nationalism, elite politics, and hedging as strategy. This project keeps the branding and inverts the premise: hedging is the null hypothesis, cascade forecasts are bets against it, and a public prediction ledger with published Brier scores keeps the model honest.

## What makes this different

**Measurement over vibes.** The analytical framework (DEPTH-N v2.0) measures Dependence, Exit cost, Provenance and control, Trajectory, and Headroom per stack layer, formalized over a network of dyadic exposure edges rather than country-level scalars. Talk is cheap and scored that way: a signed MOU decays toward zero within two years, while operational infrastructure only retires by explicit reversal.

**Seeing and denying are different powers.** Following Farrell and Newman's weaponized-interdependence work, control is decomposed into observable sub-indicators split between panopticon effects (who can watch) and chokepoint effects (who can switch it off) — never summed into one number. Where contract terms are opaque, the data says *null* with a stated reason. Missing data is published as missing, never imputed into a score.

**Falsification with teeth.** The index is tested against vendor choice at contract-renewal cliffs — pre-registered, frozen quarterly test sets resolved against verifiable award records. Brier scores and baseline comparisons publish on the dashboard. If the model can't beat "the incumbent always wins" for four consecutive quarters, that finding is published and triggers a methodology review.

**Immutability and traceability.** Published scores are never edited; corrections append. Every cited source is snapshotted at capture time so citations survive link rot.

## DEPTH-N in brief

DEPTH-N is the measurement framework (full specification in `project-knowledge/01-methodology-depth-n.md`). Each of the seven stack layers in each country is scored on five dimensions:

| Dim | Question it answers | Type |
|---|---|---|
| **D** — Dependence | Whose kit is installed, by share? | stock |
| **E** — Exit cost | What would it cost to leave — contract tenor, encumbered debt, retrained workforce, data portability, whether an alternative actually exists? | stock |
| **P** — Provenance & control | Who can see the flows, who can switch them off, and under whose law? Published as separate panopticon and chokepoint sub-indicators, never summed. | chokepoint |
| **T** — Trajectory | Which way are new commitments pointing, and are they MOUs or built infrastructure? Always shown with its instrument-tier composition. | flow |
| **H** — Headroom | How much can the country absorb — power, compute, connectivity, human capital, capital, institutions? | country-level |

The **N** is the network: every score is formalized over dyadic exposure edges (country → controller → pole), so a country's exposure to the US, PRC, and sovereign/third-pole stacks is derived from its position in that graph, not entered as a scalar. Stocks (D, E) and flows (T) recompute on different cadences and are never averaged into one "movement" number.

## Repository map

| Path | Contents |
|---|---|
| `SYSTEM_PROMPT.md` | Operating constraints: evidence standards, source tiers, instrument ladder, prohibitions, publication contracts |
| `project-knowledge/` | Methodology docs: DEPTH-N v2.0 (01), academic lineage and standing critiques (02), DIMEFIL reporting crosswalk (03), prediction-ledger specification (04) |
| `ARCHITECTURE.md` | Full system design: collection, database, review workflow, scoring pipeline, public site, and build order |
| `schemas/` | JSON Schemas for the publication contracts, the `validate.py` CI gate, and fixture data — see its README |

## Status

Early foundation stage. The methodology, architecture, and data contracts are specified and versioned; no infrastructure is running yet. Build order, from `ARCHITECTURE.md` §14: contract schemas (done), database schema, collection layer, review UI, scoring pipeline, then the public map and API.

## Intellectual lineage

The framework is built on, and accountable to, Hirschman (1945) on asymmetric dependence, Farrell & Newman (2019) on weaponized interdependence, Kuik on hedging, Shapiro & Varian (1998) on switching costs, and Bradford (2020) on regulatory power. `project-knowledge/02-academic-lineage.md` records what each source actually claims and the standing critiques the design must keep answering.

## License and contact

Solo project by [@bjharbison](https://github.com/bjharbison). License to be determined before public data launch.
