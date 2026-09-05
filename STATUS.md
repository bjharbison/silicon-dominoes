# Silicon Dominoes — Project Status
Last updated: 2026-09-05 (fifth session, end of day) · Purpose: running record of what's built, what's live, and what's next. Update this file at the end of each work session — **and commit it.** This file was not tracked in git until 2026-09-05; see that session's entry.

## Where things stand

**Repo:** github.com/bjharbison/silicon-dominoes — live, public. Contains the methodology docs (`project-knowledge/`), `SYSTEM_PROMPT.md`, `ARCHITECTURE.md`, a public-facing root README, and the build artifacts below.

**Live site:** https://bjharbison.github.io/silicon-dominoes/ — GitHub Pages, enabled and working. Dashboard is **v5** (Simple/Advanced mode), pushed and live as of the fourth session. `index.html` is a meta-refresh redirect to `map.html`, so the bare URL opens the dashboard; the README renders on GitHub itself. Repo About sidebar links the Pages site. **Operational note: Pages deploys lag up to ~10 minutes behind commits (CDN cache) — hard-refresh (Ctrl+Shift+R) or append `?v=N` before troubleshooting a "stale" deploy.**

**Collection layer live:** CT 109 `dominoes` at 192.168.1.204, Postgres 17 with all T1–T11 guarantees verified, five systemd timers active. 225 captures across five feeds (four RSS + GDELT), still growing on schedule. URL-dedup and gzip patches applied 2026-08-16. Extraction has run: 191 `review_queue` rows, 6 pending candidates — but two of four RSS feeds are effectively contributing nothing (see feed diagnosis).

**Build order progress (ARCHITECTURE.md §14):**

| Step | What | Status |
|---|---|---|
| 1 | JSON Schemas for the publication contracts (`schemas/`) | ✅ Built, committed, ✅ **verified 2026-08-16 (fourth session).** `validate.py fixtures` returns `OK — all contracts valid; cross-field checks passed` against live jsonschema 4.18 in CT 109. Remaining: `must-reject` fixtures (see open items) |
| 2 | Postgres schema (`db/`) | ✅ Built, ✅ **deployed and verified on 2026-08-16.** All eleven guarantee tests (T1–T11) PASS on live Postgres 17 in CT 109 |
| 3 | Collection layer (`collection/`) | ✅ Built, ✅ **deployed and running on 2026-08-16.** Five systemd timers active; URL-dedup fix verified live (three feeds still fail with real XML parse errors — see open items) |
| — | Dashboard (`map.html`, v5) | ✅ Built third session, ✅ **pushed and live 2026-08-16 (fourth session).** Simple/Advanced mode toggle, plain-language Overview, facility-layer earmark. Version check: `grep -c "SIMPLE_LEXICON" map.html` → 6 for v5, 0 for v4 |
| 4 | Review UI + LLM candidate-event extraction | **4a built and run.** `collection/collector/extract.py` (586 lines, `c11d750`); 191 `review_queue` rows, 6 pending candidates, first real machine-coded event captured. Blocked on two feed defects (see diagnosis). Review UI not started |
| 5 | Scoring pipeline + publication of the five contracts | Not started |
| 6 | Public frontend (full version) | v5 live |
| 7 | Public API, exports, PDF reports | Not started |
| 8 | Backfill / shadow-replay tooling | Not started (needed by first methodology revision, not launch) |

## Deployment session (2026-08-16, first session)

The full collection-layer deployment ran in one session. Key facts to preserve for future recovery:

- **CT 109 is privileged, not unprivileged.** Originally created unprivileged per the homelab principle, but unprivileged LXCs cannot bind-mount NFS from the host with dominoes-writable permissions (uid mapping refuses writes even to `chmod 777` directories, and Synology's Advanced ACLs override POSIX). Converted to privileged via `vzdump` → `pct destroy` → `pct restore --unprivileged 0`. Backup file: `/var/lib/vz/dump/vzdump-lxc-109-2026_08_15-19_57_21.tar.zst` (kept as safety net).
- **Raw archive is on LOCAL CT storage, not the NAS.** After multiple rounds fighting Synology's ACL model refusing writes even after `chmod 777` and squash rules were set, `SD_ARCHIVE_DIR` was changed to `/var/lib/silicon-dominoes/archive` (on CT rootfs). The NAS bind-mount at `/mnt/nas-archive` still exists but is unused. The CT's vzdump backup covers the archive indirectly. **Moving the archive to the NAS is a deferred TODO** — needs a fresh session with either the ACL disabled or a different mount approach.
- **Synology NFS state:** `/volume1/silicon-dominoes` share exists with allow-list `192.168.1.0/24`, squash "No mapping", non-privileged ports allowed. Rule verified via `showmount -e 192.168.1.163` on Proxmox host. Host-side mount at `/mnt/pve/silicon-dominoes-archive` is persistent in `/etc/fstab`. Container bind-mount at `/mnt/nas-archive` exists but pipeline no longer writes there.
- **T1–T11 all PASS.** First live run of the step-2 schema tests: corroboration enforced, UPDATE/DELETE rejected on events, corrections require reference, reversals require target+tier, temporal country_scores appends, shadow rows filter out, ledger append-only with separate resolutions, cascade edges reject "neighbors correlate" and require ledger refs, frozen cliffs undeletable. Test transaction rolled back cleanly — database untouched.
- **Peer auth working.** Service user `dominoes` connects to Postgres over unix socket with no password anywhere in the system. Root cannot connect (no root role) — by design.
- **Five timers scheduled:** sd-gdelt (every 6h), sd-rss (every 2h), sd-snapshot-retry (every 6h), sd-verify (daily 07:10 UTC), sd-health (daily 08:05 UTC). All load `/etc/silicon-dominoes/collector.env` via `EnvironmentFile=` so config changes propagate without service edits.
- **First cycle results:** 20 datacenterdynamics + 50 lightreading + 50 e27 + 60 mic-vn = 180 captures. Three feeds failed with real XML parse errors: rss-techwireasia, rss-developingtelecoms, rss-imda-sg (not permission problems — malformed feeds).
- **Dedup anomaly observed** — diagnosed and fixed in the second session; see below.

## Dedup and archive-compression session (2026-08-16, second session)

**The anomaly was real, and the diagnosis matters for anyone maintaining the collector.**

`raw_captures` deduped on `UNIQUE (feed_id, sha256)` — content hash. But news pages re-render with per-request identifiers on every fetch: `data-ruid` attributes on every meta tag, and Cloudflare's email-obfuscation cipher, which re-keys each render. Two captures of the same Light Reading article 12 hours apart differed in *nothing* editorial and still produced different hashes. Roughly 90 duplicate rows accumulated in ~1 day. Left alone this would have grown without bound, distorted coverage metrics, and doubled every candidate event in the review queue.

**Fix (three parts).** *Correction (fourth session): these were written but NOT committed — see "Commit and sync session" below. They are committed as of `c11d750`.*

1. `common.url_already_captured(conn, feed_id, url)` — index-backed existence check.
2. `poll_rss.poll_feed` calls it **before** `fetch_article`, so a known URL costs neither an HTTP round-trip nor a Wayback submission. Gated on a per-feed `dedupe_on` key (default `url`).
3. `collection/sql/003_url_index.sql` — `CREATE INDEX idx_raw_captures_url ON raw_captures (feed_id, url)`. **Non-unique deliberately:** historical duplicates already exist and cannot be deleted (immutability trigger), so a UNIQUE index would fail to build. Uniqueness is enforced in the poller, not the schema.

**Verified live:** poll skipped 178 already-captured URLs, captured only the 2 genuinely new articles, and completed in seconds rather than minutes.

**Archive gzip.** `write_archive` now writes `gzip.compress(payload)` with a `.html.gz` object key. Measured cost before the change: 58 MB / 220 captures ≈ **264 KB per article**, almost all framework markup around ~3 KB of content. Projected ~36 GB/year uncompressed at full feed volume; ~6 GB/year gzipped. **Readers must handle both extensions** — objects captured before 2026-08-16 are plain `.html` / `.json` and are not being rewritten.

**Historical duplicates are permanent.** `trg_raw_captures_immutable` blocks DELETE, and that trigger is a T-guarantee. Cleaning them would trade a load-bearing invariant for tidiness. They stay as an honest record of what the collector actually did; **the extractor must filter with `DISTINCT ON (feed_id, url) ... ORDER BY feed_id, url, capture_id`** (earliest capture wins).

## Commit and sync session (2026-08-16, fourth session)

**The finding that mattered: two sessions of collection work existed only as an uncommitted working tree inside CT 109.** `git status` in the container showed `common.py`, `config.py`, `poll_rss.py`, and `feeds.yaml` modified but uncommitted, with `extract.py` and `facets/entities.yaml` untracked entirely. HEAD was still at `2b638e5 updated map`. Nothing from the dedup or gzip work was in git history, let alone on GitHub — vzdump was the only thing between the project and losing it. STATUS.md said "all committed"; it was not. **Check `git status` in the CT at the end of every session that touches `collection/`.**

Also caught: `collection/sql/003_url_index.sql` had never been written as a file. The index existed only in the live database, applied directly via psql. A rebuild from the repo would have silently come up without it and the dedup fix would have degraded to a sequential scan. The file now exists, carrying the non-unique rationale as a comment.

**What was done:**

- Git identity configured for the `dominoes` user (`harbisonbrian@protonmail.com`).
- `.gitignore` added (`__pycache__/`, `*.pyc`, `.venv*/`).
- `003_url_index.sql` written to match the live index.
- Eight files committed in the CT, then transferred to Windows and pushed via GitHub Desktop. `origin/main` moved `2b638e5` → `c11d750`.
- CT reset to `origin/main`; both checkouts back in sync at `c11d750`.
- Verified on GitHub by byte count from the fetched objects: `extract.py` 24183, `common.py` 5299, `config.py` 2661, `poll_rss.py` 3390, `feeds.yaml` 3668, `entities.yaml` 6385, `003_url_index.sql` 485, `.gitignore` 27.

**Step 1 verification closed.** A throwaway venv at `/opt/silicon-dominoes/.venv-validate` (gitignored) runs `validate.py`; all four contract fixtures pass schema and cross-field checks. Invocation: `pct exec 109 -- su - dominoes -c 'cd /opt/silicon-dominoes/schemas && ../.venv-validate/bin/python validate.py fixtures'`.

**Transfer mechanics (RETIRED 2026-09-05 — CT 109 is now pull-only; see fifth session. Kept for the record.)** The CT has no GitHub credential and `dominoes` cannot push. The route used was `git archive --format=zip -o /tmp/sd-changes.zip HEAD .gitignore collection` → `pct pull` → `scp` to Windows → extract over the checkout → commit in GitHub Desktop. Gotchas:

- **The Explorer "Replace or Skip Files" dialog must be answered "Replace the files in the destination."** First attempt skipped it, and only the two files at paths that did not already exist were written — Desktop showed 2 changed files instead of 8.
- `git archive` includes only tracked files, so `__pycache__` and venvs are excluded automatically.
- Windows hides extensions: `sd-changes` on the Desktop is the .zip, not an extracted folder. `dir sd-changes\...` fails accordingly.
- The CRLF warning in GitHub Desktop is expected and harmless — `.gitattributes` normalizes line endings, which is why the diff was 2 real files rather than 28 phantom ones.
- **This shuttle only works while the CT owns `collection/` and Windows owns `map.html`.** If both ever edit the same file, overwrite-extract silently loses one side. That is the argument for giving the CT a scoped push token at step 5. *(Resolved the other way on 2026-09-05: the CT was demoted instead of promoted.)*

## Repo ownership consolidation (2026-09-05, fifth session)

**Purpose:** land the prerequisite for any Claude Code agent task — one author (the Windows/Spectre checkout), CT 109 demoted to a pull-only deploy target, the `git archive → pct pull → scp → GitHub Desktop` shuttle retired.

**Findings on the way in:**
- **The CT was one commit ahead of GitHub again.** `facets v1.1` (Vietnamese geo forms; geo/entity duplicate removal; ministry-name exclusion) existed only in the container, and GitHub had two commits the CT lacked (`CLAUDE.md`, a `map.html` update). Non-overlapping files, so a rebase was clean. Second time this failure mode has bitten; it is now structurally closed.
- **STATUS.md was never tracked.** `git ls-files` on both checkouts showed no `STATUS.md`; the only copy was the 2026-08-16 version in the Claude Project knowledge. Committed this session.
- **Command-line git on Windows was authenticating as a different GitHub account** (403 on push); GitHub Desktop had its own working credential, which is why earlier pushes succeeded. Fixed with `git config --global credential.https://github.com.username bjharbison`; `git push` now prompts for and caches the right account. Matters because Claude Code shells out to `git`.
- Windows checkout path is nested oddly: `C:\Users\harbi\Documents\GitHub\chessmasterAI\silicon-dominoes\silicon-dominoes`. Works; just not where anyone would look.

**What was done:**
1. CT commit rebased onto `origin/main`, exported with `git format-patch -1`, moved via `pct pull` → `scp`, applied on Windows with `git am` (preserves message and author; cannot silently skip files the way the zip-extract did). Pushed as `0fe76b4`.
2. CT reset to `origin/main` (`0fe76b4`). Its push URL set to `DISABLED` (`git remote set-url --push origin DISABLED`), so a push from the container fails by construction, not by absence of a credential.
3. Pull-only deploy script at `/home/dominoes/bin/sd-deploy` in CT 109: refuses with exit 1 if `git status --porcelain` is non-empty, otherwise `fetch` + `reset --hard origin/main`. **The refusal is the guard** — an edit made in the CT now breaks the deploy loudly instead of being destroyed by the reset. Verified by deliberately dirtying the tree.

**New operating rule:** edit on Windows → push → `pct exec 109 -- su - dominoes -c '~/bin/sd-deploy'` on the Proxmox host. The CT never authors. The `collection/.venv` and `/etc/silicon-dominoes/collector.env` are outside the repo and unaffected by the reset.

**Later same day — both prerequisites closed, first agent task accepted:**
- **`PreToolUse` guard landed as `b576eff`.** `.claude/settings.json` registers `.claude/hooks/guard-bash.sh` on the Bash tool; it blocks `psql`, `pg_dump`, `su - postgres`, `pct …`, `ssh user@`, any `192.168.1.x` address, `sd-deploy`, `systemctl`, and `collector.env`. Exit 2 with the reason on stderr, which Claude Code feeds back to the agent. **SQL text is deliberately not blocked** — writing numbered `.sql` files is the agent's job; executing them is Brian's, as `postgres` inside CT 109. `.gitattributes` pins `*.sh` to LF so the script survives Windows checkout. Verified live in the desktop Code tab: the agent's `echo 192.168.1.204` was blocked with the message surfaced; `/hooks` listing is terminal-only and says nothing about whether the hook loads.
- **First Claude Code agent task accepted — `3b016ee`.** `validate.py` now rejects `synthetic: true` / `provisional: true` envelopes with an explicit check and its own message (containment is intentional, not incidental via `additionalProperties`); `schemas/fixtures/must-reject/` holds the desk-pass file and a minimal synthetic envelope; `--self-test` fails if any must-reject case validates. The agent respected the guard when it hit it and ran the test via WSL Ubuntu (Python 3.14) because **Windows Python on Spectre is 3.8 without `jsonschema` — use WSL for `validate.py` locally.** Agent committed nothing; Brian reviewed the diff, committed on the task branch, fast-forward merged, pushed, deployed.
- **Agent-task loop as run:** open Claude Code (desktop Code tab, Local, folder = checkout, `main`, worktree off) → paste task with "done = passing test, don't commit" → `git diff --stat main` + rerun the test yourself → keep (`commit`, `checkout main`, `merge --ff-only`, `push`, `sd-deploy`) or discard (`branch -D`).
- Observed: agent output linked a commit under a different GitHub account's URL (`VerdunHere/…`), presumably inferred from another login on the machine. Remote is `bjharbison`; pushes confirm it. Treat agent-generated links as unverified.

## Gap record: 2026-08-16 → 2026-09-05 (reconstructed, not contemporaneous)

Sessions in this window did not update this file (it was not in git). The following is reconstructed from commit history and Project memory and should be treated as a summary, not a session log. Items marked † are decisions recorded in project-knowledge docs or memory rather than in this repo.

- **Phase 1 intake work:** `facets/entities.yaml` bumped to v1.1 (commit `0fe76b4`): Vietnamese-form geo terms added; geo/entity duplicate terms removed; ministry-name exclusion recorded. Status of the NFC/AND-gate measurement, `Viettel`/`VNPT`/`FPT` geo→actors move, `facets_version` re-examination mechanism, `rss-e27`, and `trafilatura` in requirements: **check against the file and the queue before assuming any are done.** Known: `rss-mic-vn` points at a staging host (`emicweb.dev.cnnd.vn`) that cannot support durable citations.
- **`CLAUDE.md`** added to repo root (commit `2180466`). Keep under ~200 lines; constraints, not a copy of the docs.
- **`map.html`** updated (commit `c47f15c`). Contents not recorded here — diff against `2180466` if it matters.
- † **Doc 07 F-7 (pole resolution)** drafted: `pole` enum narrowed to `us | prc | third`; `sovereign` removed as a registry-level pole and recast as an edge-level derivation (controller jurisdiction matches exposed country ISO3). Hard rule: no exposure field may be a residual of others. Merged into the F-2 breaking pass. Not yet in `project-knowledge/` in this repo — confirm.
- † **Source-tier decision:** data-centre directory aggregators (Data Center Map, Baxtel, datacenterHawk) are S5 — private `discovery_index` in CT 109 only; never exported, never joined to scores, never in `evidence_refs[]`. `validate.py` has no gate for the S5 floor yet; `check_source_tier_floor` fixture is the mechanism when the facility layer is built.
- † **H sub-index indicators** agreed (UNESCO UIS researchers/million, GERD % GDP, ISCED-8 graduates in relevant fields, WIPO IPC G06N resident filings, OECD triadic families); US and PRC as full country records under the global-expansion tier. Publishing H sub-indices individually belongs in the Phase 2 pass.
- † **D/E stock baseline path agreed:** hand-code a nine-country baseline against S1/S2 through the review queue with corroboration enforced, published as cycle zero. Analyst work; no agent produces D/E values.
- † **BrightRay MY-01** (Sedenak, Johor) researched; controller attribution unresolved; US$600–700M convertible financing creditor unnamed — standing `research_gap`.
- † **Open verification:** WAICO founding date conflict (system prompt 16 July vs Jamestown 17 July 2026) — resolve against S1 and flag in changelog before the announcement post's membership claims publish.

## Dashboard v4 (live)

Superseded v3 with seven-layer drill-down across the three surfaces:

1. **Charts layer filter.** "All layers" shows the current composite exposure view (scatter, sorted bars, side-by-side bars). Picking a specific layer (power / facilities / silicon / networks / cloud / models / applications) re-renders the first three charts from that layer's D vector as installed-base shares, with an amber "INSTALLED-BASE (D) VIEW" chip. Composite per-layer exposure scores are pipeline outputs from edges (not in countries.json), so the UI never invents them — a deliberate constraint. Lock-in, contest, and readiness charts stay country-level.
2. **Heatmap cells click-through.** Any evidenced heatmap cell (Vietnam × Networks, etc.) opens the layer detail card in the side panel — D shares by pole, E per incumbent pole, all five P sub-indicators with `opacity_reason` inline (chokepoint and panopticon visually separate, never summed), and T by pole with tier composition.
3. **Table row expansion.** New chevron on each country row opens a seven-layer strip; layer rows click through to the same detail card.
4. **Layer tabs inside the detail card** switch layers without going back; "← full record" backlink returns to the country view. Layers with no data are disabled/greyed.

All spec display rules survive at layer level: evidenced/null P dots with opacity_reason, chokepoint|panopticon never summed, hedged_active/inert never share a color, insufficient-data hatched and never neutral, derived flags on alignment, methodology + weight stamp in header.

**Quick version check:** `grep -c "INSTALLED-BASE" map.html` returns 1 for v4, 0 for v3.

## Dashboard v5 — Simple/Advanced mode (2026-08-16, third session)

Adds a lay-reader mode on top of v4 without touching the advanced surfaces. Built via anchored string-splice (10 edits), `node --check` clean, headless smoke tests pass.

1. **Mode toggle** in the header: Simple (default for new visitors) / Advanced (v4 exactly, untouched). Choice persists via localStorage. Switching modes re-renders the side panel in place.
2. **Overview tab** (Simple mode only, replaces Charts/Table in the nav): sorted country cards — posture chip in the standard palette, generated summary sentences, US/China exposure bars with numbers, confidence dots. Insufficient-data countries render as hatched "doesn't have enough sourced evidence" cards, never neutral.
3. **Simple side panel:** summary sentences + "the two numbers behind the label" (US/China exposure bars) + a "Show full record → Advanced" button. This satisfies doc 02 regression test 2 in both modes: the lean label is never shown without its exposure decomposition one tap away.
4. **`SIMPLE_LEXICON` v0.2** embedded and versioned: posture→phrase, lock-in bands, talk-vs-done trajectory phrases, confidence dots, layer nouns. All Simple-mode prose is composed mechanically by `simpleSummary()` from lexicon + record fields, then `esc()`-escaped — **no free text exists anywhere in Simple mode, so the words can never disagree with the numbers.** Excluded/inert postures use a "what presence exists is mostly …" clause instead of "driven mostly by" (the driver phrasing was incoherent for countries defined by absence — caught by rendering Myanmar from real data before writing any JS).
5. **Facility layer earmarked, empty:** `FACILITIES=[]` with the data contract documented in a code comment ({id, name, lat, lon, layer, pole, country_iso3, status, evidence_refs[]}), a no-op `renderFacilities()`, and a marker `<g>` already inside the map's zoom transform — so the future facility session is pure data curation, zero map surgery. Zoom/pan already existed in v4.
6. **Map adapts by mode:** legend heading and tooltips drop jargon in Simple mode ("Leans US · US 61 · China 45 — click for the story").

**Smoke tests (T1–T4):** lexicon covers every posture enum in `POSTURE_COLORS` (unmapped enums fail loudly); all nine desk-pass summaries generate with correct shape; exact-string match against the reviewed lexicon-v0.2 samples for MMR/VNM/LAO; structural markers present. The reviewed lexicon and rendered samples are committed to `project-knowledge/` as the editorial record. **The embedded `SIMPLE_LEXICON` object in map.html is the operative version; the markdown is documentation.** A wording change edits the code, bumps the version field, and updates the markdown in the same commit — if the two disagree, the code is truth.

## Step 4a extractor audit (2026-08-16, fourth session)

`extract.py` was read end to end after the byte count contradicted this file's "not started." It is a complete implementation of the 4a build order, not a scaffold — **and it has already been run.** `review_queue` held 191 rows at audit time, including one real machine-coded candidate event. See "First extraction run" below.

**Implemented:** `DISTINCT ON (feed_id, url)` work selection with an anti-join against `review_queue`; archive reader handling `.html`, `.html.gz`, `.json`; deterministic facet prefilter; LLM call at temperature 0 with reasoning-block stripping; candidate validation against the Postgres enums; write to `review_queue`. Flags: `--dry-run`, `--limit N`, `--capture-id N`.

**Design decisions in the file worth preserving:**

- **Every examined capture gets a `review_queue` row, including prefilter misses** (`status='rejected'`, `rejection_reason='prefilter: ...'`, matched terms stored in the candidate blob). `raw_captures` is immutable so `parse_status` cannot record "examined"; extraction state therefore lives in the queue. This makes the gate auditable per system prompt §2.3 and makes runs resumable by anti-join.
- **Word-boundary matching, case-sensitive for short all-caps terms.** Substring matching produced real false positives: "Digi" in "digital", "DICT" in "predict", "Intel" in "intelligence", "GIC" in "strategic".
- **Goodhart guard at extraction:** a `verified_value_usd` on a Tier-1/2 event is moved to announced and the coercion recorded as a problem on the row.
- **`source_tier` is attached from `feeds.yaml`, never inferred by the model.**
- Validation rejects against the actual enum sets, so a hallucinated layer name cannot fail an insert at approval time.
- Per-capture exception handling: one bad capture never stops a run.

**Known gaps and risks, to check on first run:**

- **`trafilatura` is imported in `read_capture` but is NOT in `collection/requirements.txt`.** ✅ Confirmed installed in the venv (2.2.0), so this is not currently breaking anything — but a rebuild from the repo would come up without it, and because the import sits inside a bare `except Exception` returning `None`, the failure would present as every HTML capture being "unreadable" rather than as an ImportError. Still needs adding to requirements.
- **`LLM_BASE` defaults to `http://192.168.1.190:4000` and `call_llm` appends `/v1/chat/completions`.** If LiteLLM's base already carries `/v1`, first contact is a 404.
- **GDELT JSON payloads are rejected** with "not an article." The path that expands them into per-article fetches is not built, so one of five feeds contributes nothing yet.
- **`mic-vn` is Vietnamese-language** — ✅ confirmed a real blind spot, see the feed diagnosis below. 0 of 63 captures passed the prefilter.
- `event_date` is passed through unvalidated — a malformed date string reaches the reviewer rather than being caught.

## First extraction run and feed diagnosis (2026-08-16, fourth session)

**The extractor had already been run** — 191 `review_queue` rows existed at audit time, from runs on 2026-08-16 between 13:47 and 14:23. A `--dry-run` therefore returns `0 capture(s) to examine`, which is correct behaviour, not a failure: the anti-join excludes captures that already have queue rows.

**Correct venv path is `/opt/silicon-dominoes/collection/.venv/`** — inside `collection/`, not beside it. From `WorkingDirectory=/opt/silicon-dominoes/collection` the interpreter is `.venv/bin/python`. (`.venv-validate` at the repo root is a separate throwaway for `validate.py` only.)

### The funnel

190 non-GDELT captures examined → 178 prefilter rejects → 12 reached the LLM → **6 pending, 3 judged not relevant, 3 LLM failures**. A ~6% pass rate, but see the diagnosis: most of it is not the filter's fault.

The 3 `needs_more_sourcing` LLM failures are all `capture_id 41` — one article retried during debugging, not three broken captures. That same capture also produced **two** `pending` rows, because `--capture-id` deliberately bypasses the anti-join. **The 6 pending candidates come from at most 5 distinct captures.** Either the review UI must tolerate duplicate candidates per capture, or the extractor should skip captures that already have a `pending` row.

### First machine-coded candidate event (review_id 4, capture 41)

The STT GDC $1.37B green loan for the 166MW Johor campus — the exact case this file nominated as the first test. The pipeline worked end to end: prefilter matched `MYS: Malaysia, Malaysian, Johor, Iskandar Puteri` plus entities `STT GDC, ST Telemedia, Nvidia`; the LLM returned parseable structured output; validation passed it with zero problems; `source_tier: S2` came from `feeds.yaml`, not the model.

**Coding problems the reviewer should catch — these are what human review exists for:**

- **`depth_dimensions: ["D"]` is wrong.** The campus completes in 2027; nothing is installed. A new commitment evidences **T** (flow), and a loan secured against the asset arguably **E**. Coding it as D violates the mandatory stock/flow separation (doc 01 §2).
- **`instrument_tier: 3` vs. the expected 4.** The ladder puts "financing closed" at Tier 4. A secured green loan reads as closed financing, not merely a signed contract. Material: weight 0.85 vs 0.70, and Tier 4 triggers an out-of-cycle D/E recompute (§4).
- **`controller_name: null`.** The exposure-edge primitive is (country, controller, layer); without a controller this event cannot produce an edge. Should be ST Telemedia Global Data Centres.
- **`model` recorded as `"qwen3.6"`** while `config.LLM_MODEL` defaults to `qwen3.6:35b-mlx`. Reproducing a coding decision needs the exact model string.
- `direction: "sovereign"` is defensible and should stand — STT GDC is Singapore-controlled and Singapore is in the third-pole vector per system prompt §1. Confirm it was deliberate rather than a fallback.

Note it cannot be approved as-is regardless: a Tier-3 event needs two independent S3+ sources and this has one S2. The queue holding it is the corroboration rule working.

### Feed diagnosis — the pass rate is mostly an input problem

Text length by feed, deduped by URL, measured through `read_capture`:

| Feed | n | median chars | under 3k | verdict |
|---|---|---|---|---|
| rss-lightreading | 50 | 3,933 | 13 | healthy — the only feed producing candidates |
| rss-datacenterdynamics | 22 | 3,274 | 11 | healthy; 0 passes is plausibly honest at this sample size |
| rss-mic-vn | 63 | 2,991 | 32 | real article text; **facet blind spot** |
| rss-e27 | 50 | 1,999 | 50 | **collector bug — not article text** |

- **e27 is a collector bug.** All 50 captures under 3k with a median of 1,999 is a distribution too tight to be real articles. Sampled text is login-wall chrome: "Your account contact email hasn't been verified yet… RECOMMENDED POSTS… POPULAR TOPICS." No facet work can rescue this; the fetch path is retrieving the wrong page.
- **mic-vn is a diacritics gap.** The text is genuine Vietnamese article content, but the geo facets carry `Vietnam` / `Viet Nam` undecorated while Vietnamese copy writes **Việt Nam**. Measured: **18 of 30 sampled captures contain "Việt Nam" or "Viettel".** 0/63 passing is a systematic coverage bias in a pilot country, not an absence of news.
- **Suspected AND-gate problem, not yet measured.** The prefilter requires ≥1 geo term AND ≥1 entity term, but `Viettel`, `VNPT`, and `FPT` are in the **geo** list, not `actors`. A domestic article about Viettel building its own DC matches geo, finds no entity, and is filtered — precisely the case that feed exists to catch. The diagnostic to confirm this was written but did not run successfully; rerun it before editing facets.

### Re-examination is a design decision, not a rerun

The anti-join keys on `review_queue` existence, so the 178 prefilter rejects will **not** be reconsidered under improved facets. `review_queue` rows are not immutable the way `raw_captures` is, so deleting rejects is possible — but the auditability-preserving option is to stamp `facets_version` into the candidate blob and anti-join on version, so a capture is re-examined when the facets change and the old decision stays on the record. Settle this before widening any lists.

## Desk-pass demo dataset (one-time artifact — containment rules)

`countries-desk-pass-2026-08-14.json`, embedded in map.html and existing as a standalone file. A **single-analyst manual research pass** performed in-chat on 2026-08-14, NOT pipeline output:

- Verified same-day: WAICO founding members in pilot = KHM, IDN, LAO, MYS, MMR; Pax Silica = SGP (Dec 2025 inaugural) + PHL (signed 2026-04-16); VNM and THA in neither. Major 2024–26 infrastructure from S2/S3 press (YTL–NVIDIA 600MW Johor operational; Viettel 22× DGX B200 + 120B-param sovereign model; Firmus/NVIDIA Batam; ByteDance THA ~$4B; etc.).
- Exposures/postures/bands are analyst estimates; postures are hand labels, no clustering run; no coded events table; corroboration rule not mechanically enforced; confidence 0.35–0.55; null coverage 0.80–0.94.
- **Containment:** envelope carries `provisional: true` + provenance note; `validate.py` must reject it (unknown envelope keys / no events backing) — same unpublishable-by-design property as the synthetic set. **Never ingest into the pipeline, never present as a published cycle.** When real cycles publish, keep it in the dropdown only if relabeled as a frozen August 2026 snapshot.

## Key decisions made (with rationale)

- **Pilot scope: Southeast Asia, 9 countries** (IDN, VNM, THA, MYS, PHL, SGP, KHM, LAO, MMR) — spans the full posture space and is the home turf of the Kuik hedging literature. Thin slice done properly beats global puddle.
- **Collection runs on the homelab, not GitHub Actions or the Mac mini.** LXC container **CT 109 `dominoes` at 192.168.1.204** on KAMRUI/Proxmox — per the homelab principle (Mac = inference only). Native Postgres in the CT with **peer auth** (no DB password exists anywhere), raw archive on **local CT storage** (see deployment session), ntfy for alerts, systemd timers.
- **CT 109 is privileged.** Forced by NFS + unprivileged-LXC uid mapping incompatibility; the CT's only job is fetching public feeds and writing to local storage, same trust boundary as other homelab CTs that touch the NAS. Trade-off accepted deliberately.
- **Two dedup strategies, split by module — deliberate, not accidental.** News feeds (`poll_rss.py`, `feeds:` key) dedupe on **URL**: the same article at the same URL is the same evidence, and its bytes churn meaninglessly. Verify items (`verify_watch.py`, `verify_items:` key) dedupe on **content hash** via `common.latest_sha`: the URL is stable and a *changed* page is precisely the signal (WAICO / Pax Silica membership). The two never share a code path. A `dedupe_on` key exists in `poll_rss` for per-feed override; no feed currently needs it.
- **All 18 tables are owned by `postgres`; `dominoes` holds DML only.** Every DDL change is therefore a manual step: `su - postgres -c "psql -d silicon_dominoes -c '...'"` from root inside CT 109. Kept deliberately — it makes accidental or automated migrations impossible, at the cost of the pipeline never self-migrating. Corollary worth stating plainly: the immutability triggers are enforced against the service user, not against a superuser, so T1–T11 are guarantees about the *pipeline's* behaviour, not absolute properties of the database.
- **Raw archive is gzipped from 2026-08-16 forward.** ~80% saving on HTML that is ~98% boilerplate. Pre-patch objects remain uncompressed; every reader must handle both.
- **Step-4 LLM extraction uses the existing local stack:** LiteLLM at 192.168.1.190:4000 → qwen3.6:35b-mlx. Temperature 0, strip reasoning blocks before JSON parsing. **The LLM never assigns `source_tier`** — that is a property of the feed, recorded in `feeds.yaml`, not something a model infers from an article.
- **Public/private boundary:** everything private stays in the homelab; the pipeline pushes the five JSON contracts to the GitHub repo; GitHub Pages serves the dashboard over them. Nothing in the lab gets exposed.
- **Wayback now, ArchiveBox later**; late snapshots go to append-only `url_snapshots` (`collection/sql/002_url_snapshots.sql`). `SD_WAYBACK=1` in `/etc/silicon-dominoes/collector.env` (Wayback submission is the slow part of each poll — several seconds per URL, rate-limited by IA).
- **Simple mode prose is templated, never hand-written.** Summaries are generated deterministically from a versioned in-file lexicon (`SIMPLE_LEXICON`) plus the data record — the plain-language layer inherits the same auditability guarantee as the numbers, and a lexicon change is a versioned, reviewable diff. Rationale: hand-written blurbs could silently disagree with the data; an LLM writing them would be unauditable.
- **Demo datasets are structurally unpublishable:** synthetic carries `synthetic: true`; desk pass carries `provisional: true` + unknown envelope keys. `validate.py` rejects both.

## Immediate next action (next session)

**As of 2026-09-05 (end of day):** prerequisites done, grader exists. Next: (1) verify which Phase 1 intake items below are actually complete (`trafilatura` in requirements, `Viettel`/`VNPT`/`FPT` in `actors`, `facets_version` anti-join, e27, GDELT expansion) before resuming them — several are now candidates for agent tasks since `validate.py --self-test` and the extractor's `--dry-run` give them a finish line; (2) Phase 2 breaking schema pass (doc 07 F-2/F-3/F-7, doc 04 L-8/L-9, F-6 text, H sub-indices, major `schema_version` bump) — agent writes schema deltas and `.sql`, Brian applies DDL; `validate.py` must-reject fixtures grow with it; (3) review UI only after Phase 2 lands.

**Written 2026-08-16, retained as the Phase 1 checklist:** Step 4a runs, and has produced its first real candidate. The next session fixes the two feed defects that are starving it. Ordered by value per unit of effort:

1. **Confirm the AND-gate hypothesis before touching facets.** Measure, over the `mic-vn` corpus, how many captures match geo-only vs. entity-only vs. both under the current lists, and whether the text is NFC-normalised (if it is decomposed Unicode, `"Việt Nam"` typed into YAML will not match no matter what is added, and the fix belongs in `read_capture` instead). This determines whether the fix is *more geo terms* or *moving `Viettel`/`VNPT`/`FPT` into `actors`* — probably both.
2. **Add Vietnamese-form geo terms** to `facets/entities.yaml`: `Việt Nam`, `Hà Nội`, `Đà Nẵng`, `Thành phố Hồ Chí Minh`, plus the ministries seen in the corpus (`Bộ Khoa học và Công nghệ`). Pure YAML, no code, recovers up to ~60% of 63 captures. Bump the facets version.
3. **Diagnose the e27 fetch path.** All 50 captures are login-wall chrome. Determine whether the feed supplies stub URLs, whether the page needs a different user-agent, or whether the outlet is simply unusable behind its wall — and if unusable, drop it rather than carry a dead feed that biases coverage silently.
4. **Decide the re-examination mechanism** (`facets_version` in the candidate blob vs. deleting prefilter rejects) — required before any facet change can actually recover the 178 already-rejected captures.
5. **Then the review UI.** Six pending candidates exist, one of them rich enough to design against, and the coding problems listed above are exactly the affordances the reviewer screen needs to make easy to fix.

Schema facts confirmed live, so 4a needs no migration:

- `review_queue` exists: `review_id`, `capture_id` (FK), `candidate jsonb`, `status`, `reviewer`, `reviewed_at`, `rejection_reason`. Enum `review_status` = `pending | approved | rejected | needs_more_sourcing`. Partial index on pending.
- `raw_captures` has **no title or text column** — content lives in the archive at `object_key`. The extractor reads files, not rows.

Build order for 4a (`collection/extract.py`):

1. Select work with `DISTINCT ON (feed_id, url)`; read `object_key`, handling `.html`, `.html.gz`, `.json`.
2. **Deterministic prefilter** on the facet lists (pilot-country geo terms + §1 vendor/financier entities) before any LLM call. Most captures are irrelevant trade press; the gate must be auditable, which a model triage is not.
3. LLM extraction → candidate JSON matching the `events` schema, written to `review_queue`.
4. First test case: the STT GDC $1.37B green loan for the Johor / Iskandar Puteri campus (capture in rss-lightreading) — MYS, Facilities, Tier 4 (financing closed), with value and counterparty. Clean, unambiguous, and directly comparable to the hand-coded desk-pass entry.

## Open items / TODOs

- **Fix or flag broken feeds in `collection/feeds.yaml`.** Three feeds return real malformed XML: `rss-techwireasia`, `rss-developingtelecoms`, `rss-imda-sg`. May be dead URLs, may serve HTML error pages to scraper user-agents, may need alternate URLs. Fix or remove.
- **Two `REPLACE-ME` placeholder URLs** still in `verify_items`: WAICO membership page and Pax Silica membership page (state.gov/pax-silica is the obvious S1 candidate for the latter). These will alert as failing until replaced, which is correct behaviour.
- **Move raw archive to NAS** (deferred). CT-local storage works and is backed up by vzdump, but the architecture intends the NAS as the raw-archive home. Path forward likely means either disabling Synology Advanced ACLs on the share, or a different mount approach. Less urgent now that gzip cut the growth curve ~5×.
- **Offsite backup of the raw archive**: decide Backblaze B2 via rclone vs. private GitHub repo — required, not optional. Applies to the CT-local path.
- ~~Run `validate.py` once locally~~ ✅ done (fourth session). ~~**Still outstanding: make containment intentional.**~~ ✅ `3b016ee` (fifth session, first agent task) — the three changes below are implemented. Right now the desk-pass file is rejected only *incidentally* — unknown envelope keys trip `additionalProperties: false`. Anyone who later relaxes the envelope silently loses the guarantee. Three changes: (1) an explicit check in `validate.py` rejecting any envelope carrying `synthetic: true` or `provisional: true`, with its own failure message; (2) a `schemas/fixtures/must-reject/` directory holding the desk-pass file plus a minimal `synthetic: true` envelope; (3) extend the existing `--self-test` flag so a file under `must-reject/` that *validates* is itself a test failure. Note the desk-pass file is not currently in `schemas/fixtures/` at all.
- **README line for Simple mode** (approved): "The dashboard opens in Simple mode — plain-language summaries generated word-for-word from each country's data record. The Advanced toggle exposes the full DEPTH-N analytic detail."
- **Facility map layer** (roadmap, data-side): curate `facilities.json` per the contract documented in map.html's `FACILITIES` comment. Seed candidates from the desk pass: YTL–NVIDIA Johor, Firmus/NVIDIA Batam, Viettel DGX B200 cluster. Design questions to settle first: hand-curated vs. derived from coded events (events would need optional geo fields), and whether facility entries fall under the S1–S4 corroboration rule (they should). Rendering (icons per layer, click-through, layer filter) is already scaffolded.
- **Add `trafilatura` to `collection/requirements.txt`** — confirmed in use (2.2.0 installed in `collection/.venv`) but absent from requirements. A rebuild from the repo comes up without it and fails silently as "unreadable" captures.
- **Fix or drop `rss-e27`.** All 50 captures are login-wall chrome, not articles. Collector-side bug; the feed contributes nothing until it is fixed.
- **Vietnamese-form geo terms in `facets/entities.yaml`** — 18/30 sampled `mic-vn` captures contain "Việt Nam" or "Viettel"; none match the current undecorated terms.
- **Move `Viettel`/`VNPT`/`FPT` from `geo` to `actors`** (pending confirmation) — as geo-only terms they cannot satisfy the prefilter's entity requirement, so domestic-operator stories fail the AND gate.
- **Decide the re-examination mechanism for prefilter rejects** — `facets_version` stamped in the candidate blob and anti-joined on, vs. deleting rejected rows. Without it, facet improvements do not reach the 178 already-rejected captures.
- **Extractor writes duplicate candidates for the same capture** when `--capture-id` is used (capture 41 has two `pending` rows). Either skip captures with an existing `pending` row, or handle duplicates in the review UI.
- **Build the GDELT JSON expansion path** in the extractor — result payloads are currently rejected as "not an article," so the GDELT feed produces no candidates.
- ~~`PreToolUse` hook for the DDL prohibition~~ ✅ `b576eff` (fifth session).
- **Confirm doc 07 F-7 fragment is committed** to `project-knowledge/`; it currently exists as a chat artifact.
- **Rotate `SD_LLM_KEY`** in `/etc/silicon-dominoes/collector.env` (previously exposed in chat). Enumerate consumers first.
- **License**: decide before public data launch (MIT for code like Trapline; consider CC-BY for datasets).
- From ARCHITECTURE.md §15, still open: posture clustering algorithm choice; admin framework for the review UI (lean pragmatic for v1).
- **Consider `SD_WAYBACK=0` for iterative testing.** Lower priority than it looks: since the URL-dedup fix a re-poll skips known URLs *before* the Wayback submission, so the cost only lands on genuinely new articles, and step-4 extraction reads archive objects without polling at all. **Verify the safety net before relying on it** — confirm `snapshot_retry.py` selects on *absence of a snapshot row*, not a time window; if it filters by recency, captures skipped today could fall out of range and never be snapshotted, silently breaking the traceability guarantee (ARCHITECTURE §3). **Do not edit `collector.env`** — all five timers load it, so a forgotten flag degrades scheduled runs. Use a per-invocation override instead, with the assignment *inside* the quoted command: `su - dominoes -c 'SD_WAYBACK=0 ...'` (`su -` resets the environment, so putting it outside silently does nothing).

## How to resume in a fresh chat

Point Claude at this file. For collection ops: `pct enter 109` from the Proxmox host lands you in the CT as root; use `su - dominoes -c '...'` to run pipeline commands as the service user (peer auth requires the Linux user to match the Postgres role). DDL requires `su - postgres -c '...'` instead. Check timer schedule with `SYSTEMD_PAGER=cat systemctl list-timers 'sd-*'`; check what timers ran with `journalctl -u sd-rss.service --since today --no-pager` (remember: `--since`, not `-b`, inside LXC).

**Git in the CT (pull-only since 2026-09-05):** the repo is owned by `dominoes`, so git commands run as root fail with "dubious ownership." Use `su - dominoes -c '...'`. **Never edit the repo in the CT.** Deploy with `pct exec 109 -- su - dominoes -c '~/bin/sd-deploy'`; it refuses if the tree is dirty, which is the guard. Push from the CT is disabled at the remote-URL level. Windows checkout: `C:\Users\harbi\Documents\GitHub\chessmasterAI\silicon-dominoes\silicon-dominoes`.

**Shell gotchas that cost time this session:**
- Multi-line pastes into `pct enter` or `su - <user>` are swallowed before the shell is ready — run one command at a time, or use `su - dominoes -c '...'`.
- `psql` pages through `less`; `export PAGER=cat` is now in the `dominoes` user's `.bashrc`.
- scp runs on the machine the file should end up on. Pulling to Windows means running it in PowerShell, not in the Proxmox SSH window. Give an explicit destination path so a stray paste can't swallow the `.`.
- Moving files CT → Windows: `pct pull 109 <src> /tmp/<file>` on the host, then `scp root@192.168.1.50:/tmp/<file> C:\path\<file>` in PowerShell.

For Step 4: LiteLLM is at 192.168.1.190:4000, qwen3.6:35b-mlx is the model, temperature 0, strip reasoning blocks before JSON parsing (from homelab principles).

**Running `validate.py` on Spectre:** Windows Python is 3.8 and lacks `jsonschema`; use `wsl -d Ubuntu -- bash -c 'cd /mnt/c/Users/harbi/Documents/GitHub/chessmasterAI/silicon-dominoes/silicon-dominoes/schemas && python3 validate.py --self-test'`. In CT 109 the equivalent is `../.venv-validate/bin/python validate.py --self-test` from `schemas/`.

**Pipeline venv is `/opt/silicon-dominoes/collection/.venv/`** — inside `collection/`, not beside it. Run from `WorkingDirectory=/opt/silicon-dominoes/collection` as `.venv/bin/python -m collector.extract`. Manual runs do not load `/etc/silicon-dominoes/collector.env` (systemd does that via `EnvironmentFile=`); the defaults in `config.py` currently match the env file, so this has not bitten yet, but prefix with `set -a; . /etc/silicon-dominoes/collector.env; set +a;` when it matters.
