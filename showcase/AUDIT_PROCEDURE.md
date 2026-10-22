# Weekly showcase audit — procedure v1

The weekly scheduled audit follows this file exactly. It reproduces the supervised audit of
2026-10-22 (`AUDIT-2026-10-22.md`). Changing this file is Brian's decision; audit runs never
edit it, `SPEC.md`, `README.md`, `index.html`, `queue.json`, `data/` or `tools/`.

## What it may change

Pushed only with `bash showcase/tools/safe_push.sh audit`, whose guard allows exactly:

- one new report, `showcase/AUDIT-YYYY-MM-DD.md` (the run date);
- an appended entry in `showcase/RUNLOG.md`;
- corrections to existing `showcase/countries/<ISO3>.json` records. Each corrected record
  must keep every finding id, keep `coded_at` / `coded_by`, keep every earlier
  `corrections` entry unchanged and add one new entry, be set to
  `review_status: "audited"`, and pass `check_country.py`. Records Brian marked
  `"reviewed"` are never changed. At most 120 records per push.

If the guard refuses, do not work around it: fix the run's own changes if they are at
fault, otherwise stop and report the guard output.

## Scope

All records with `review_status: "unreviewed"`. If there are more than 80, audit the 80
earliest in `queue.json` `done` order and list the rest as carried over.

## Steps

1. **Mechanical.** Run `python3 showcase/tools/check_country.py` on every in-scope record;
   check that every code in `queue.json` `done` has a file and vice versa; read the RUNLOG
   entries since the last audit for problems the runs reported.
2. **Coding read-through.** Read every in-scope finding against `SPEC.md`: controller rule
   (equipment vendor vs owner-operator; fine-tuned national models are domestic),
   instrument tier vs the stage the summary describes, `reversal_reason`, `scale`,
   `still_current` for anything before 2020, enforcement coded as adoption, and the same
   situation coded differently across countries.
3. **Source verification.** Select every reversal, every tier 4–5 finding, every finding
   with a value, and a random 20% of the rest. Split them among independent sub-agents
   (about 25–30 findings each) that did not write them. Each checker may WebFetch a URL
   only after it appears in its own WebSearch results, never fetches Chinese-hosted sites
   (.cn, Baidu, Xinhua, PRC government or state media, PRC vendor sites), and returns for
   each finding: `supported`, `partly_supported`, `not_supported`, `source_unreachable`
   or `verified_elsewhere`, the problem, the URL it actually read, and any newer
   information (`possibly_stale`).
4. **Correct records.** Apply only:
   - factual fixes backed by a page a checker actually read (dates, numbers, names,
     stage/tier, summary wording);
   - recoding that the current `SPEC.md` already requires (it never invents new rules);
   - `still_current` from checker evidence, and newer information added to
     `analyst_inference` prefixed `Audit YYYY-MM-DD:`;
   - new sources only if a checker opened them (`opened: true`).
   Never upgrade `corroborated` or `confidence` by recounting; upgrade only when the
   audit itself opened the second source and adds it. A `not_supported` finding gets
   `confidence: "low"`, `pole: null` and an `analyst_inference` explaining why — it is
   not deleted. Add one `corrections` entry per record:
   `{"date", "audit": "AUDIT-YYYY-MM-DD.md", "ids": [changed finding ids], "note"}`, and
   set `review_status` to `"audited"` on every in-scope record, changed or not.
5. **Report** `showcase/AUDIT-YYYY-MM-DD.md`: scope; mechanical result; verdict counts;
   a table of every non-supported or stale item and what was done; coding problems found;
   **rule problems** — anything the spec gets wrong or leaves open — written as proposals
   for Brian, never applied; how the map colours changed for audited countries, with any
   result that looks wrong flagged as a judgment call.
6. **Push** in one commit `showcase: weekly audit YYYY-MM-DD (<n> records)` via
   `bash showcase/tools/safe_push.sh audit`.
