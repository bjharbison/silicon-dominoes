# DOC 03 — DIMEFIL REPORTING VIEW AND CROSSWALK
Status: Reference · Read when: generating the DIMEFIL briefing view or any product for government audiences.

## Principle
**Measure in DEPTH-N, report in DIMEFIL.** Government readers speak DIMEFIL fluently; the dashboard offers it as a *reporting projection* of the underlying measurement. The projection is lossy by design, and the loss is disclosed rather than hidden: every DIMEFIL-view export carries the watermark **"Briefing projection — lossy mapping from DEPTH-N v2.0; see methodology doc 03"** and links the crosswalk. If the crosswalk were lossless the two frameworks would not be meaningfully distinct; because it is lossy, the DIMEFIL view is never the system of record and no score is ever computed in DIMEFIL space.

## Crosswalk table
| DIMEFIL pillar | Sourced from (DEPTH-N) | What the projection loses |
|---|---|---|
| **D** Diplomatic | T-flow events at governance "layer-adjacent" venues: WAICO/Pax Silica membership, UN/ITU/SC42 positions, AI dialogues | Instrument-tier decay; the Goodhart cap context |
| **I** Information | D + `p_telemetry`/`p_jurisdiction` at Models and Applications; app penetration; smart-city deployments | The panopticon/chokepoint split |
| **M** Military | All DEPTH dims filtered to defense end-use tags; security-agreement riders → `p_license` | Bundle linkage to civilian layers |
| **E** Economic | D and T at Silicon, Networks, Facilities; trade and capex flows | Stock/flow separation (E-pillar readers see one number) |
| **F** Financial | Creditor identity on exposure edges; T financing-source attribute; debt-secured component of E | Dyadic creditor concentration (HHI) |
| **I** Intelligence | `p_jurisdiction` + `p_telemetry` across layers; CERT and cyber-cooperation events | Sub-indicator nulls and coverage rates |
| **L** Legal/Regulatory | `p_license` regime, localization law, adequacy/AI Act events; sovereign-vector regulatory coefficient | Layer-specificity of the Brussels effect |
| **A** Absorptive | H (readiness) verbatim | `strategic_salience` (report it alongside, not inside) |

## Rules for the reporting view
1. DIMEFIL pillar numbers are regenerated from DEPTH-N at render time; they are never stored as independent scores and never accept direct edits.
2. Any briefing that quotes a DIMEFIL pillar must be able to expand it to its DEPTH-N sources on request; the export bundles the event citations.
3. The legacy v1 pillar-weight sliders survive only inside this view and affect only this view.
4. When a reader decision would differ between the DIMEFIL projection and the DEPTH-N measurement (known cases: hedgers with high D-pillar diplomatic warmth but low installed dependence; states with identical E-pillar economics but opposite `p_license` status), the view must surface a divergence flag. These divergence flags are themselves useful analytic output — log them.
