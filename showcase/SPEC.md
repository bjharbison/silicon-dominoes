# Showcase desk pass — coding spec v0.1

This is the fixed instruction set for the global showcase desk pass. Every scheduled run
reads this file first and follows it exactly, so that the 200th country is coded the same
way as the first. Changing this file is Brian's decision; runs never edit it.

**What this is.** A static, global first pass to show what the dashboard looks like with
every country filled in. Every country record is a Claude draft with
`review_status: "unreviewed"` until Brian audits it. It is not pipeline output, it is not a
published cycle, and nothing here is ever ingested into the Postgres pipeline.

**What this is not.** No DEPTH-N scores, no exposure values, no postures, no D/E figures.
Records hold dated, sourced *findings* only. The map derives a display colour from the
findings with a fixed rule (`showcase/README.md` §Derived lean), clearly labelled as derived.

---

## 1. Hard rules (a run that breaks one of these has failed, however good its content)

1. **Only add.** A run creates new `showcase/countries/<ISO3>.json` files, edits
   `showcase/queue.json` (moving its ISO3 codes from `pending` to `done`) and appends to
   `showcase/RUNLOG.md`. Nothing else. It never deletes, renames, or edits any other file,
   never touches anything outside `showcase/`, and never edits a country file that already
   exists — even one it thinks is wrong (log that in RUNLOG.md instead).
2. **Push only through** `bash showcase/tools/safe_push.sh daily`. Never `git push`
   directly, never `--force`, never push to `main` or any branch other than `showcase`,
   never create or delete branches or tags, never open or merge pull requests. If the guard
   refuses, stop: do not try to work around it; record the refusal in the final message.
3. **No invented values.** Every finding cites at least one URL that the run actually
   fetched or saw in search results. Unknown is `null` with a reason. Never guess a URL,
   date, amount or counterparty.
4. **No Chinese-hosted sites first-hand.** Never fetch .cn domains, Baidu, Xinhua, People's
   Daily, CGTN, Global Times, or any PRC government or state-media site, and do not fetch PRC
   vendors' own corporate sites. Secondary reporting that relays them is fine; mark such a
   source `"relays_prc_state_media": true` and treat the claim as S4.
5. **Own words.** Summaries are paraphrased, at most 40 words. No quotations longer than a
   few words, and only where exact wording is material.
6. **At most 15 new country files per run** (the guard enforces this; aim for 10).

## 2. What to look for (the gap-fill layers)

The global layers in `showcase/data/` already cover bloc membership, US and PRC cloud
regions, PRC-model restrictions and US chip-export posture. **Do not re-research those.**
Read the country's rows in those files first, so you know what is already covered, then
spend the run's searches on these questions, most important first:

| Layer | Question | Typical evidence |
|---|---|---|
| networks | Who supplies the main mobile operators' radio/core network? Any ban, exclusion or rip-and-replace of Huawei/ZTE? PRC-built submarine cables (HMN Tech)? | regulator decisions, operator vendor awards, trade press |
| facilities | Large data-centre or AI-campus projects since 2023 — who controls them (controller's home jurisdiction, not the tenant's) and how far along (instrument tier)? | investment-agency releases, filings, DCD |
| silicon | Large accelerator purchases or deployments (Nvidia/AMD vs Huawei Ascend), export licences granted or denied for this country | company releases, BIS/licence reporting |
| models | National partnerships or government adoption of a frontier model provider (OpenAI, Anthropic, Google, Meta, Mistral, DeepSeek, Qwen, …); a sovereign national model | ministry releases, provider announcements |
| applications | Safe-city / surveillance systems (Huawei, Hikvision, Dahua, ZTE), e-government AI built on a foreign stack | procurement records, reputable investigative reporting |
| power | Only if specifically tied to AI/data-centre supply (e.g. a dedicated plant for an AI campus) | utility filings |
| (any) | **Reversals**: cancellations, bans, exclusions, licence denials, projects paused | these are high-information; never skip one you find |

Budget about 6–10 searches per country. Small states and territories with no evidence are
normal: code them quickly with nulls and move on. Prioritise S1/S2 sources.

## 3. Coding a finding

```json
{
  "id": "VNM-001",
  "layer": "networks",
  "pole": "prc",
  "controller": "Huawei",
  "local_actor": "Viettel",
  "instrument_tier": 3,
  "reversal": false,
  "reversal_of": null,
  "date": "2025-06",
  "summary": "Own-words description, at most 40 words.",
  "announced_value_usd": null,
  "verified_value_usd": null,
  "sources": [
    {"url": "https://...", "publisher": "DatacenterDynamics", "source_tier": "S2",
     "date": "2025-06-12", "retrieved_at": "2026-10-20T07:10:00Z"}
  ],
  "corroborated": false,
  "confidence": "medium",
  "analyst_inference": null
}
```

- **layer**: one of `power, facilities, silicon, networks, cloud, models, applications`.
- **pole**: the *controller's* pole — who can switch it off or update it — not the tenant's
  or buyer's nationality. `us`, `prc`, `third` (another foreign controller: EU, Japan, Korea,
  Gulf, India…), or `domestic` (controller is from this same country = sovereign). A US
  model hosted by a Gulf firm in Kenya: the model finding is `us`, the facility finding is
  `third`. If control is genuinely unclear, `pole: null` and say why in `analyst_inference`.
- **instrument_tier** (from SYSTEM_PROMPT §2.1): 1 statement/MOU/LOI · 2 framework, pilot,
  feasibility study · 3 signed contract or binding award (needs counterparty, ideally value)
  · 4 financing closed, construction started, licence issued · 5 operational at scale,
  migration complete. Code what the evidence shows, not what was promised.
- **reversal**: `true` for cancellations, bans, exclusions, denials, pauses; then
  `instrument_tier` is the tier of the thing reversed and `reversal_of` names it, and
  `pole` is the pole of the thing reversed (a Huawei ban is `pole: "prc", reversal: true`).
- **source_tier**: S1 primary (government, regulator, gazette, company filing/IR, court) ·
  S2 specialist trade press · S3 general press, wires · S4 state-affiliated media of any
  government, corporate marketing, think-tank advocacy · S5 aggregators, social, blogs —
  S5 may be used to find leads but is **never cited** in `sources`.
- **corroborated**: `true` only if there are ≥2 independent sources of tier S3 or better,
  at least one S1 or S2, and they are not two state announcements. Tier ≥3 findings that are
  not corroborated stay in the record but must carry `"confidence": "low"`.
- **values**: `announced_value_usd` is what was announced; `verified_value_usd` only from a
  filing, award notice or audited figure. Convert at the rate on the source date and say so
  in `analyst_inference` if converted. Otherwise null.
- **analyst_inference**: any judgment beyond what the sources state goes here, labelled,
  never in `summary`.
- Bundles: if one deal spans layers (e.g. a DSR package with cloud + network + financing),
  code one finding per layer and give them the same `"bundle_id": "<ISO3>-B1"`.

## 4. The country record

```json
{
  "schema": "sd-showcase-country/0.1",
  "spec_version": "0.1",
  "iso3": "VNM",
  "name": "Vietnam",
  "review_status": "unreviewed",
  "coded_at": "2026-10-20T07:30:00Z",
  "coded_by": "claude scheduled desk pass",
  "findings": [ ... ],
  "layers_searched": {
    "networks": {"result": "coded"},
    "facilities": {"result": "coded"},
    "silicon": {"result": "null", "null_reason": "no public accelerator purchases found in 4 searches"},
    "models": {"result": "coded"},
    "applications": {"result": "null", "null_reason": "..."},
    "power": {"result": "not_searched", "null_reason": "no AI-specific power evidence surfaced"}
  },
  "research_gaps": ["specific questions a human should chase"],
  "searches_run": 8
}
```

`layers_searched` must list all six gap-fill layers (cloud is covered by the global layers).
`result` is `coded` (≥1 finding), `null` (searched, nothing found — give the reason), or
`not_searched` (give the reason). An empty `findings` array is valid and honest.

Write JSON with 2-space indentation, UTF-8, keys in the order shown.

## 5. A run, step by step

1. Clone the repo, check out `showcase`, read this file and `showcase/README.md`.
2. Read `showcase/queue.json`; take the first 10 ISO3 codes in `pending` that have no file
   in `showcase/countries/`. If `pending` is empty, write nothing, report "queue complete".
3. For each country: read its rows in `showcase/data/*.json`, research §2, write the record.
4. Validate: `python3 showcase/tools/check_country.py showcase/countries/<ISO3>.json` for
   each new file; fix the record (never the checker) until it passes.
5. Move the codes from `pending` to `done` in `queue.json`; append one RUNLOG.md entry
   (date, codes, findings count per country, notable reversals, problems).
6. One commit, message `showcase: desk pass <codes>`, then
   `bash showcase/tools/safe_push.sh daily`.
7. Report: countries coded, notable findings, guard result, anything skipped.
