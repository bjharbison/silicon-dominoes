# Global showcase (draft)

A static, global first pass at the Silicon Dominoes map: every country, every layer that
can be filled from global source lists, plus a country-by-country desk pass that fills the
gaps ten countries a day. Built to show what the finished dashboard looks like. **Every
value here is an unreviewed Claude draft until Brian audits it.**

Open it at `showcase/index.html` on GitHub Pages once the branch is merged, or locally with
`python3 -m http.server` from the repo root (it fetches its JSON, so `file://` won't work).

## Relationship to the rest of the repo

- Lives entirely in `showcase/`. It never edits `map.html`, the schemas, the collector or
  anything else, and nothing here is ingested by the pipeline.
- It deliberately departs from `CLAUDE.md` §6 ("agents do not code events"): Brian chose on
  2026-09-23 and 2026-10-19 to have Claude draft codings for a showcase, shown as unreviewed.
  The departure is contained: these are draft *findings*, not DEPTH-N scores; there are no
  exposure values, postures or D/E figures; the map's colour is a labelled mechanical rule.
- The pipeline's guarantees (corroboration enforced in review, immutable events, Brier
  ledger) do not apply here. If a showcase finding is worth keeping, it goes through the
  review queue like any other lead.

## Files

| Path | What | Who writes it |
|---|---|---|
| `SPEC.md` | coding rules every desk-pass run follows | Brian (Claude drafts) |
| `data/diplomatic_alignment.json` | WAICO / Pax Silica membership | build session |
| `data/us_cloud_regions.json` | AWS / Azure / Google Cloud regions | build session |
| `data/prc_cloud_regions.json` | Alibaba / Huawei / Tencent Cloud regions | build session |
| `data/prc_model_restrictions.json` | government restrictions on PRC models | build session |
| `data/us_chip_export_posture.json` | US advanced-chip export licence posture | build session |
| `data/country_names.json` | display names | build session |
| `countries/<ISO3>.json` | desk-pass record per country | daily runs (add-only) |
| `queue.json` | which countries are pending / done | daily runs |
| `RUNLOG.md` | one entry per run | daily runs (append) |
| `tools/guard.py`, `tools/safe_push.sh` | push guard (see below) | build session only |
| `tools/check_country.py` | record validator | build session only |

## Safety model

Unattended runs can only add. `tools/safe_push.sh daily` runs `tools/guard.py --mode daily`
before every push, which refuses if the push would delete or rename any file, touch any
path outside `showcase/`, rewrite published history, modify an existing country record,
change anything other than new country files plus `queue.json` / `RUNLOG.md`, or add more
than 15 countries. The push is a plain fast-forward of `showcase` only. `main` is frozen at
`backup/main-2026-10-19` as an extra copy. A GitHub ruleset blocking deletions and force
pushes on all branches is the backstop that does not depend on any of this code.

## Derived lean (the default map colour)

Display-only, recomputed in the browser from the files above, never stored.

1. Each piece of evidence adds weight to the pole of its **controller**:
   instrument tier weights T1 0.15 · T2 0.35 · T3 0.70 · T4 0.85 · T5 1.00.
   - desk-pass finding: its tier weight to its `pole` (`domestic` and `third` both count as
     *sovereign/third*); halved if `confidence` is low; **negative** if `reversal: true`.
     Rule v0.2 (after `AUDIT-2026-10-22.md`): only reversals with `reversal_reason`
     `geopolitical` or `sanctions` count (permit pauses, business decisions and law
     enforcement do not); `municipal` findings count half; non-reversal findings dated
     before 2020 count half unless `still_current: true`, and zero if `still_current: false`.
   - US cloud region live (any of AWS/Azure/GCP): US +1.00; announced only: US +0.35.
   - PRC cloud region live (any of Alibaba/Huawei/Tencent): PRC +1.00; announced only: PRC +0.35.
   - Bloc membership: +0.15 to that bloc's pole.
   - Restriction on PRC models in force: national block PRC −0.70; government devices or a
     sector PRC −0.35.
   - US chip-export posture is **not** counted: it is a US decision about the country, not the
     country's own choice. It has its own layer.
2. Goodhart cap: positive Tier-1/2 contributions to any one pole are capped at 0.30 in total.
3. Pole totals below zero are shown as zero.
4. Cell: total < 0.70 → **insufficient data** (hatched, never neutral). Otherwise, if US and
   PRC each hold ≥ 30% of the total → **contested**; else if one pole holds ≥ 50% →
   **leans US / PRC / sovereign-third**; else **mixed**.

5. The United States and China are shown as the two poles, not scored: hosting the
   other side's cloud regions would otherwise make the US look contested.

Until a country's desk pass lands, its lean rests only on the global layers, which
over-weight hyperscaler presence and under-count sovereign and PRC network stacks. The
"desk pass" layer shows which countries have been coded.
