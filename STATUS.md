# Silicon Dominoes — Project Status
Last updated: 2026-09-20 (eighth session, end of day; sixth and seventh sessions were recorded retroactively the same day) · Purpose: running record of what's built, what's live, and what's next. Update this file at the end of each work session — **and commit it.** This file was not tracked in git until 2026-09-05; see that session's entry.

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

## Intake audit and Phase 1 close-out (2026-09-08, sixth session)

**Purpose:** verify the "roughly a thousand unprocessed captures" claim before scoping Phase 1 work. The claim was stale; Phase 1 is closed. Every finding below is from live container output that session, not from prior notes.

**Findings (all checked against the machine, several correcting this file):**

- **Intake is current.** `sd-deploy` landed `5bc5c30`. Extractor dry run found **89** captures to examine, not ~1,000 — extraction had been running since the fifth session (the `ensure_ascii=False` fix in `5bc5c30` only triggers on Vietnamese geo hits). Full run: `extracted=2, filtered=85, not-relevant=2`. Funnel by feed for the day: DCD 3 pending / 47 rejected, e27 22 rejected, Light Reading 18 rejected. No mic-vn in the batch.
- **`gdelt-sea-stack` and `rss-mic-vn` are retired, not failing.** Commit `e1edad9` (2026-09-05) commented both out of `feeds.yaml` with inline rationale; `sd-gdelt.timer` is `disabled` (unit file present, `Active: inactive (dead)`); only four `sd-*` timers are scheduled. Earlier "GDELT 429 on every run" and "mic-vn staging host unresolved" framings in this file describe the pre-`e1edad9` state and are superseded. GDELT's last capture: 2026-08-16. mic-vn's last capture: 2026-09-05 14:16 UTC.
- **GDELT root cause is upstream.** Per GDELT's own guidance the legacy DOC search API is being intentionally starved during a Spanner migration; the sanctioned replacement is the Web NGrams dataset (per-minute gzipped files plus TOC at `data.gdeltproject.org/gdeltv5/weblegacy/ngrams/`). Reinstating GDELT is therefore a rebuild (new poller, domain→source-tier map, provenance recording), not a fix. Parked behind Phase 2; run a volume probe before scoping.
- **`entities.yaml` header fix landed** in `e1edad9` (header now defers to the `version` key).
- **Review queue, all time:** `pending 10 · rejected 1191 · needs_more_sourcing 48`. All 48 `needs_more_sourcing` rows carry `rejection_reason = "LLM extraction failed — retry"`. They are retry work, not review work; the true review backlog is 10.
- **Health monitor is running and opening `research_gaps`** (gaps 5–9 exist). Two defects:
  - **Baselines are miscalibrated.** Gaps 6–8 flag DCD, e27, and Light Reading as "capture rate collapsed" against expected ~29–31 per 48h. Those expectations are the first-fill burst (50-item initial polls). Post-dedup steady state is 0–2 new per poll, which is correct behaviour. Three of the five most recent gaps are false positives.
  - **Gap 9 (mic-vn) has no `country_iso3`.** Retiring mic-vn leaves **VNM with zero in-country sources**; all Vietnam evidence now arrives via English-language trade press. This is a coverage gap for a pilot country (ARCHITECTURE §4 "we stopped hearing" vs "nothing happened") and must be disclosed as such, not filed as feed health.
- **`rss-e27` is still live** and captured 59 items in the prior 7 days, all login-wall chrome (22/22 prefilter rejects on the day). It inflates capture counts and coverage metrics while contributing nothing.
- **Three malformed feeds still fail every 2h run** (`rss-techwireasia`, `rss-developingtelecoms`, `rss-imda-sg`) and emit a notify each time — ~12 alerts/day for a known condition.
- **`dominoes` is not in `systemd-journal`**, so `journalctl` as that user shows a partial log. Read logs as root: `pct exec 109 -- bash -c "journalctl -u sd-rss.service --since '6 hours ago' --no-pager"`.

**The day's two candidates became the Phase 2 test fixtures.** Both miscoded in exactly the ways doc 07 F-2 predicts:

- Capture 1274, Firmus/OpenAI Malaysia → extractor coded `MYS / facilities / T2 / us`. Direction assigned from the customer's nationality; the controller is Firmus (control AUS, third pole) with OpenAI as tenant.
- Capture 1242, Thailand pauses construction on 49 DCs → extractor coded `THA / facilities / T1 / sovereign` **and** `THA / cloud / T5 / us`. The first is wrong: a construction pause is a reversal at the tier reversed, not a Tier-1 statement of intent. The second is a hallucinated event — AWS's existing presence mentioned as article context, coded as new operational-at-scale; it would trigger an out-of-cycle D/E recompute if approved.

**Decisions:**
- Phase 1 declared closed. No facet work is on the critical path.
- The critical path is Phase 2 (one breaking schema pass), then the review UI.
- mic-vn retirement stands; the VNM coverage gap is recorded and a replacement-source gap is owed (candidates: VnExpress International, Vietnam Investment Review — S2/S3, English, durable URLs).
- e27 to be retired the same way as mic-vn (comment out with dated inline rationale).

## F-2 / F-7 schema pass (2026-09-09, seventh session)

*Written 2026-09-20 from session notes, not contemporaneously. Items marked ✓ were re-verified against the checkout on 2026-09-20.*

- **Design review before build.** Five registry design decisions (D1–D5) were critically reviewed before any code was written; all five had real defects: D1 conflated incorporation with jurisdiction of control and left `third` as an unexamined residual; D2 silently dropped mixed-ownership signal in consortia; D3 wrongly concluded a foreign tenant produces no exposure edge; D4 made reversals of pre-collection facts structurally uncodeable; D5 had no handling for HKG, MAC, TWN, or EU. The corrections are recorded as doc 07 **F-7** and are authoritative for the F-2 implementation.
- **Fixture containment.** Fixtures carrying real entity names and plausible URLs were identified as fabricated citations sitting in the repo. Rule now mechanical: `https://fixture.invalid/` URLs only, `^test_` ids, `(fixture)` in names, enforced by `check_no_fixture_artifacts` on the publish path.
- **Pre-flight finding: `project-knowledge/` held only docs 01–04.** Docs 05, 06, and 07 were absent despite this file recording otherwise. Doc 07 v1.1 (31,750 bytes) was written and committed as `5bda127` ✓. **Docs 05 (Simple-mode lexicon) and 06 (DIMEFIL provenance) are still not in the repository** — recovery items, below.
- **Build.** Agent task on branch `f2-controllers`: 859 net insertions across 17 files — `controllers.schema.json`, `pole_map.json`, `controller_id` required on edges with no null branch, stored `direction` forbidden, `direction_derived` emitted `derived: true`, inline `reversal_target`, no sum constraint across exposure fields. `validate.py --self-test` passed with 11 must-reject cases in both WSL and the CT 109 venv. Merged fast-forward to main as `b5cff84` ✓, pushed ✓ (`origin/main` = `b5cff84` after fetch, 2026-09-20), deployed to CT 109.
- **`schema_version` is already `2.0.0`** ✓ across the five passing fixtures and the must-reject set; the major bump rode in `b5cff84`. The `countries.json` in `must-reject/desk-pass` and `must-reject/synthetic-minimal` are at `1.0.0` ✓ — confirm each is rejected for its intended reason (the `provisional` / `synthetic` envelope check) and not incidentally.
- **Git identity.** The Windows global identity was `VerdunHere` / `harbison.brian@gmail.com`; corrected to `Brian Harbison` / `harbisonbrian@protonmail.com`. Two already-pushed commits carry the old identity and were deliberately left as-is rather than rewriting published history. A junk config key `credential.https://github.com.usernamegit` (paste collision) was removed. `git config --global core.pager cat` set, so `git diff --stat` no longer traps in `less`.
- **Agent fabrication pattern, second instance.** The Code agent printed a commit URL under `github.com/VerdunHere/...` — inferred from the misconfigured local identity, not from the remote. The actual remote is `bjharbison`. **Verify pushes by `git fetch` + `git log origin/main`, or on GitHub directly — never from agent output.**
- **Fixture bloat flagged.** Each of the 11 must-reject cases carries a full five-contract copy (55 files ✓). Every new check adds five more. Addressed first in the next-action list.

## Collection outage, feed retirements, and collector hardening (2026-09-20, eighth session)

Everything below was verified against the machine during the session unless marked otherwise.

**What was found.** A routine `sd-deploy` failed on DNS, and the follow-up check of the RSS journal showed the last line was `Starting sd-rss.service` at **2026-09-19 20:17:03 UTC** with no `Finished`. The poller had been hung for ~18 hours; `sd-rss.timer` showed `NEXT -` because systemd will not schedule a new run while the previous activation is still `activating (start)`. Last completed poll before the hang: 2026-09-19 18:19 UTC.

**Diagnosis.**
- `ss -tunp` on the hung process showed one socket, `ESTAB 0 0 → 104.26.4.32:443`, both queues empty: a blocking read on a connection whose far end had gone quiet. Resolving every host in `feeds.yaml` matched that address to **`e27.co`**. The hang reproduced on a fresh run at 14:08 UTC (same host, 306 ms CPU in four minutes) and did *not* reproduce at 14:24 UTC — intermittent, not permanent.
- **Root cause, confirmed in the fix diff:** `poll_rss.poll_feed` called `feedparser.parse(url, agent=...)`, letting feedparser fetch the feed URL itself with **no timeout**. The article fetch beside it already had one. One unbounded call, on the first network operation of every feed.
- **Contributing:** (1) the units are `Type=oneshot`, whose start timeout defaults to infinity — nothing bounded the run; (2) stdout to the journal is block-buffered and flushes at exit, so a hung run logs *nothing* — an empty journal says nothing about which feed it is on (the feed was identified from the socket, not the log); (3) `sd-health` ran at 08:12 UTC, twelve hours into the outage. Whether it alerted was not checked; either way the alert would have been indistinguishable from the three false "capture rate collapsed" gaps it opens daily. The miscalibrated baseline stopped being housekeeping when it masked a real outage.

**Evidence lost: none.** `raw_captures` has zero rows after 2026-09-19 18:00 UTC, but the three polls before the hang had also found 0 new (weekend), and the first completed polls after recovery found DCD 20/20 and Light Reading 50/50 entries already captured — no item entered either feed during the outage that was not already held. Collector down ~18h over a weekend, nothing missed.

**Feed retirements — `8edc18c`.** `rss-e27` (all-time `review_queue`: **254 of 254 rejected**, zero candidates ever; ~a fifth of everything the extractor had examined), plus `rss-techwireasia`, `rss-developingtelecoms`, `rss-imda-sg` (unparseable on every poll since 2026-08-16). Commented out in `feeds.yaml` with dated inline rationale, same style as the mic-vn retirement. First poll under the new config, 14:33 UTC: two feeds, `0 feed failure(s)`, no `[notify]` line — the first alert-free poll since 2026-08-16.

**The collection layer is now two feeds:** `rss-datacenterdynamics` and `rss-lightreading`. Both S2, both English-language trade press. **No S1 source and no in-country source for any of the nine pilot countries.** This was effectively true since August (the other four never produced a candidate); the config now says so. Source recruitment is a real workstream and the source choices are Brian's analyst call.

**Collector hardening, task A — `2497b85`** (branch `harden-fetch`, agent-built in two passes, 20 files, 1,113 insertions; merged fast-forward, pushed, deployed).
- `collector/fetcher.py`: the one bounded HTTP path for every outbound call (feed fetch, article fetch, Wayback, ntfy, verify_watch, snapshot_retry, poll_gdelt, the LLM call). Connect and read timeouts, a **whole-call wall-clock deadline** (connect, header wait, and body all count), and a response size cap; typed `FetchError` / `FetchTimeout` / `FetchTooLarge`. Mechanism: the request runs in a **daemon** watchdog thread and `fetch()` waits on it up to the deadline — daemon so an abandoned worker can never keep the process from exiting. feedparser now only ever parses bytes.
- Defaults in `config.py`, env-overridable: `SD_HTTP_CONNECT_TIMEOUT=10`, `SD_HTTP_READ_TIMEOUT=30`, `SD_FETCH_DEADLINE=60`, `SD_FETCH_MAX_BYTES=10485760`, `SD_FEED_BUDGET=300`, `SD_LLM_DEADLINE=300`. The LLM call passes `read_timeout` and `deadline` = `LLM_DEADLINE`, because a local model sends nothing until the whole completion is ready.
- `poll_rss`: per-feed wall-clock budget checked before each article fetch; on exhaustion logs `outcome=budget_exhausted urls_remaining=N` and moves on (the URLs are picked up next poll by the existing dedup). Budget exhaustion is not counted as a failure. A small `DbStore` seam and a `run(cfg, store)` loop let tests drive the poller with an in-memory fake. **Expected behaviour:** a new 50-item feed with Wayback submissions will exhaust its 300 s budget on first fill and finish on the next poll.
- All five `collection/systemd/*.service` files carry `Environment=PYTHONUNBUFFERED=1` and a `TimeoutStartSec` (rss 30min, gdelt 30min, snapshot-retry 60min, verify 10min, health 10min).
- **First collector test suite:** `collection/tests/`, 16 tests, stdlib `unittest`, no Postgres, no external network (stub servers on 127.0.0.1): black-hole, body-trickle, header-trickle, oversized, gzip decoded, 5 MiB throughput (0.027 s), explicit-deadline override, LLM slow-response, poll run over [black-hole, trickle, healthy], per-feed budget, static guard (no raw `requests`/`urllib`/`feedparser.parse(url)` outside the fetcher), unit-file guard, requirements scan including function-level imports. **Passes in WSL (Python 3.14) and in the CT 109 pipeline venv (Python 3.13).** The first agent pass streamed with `chunk_size=1`; the follow-up replaced it. The "before" throughput was never recorded, so no claim is made about how slow it was.
- **Unit files installed on CT 109 from the repo** (~17:02 UTC): diffed first (additions only), copied to `/etc/systemd/system/`, the live-only `sd-rss.service.d/timeout.conf` drop-in removed, `daemon-reload`, all five verified via `systemctl show`. A rebuild from the repo now reproduces the ceilings.

**CT 109 DNS.** `sd-deploy` failed three times during the session with `Could not resolve host: github.com`; each time a retry minutes later succeeded, 20/20 burst lookups were clean on both CT and host, and the poller resolved hosts seconds after a failure. Pattern: **the first lookup after several idle minutes fails.** Root cause not established (the double-NAT path is the suspect). Mitigation applied: `pct set 109 --nameserver "1.1.1.1 9.9.9.9"`, active after a CT reboot (~17:00 UTC; all four timers rescheduled). If `sd-deploy` fails this way again with two resolvers, the problem is upstream of the CT and belongs on the UDR7 migration list.

**Operational notes learned this session:**
- `systemctl start` on a oneshot unit blocks until the run ends — use `systemctl start --no-block sd-rss.service`, then read the journal.
- A hung run is diagnosed from its socket (`ss -tunp | grep python`, then resolve the hosts in `feeds.yaml`), not from its log.
- `unittest` prints its verdict to stderr while test `print()` output is buffered to the end, so `| tail` can cut off the verdict. Use: `... -m unittest discover -s collection/tests 2>&1 | grep -E "^Ran |^OK|^FAILED|^FAIL:|^ERROR:"`.
- Run collector tests in the CT from the repo root: `pct exec 109 -- su - dominoes -c 'cd /opt/silicon-dominoes && collection/.venv/bin/python -m unittest discover -s collection/tests'`. In WSL use the venv at `collection/.venv-test` (gitignored).
- Unit files in `/etc/systemd/system/` are **copies**, not symlinks. After any change under `collection/systemd/`: deploy, `diff -u` live vs repo (stop on any `-` line), `cp`, `daemon-reload`, verify with `systemctl show`.
- `STATUS.md` had itself fallen two sessions behind; the sixth- and seventh-session record was spliced in at the start of this session as `51360a3`.

### Task B1 — run ledger and circuit breaker (`bbb0ccf`, same session, later)

**What landed** (branch `harden-ledger`, agent-built in three passes; 9 files, 1,013 insertions; merged fast-forward, pushed, deployed):
- `collection/sql/004_feed_runs.sql` — append-only `feed_runs` ledger: one row per feed per poll **attempt** (`ok | failed | timeout | budget_exhausted | skipped_quarantined`), `entries_seen`, `new_captures`, `error`; index on `(feed_id, finished_at DESC)`; `CALL make_append_only('feed_runs')`; `GRANT SELECT, INSERT ... TO sd_pipeline`. No sequence grant — `run_id` is an identity column and needs none; the agent's first draft carried a schema-wide `GRANT USAGE ON ALL SEQUENCES`, removed as out of scope.
- `collector/breaker.py` — pure, no I/O. Quarantine after `SD_BREAKER_THRESHOLD=5` consecutive `failed`/`timeout` runs; `ok` and `budget_exhausted` reset the count; the breaker's own skip rows are ignored when counting; one probe per `SD_BREAKER_PROBE_H=24`. There is **no stored "quarantined" flag**: state is derived from the ledger, and "just entered" is detected by comparing the verdict on the history with the verdict on the history minus its newest row — so each transition notifies exactly once and is reproducible from the table alone.
- `poll_rss`: `poll_feed` returns an outcome; feeds are polled healthy-first, then no-history, then failing (stable within groups); a quarantined feed is skipped with no connection attempt, logged `outcome=skipped_quarantined next_probe=...`, and is not counted as a failure for the per-run notify.
- **The ledger is best-effort and must never fail a poll.** A bug here was caught in review, not by tests: psycopg leaves a connection in an *aborted transaction* after any failed statement, so with `feed_runs` missing the swallowed `UndefinedTable` would have made every later query in the run fail — a missing ledger would have failed every feed. The in-memory `FakeStore` has no transaction state and all 38 tests passed regardless. Fix: `DbStore.record_run` / `recent_runs` roll back before re-raising, and `run()`'s generic except-branch rolls back before recording the failure, so a DB error inside one feed cannot poison the connection for the feeds after it. Pinned by `test_transaction_recovery.py`, which drives the real `DbStore` over a fake psycopg-style connection.
- **39 tests**, passing in WSL (Python 3.14) and in the CT 109 pipeline venv (Python 3.13). New: breaker transitions, quarantine with zero connection attempts, ordering, ledger rows per outcome, ledger-unavailable, aborted-transaction recovery, a static check that the SQL `CHECK` list matches the code's outcome constants and grants only to `sd_pipeline`, and a **must-exit** subprocess test (a process that hit a trickle timeout must actually exit).

**Live verification, in this order on purpose.** Deployed `bbb0ccf` *before* applying 004 and ran a poll: `feed_runs unavailable: relation "feed_runs" does not exist` printed **once**, both feeds polled, `0 feed failure(s)` — the rollback fix exercised against real psycopg, which the test suite can only imitate. Then 004 applied as postgres (`BEGIN / CREATE TABLE / CREATE INDEX / CALL / GRANT / COMMIT`); the next poll wrote `run_id` 1 and 2, both `ok` (DCD 20 entries, Light Reading 50, 0 new), 2026-09-20 17:50 UTC.

**Applying collection DDL** (root's shell pipes the file in, so `postgres` needs no read access to the dominoes-owned repo):
`pct exec 109 -- bash -c "su - postgres -c 'psql -d silicon_dominoes -v ON_ERROR_STOP=1' < /opt/silicon-dominoes/collection/sql/004_feed_runs.sql"`

**Role finding (verified live).** Every collector table grants to role **`sd_pipeline`**; `dominoes` is the peer-auth login role and a *member* of `sd_pipeline`. `CLAUDE.md` §1 and earlier sections of this file say `dominoes` "holds DML" — true only via that membership. New tables grant to `sd_pipeline`. Brian to correct the `CLAUDE.md` wording.

**Health-monitor findings — from reading `feed_health.py` and the live tables; these define task B2:**
1. It measures **captures, never polls.** Every check reads `raw_captures`, where "nothing new" and "collector dead" are the same picture.
2. **Gaps open once and never close.** `open_gap_if_needed` returns early if a gap is open for that origin and nothing ever closes one. Gaps 6 and 8 — the August false positives on the two live feeds — have occupied those feeds' slots since 2026-08-23/24, so during the outage the monitor *could not* open anything new. This corrects contributing factor (3) above: not "indistinguishable" — no new signal was possible.
3. **The notification is identical every day.** `notify` fires whenever `problems` is non-empty, and the two `REPLACE-ME` verify items guarantee it never is: a high-priority "Feed health: N problem(s)" has gone out every morning since 2026-08-17.
4. **The capture-rate check is weekend-blind:** last 48 h vs. a 30-day daily average, so the Monday 08:12 UTC run sees a weekend-sized window. (The August first-fill inflation has since rolled out of the 30-day baseline; the weekend effect is structural.)
5. **Retired feeds are still `active = t`.** All ten rows in `feeds` are active, including `gdelt-sea-stack`, `rss-mic-vn`, `rss-e27` and the three malformed feeds: `config.sync_feeds` never deactivates a feed that leaves `feeds.yaml`, so the monitor checks retired feeds forever. (The poller is unaffected — it reads the YAML.)
6. No gap carries a `country_iso3` — feed-health gaps have no country attribution at all, not just gap 9.
- **Correction:** the prediction written earlier this session — that the 08:12 UTC run would open *new* false gaps for the four retired feeds — was wrong. Each already holds its one open gap (2, 3, 7, and presumably 1), so nothing new can open. Gap 9 (mic-vn, opened 2026-08-31, before its retirement) was the one correct gap in the table.

**Not reviewed this session:** the B1 agent's report lines on (a) whether the must-exit test fails when `daemon=True` is removed from `fetcher.py`, (b) whether the aborted-transaction test failed before the rollback fix, and (c) what `common.connect()` does about autocommit. The code and tests were reviewed directly; these three claims were not seen.

### Task B2 — health monitor rebuilt on the ledger (`52a5147`, same session, evening)

**What landed** (branch `harden-health`, agent-built in an initial pass plus three reviewed follow-ups; merged fast-forward, pushed, deployed; **93 tests** pass in WSL on Python 3.14 and in the CT 109 venv on Python 3.13):
- `collector/health_rules.py` — pure rules, no I/O, `now` injected. Origins: `collector_down` (every judgeable RSS feed is poll-stale; replaces the individual gaps), `poll_stale:<id>` (newest `feed_runs` row of any outcome, skips included, older than `SD_POLL_STALE_H=6`), `dead_feed:<id>` (breaker's consecutive-failure count — one definition of "failing" — or no captures ever, or a verify item never/no longer verified), `low_volume:<id>` (last 7 days < 25% of the median of the prior 4 full weeks; needs 3 clean weeks, first 48 h excluded, median >= 5). Seven-day windows each contain one weekend, so the old Monday false alarm cannot recur. **`low_volume` is dormant until about 2026-10-20** — the ledger began 2026-09-20.
- `reconcile()` opens what is asserted and has no gap, and **closes what is no longer asserted**, so the monitor can fire again. Only managed origins (the four above) are ever touched; analyst gaps are not.
- **Unknown is never "cleared".** `evaluate()` returns `problems` plus an `undetermined` set, and `reconcile()` neither opens nor closes an undetermined origin: an unreadable ledger freezes every ledger-derived origin; a failed `raw_captures` read is recorded as *unreadable*, never as "never captured"; while the collector is down or one feed is poll-stale, that feed's `dead_feed` / `low_volume` gaps are frozen rather than closed; the breaker half of `dead_feed` and `low_volume` are still evaluated from the ledger when captures are unreadable.
- **New-feed grace:** a feed with no ledger rows is undetermined for `SD_POLL_STALE_H` after `feeds.created_at` (which the `sync_feeds` upsert does not touch) and counts toward `collector_down` in neither direction — adding sources will not produce false alert pairs.
- `config.sync_feeds` now sets `active = false` for rows absent from `feeds.yaml` (feeds and verify items both count as present) and reactivates returns; gaps on inactive feeds close as "feed retired".
- Notifications **on change only**: one message per run listing opens and closes; high priority only if something opened or a `collector_down` re-notify is due (not before `SD_COLLECTOR_DOWN_RENOTIFY_H=6` has elapsed, then about once per window); closures-only is default priority. `--dry-run` evaluates and prints, no writes, no notify. `--digest` lists every open managed gap.
- Units: `sd-health.timer` is **hourly** (`*:05` + up to 10 min jitter) — a dead collector is now noticed within about 7 h of its last poll rather than up to a day later; new `sd-health-digest.service/.timer`, Mondays 08:30 UTC. Optional per-feed `country_iso3` in `feeds.yaml` is carried onto gaps (documented in `collection/README.md`).

**Caught in review, not by tests** — all one family, "could not find out" treated as "fine": (1) an unreadable ledger would have closed a real `collector_down` gap and announced it; (2) a failed `raw_captures` read was the same value as "never captured" and would have opened a false `dead_feed`; (3) `collector_down` or a stale feed silently stopped asserting per-feed gaps, closing them as "condition cleared"; (4) the unreadable-captures branch skipped `low_volume`. Also: `test_collector_down_renotify_timing` passed in the evening and failed two hours later — the fake store stamped `opened_at` from the wall clock while `run()` used an injected `now`. Fixed in the test (clock pinned to `T0`); the rule was not loosened. The agent itself flagged that no test can catch a wrong SQL literal (`gap_status` is `open | resolved | wontfix` — there is no `closed`).

**Live sequence and results (2026-09-20, UTC):**
- Write statements validated against real Postgres inside a rolled-back transaction, as `dominoes`: `BEGIN; INSERT ...; UPDATE ... SET status = 'resolved' ...; SELECT ...; ROLLBACK;` -> `INSERT 0 1`, `UPDATE 1`, `resolved | t`. This is the pattern for any SQL the tests cannot execute.
- A poll ran `sync_feeds`: six rows went `active = f` (`gdelt-sea-stack`, `rss-mic-vn`, `rss-e27`, `rss-techwireasia`, `rss-developingtelecoms`, `rss-imda-sg`); four stay active.
- `--dry-run` predicted exactly: 0 to open, 7 to close. Units installed (diffed first: only `sd-health.timer` differed, two digest units new). First live run 21:54:53 — gaps 1–3, 7, 9 closed as "feed retired", gaps 6 and 8 (the August false positives on the live feeds) as "condition cleared", `notified=True`. The hourly timer then fired by itself three seconds later: `0 opened, 0 closed, notified=False` — an unplanned proof of idempotence. A second `--dry-run`: nothing to do. **Open managed gaps: 4 and 5 only** (the two `REPLACE-ME` verify items — real problems).
- The ntfy message did **not** arrive: `SD_NTFY_URL` was empty. See the next subsection.

**Known limits, accepted:** the `collector_down` re-notify is stateless (derived from `opened_at`), so timer jitter can occasionally skip or double a window; ledger-based rules cover `feed_class = rss` only — a rebuilt GDELT poller must write `feed_runs` or it will be invisible to them; `--dry-run`'s final `done:` line says "closed" for would-close (cosmetic).

**Not reviewed this session:** the final `config.py` diff after the follow-up that added the empty-config guard (if `feeds.yaml` yields zero feed_ids, skip the deactivation UPDATE and warn) — requested and covered by a test, but not read by eye. The three B1 agent-report items listed above also remain open.

**A side effect to act on:** auto-closing gap 9 (`dead_feed:rss-mic-vn`, "feed retired") removed the only open record of **Vietnam's coverage hole**. A retired feed is not a closed intelligence gap. It needs an `analyst`-origin gap, which the monitor never touches (see housekeeping).

### Alerts were never delivered — found and fixed (`9bf5f72`, same session, night)

**The finding.** After the first live health run logged `notified=True`, Brian reported that nothing reached his phone. `SD_NTFY_URL=` was present in `/etc/silicon-dominoes/collector.env` with **no value**, and `notify()` was written to print its `[notify] ...` line and return quietly when the address is empty. **From 2026-08-16 until 2026-09-20 this pipeline never delivered a single notification to anyone.** No phone had the ntfy app or a subscription either. It was found only because someone asked whether the message actually arrived.

**Corrections to earlier statements in this file** (left in place above as the record of what was believed at the time):
- Sixth session: the three malformed feeds did not "emit a notify ... ~12 alerts/day". They wrote twelve `[notify]` lines a day to the journal. Nothing was sent.
- Eighth session, contributing factor (3), and B1 health-monitor finding 3: no "Feed health: N problem(s)" message ever "went out", daily or otherwise. The alarm-fatigue reasoning was built on journal lines. The 18-hour outage raised nothing because the system had no way to tell anyone anything.
- "The first alert-free poll since 2026-08-16" was the first poll without a `[notify]` *log line*.
- Every `notified=True` before `9bf5f72` meant "notify() was called", not "delivered".

**Fixed the same night.**
- Alerts now go to the public `ntfy.sh` server on a secret topic. **The topic name is the only protection** — anyone who knows it can read and send — so it is a long generated passphrase, saved in Vaultwarden first, entered on the phone (ntfy app: **+**, topic name, Subscribe) and on the server. It never appears in chat, terminal output, or the journal. Self-hosting ntfy on the tailnet remains an option; it would be a one-line change to the same file.
- `collector.env` was world-readable (`-rw-r--r-- root root`); now `-rw-r----- root dominoes`, since it holds a secret.
- `notify()` (`9bf5f72`): returns `True` only on a 2xx from the server; an empty address prints `NOT SENT`; a refusal prints the HTTP code; any exception is caught; the URL, host, path and bare topic are redacted from every printed line; titles are made header-safe so a character like an em dash cannot silently kill delivery. `feed_health`'s `done:` line reports `none | delivered | FAILED`, and a failed delivery never undoes the gap writes. Every collector entry point prints `WARNING: SD_NTFY_URL is empty - notifications are journal-only` at startup if it is ever blank again. `--dry-run` now says "would open / would close". **106 tests** pass in WSL and in the CT 109 venv.
- **Verified end to end on the phone:** a manual test message, then "Feed health digest: 2 open gap(s)" sent by the real monitor through systemd. The journal of that run contained no warning line and no trace of the topic.

**CT 109 DNS — the two-resolver mitigation did not fix it.** `sd-deploy` failed again on a cold lookup with both resolvers active. A timed lookup took **10.037 s** and then succeeded: two back-to-back 5-second timeouts, one to each resolver, then an instant answer. The resolvers are fine; **the first UDP packet of each new flow is dropped after idle** — per destination, so it is not a one-time ARP miss. Something stateful in the path (double-NAT, flow offload / IDS, or the Proxmox bridge firewall) is the suspect; not established. TCP hides it by retransmitting within a second. The collector tolerates it (every fetch has a 60 s whole-call deadline); `sd-deploy` does not, because git gives up sooner than the resolver does.

**Gotchas learned tonight:**
- **A pasted command that ends in `read` swallows its own trailing newline** (Windows line endings: the second character is consumed as the input). `read -rs T` returned empty every time, invisibly. Use a loop that ignores empty input: `unset T; while [ -z "$T" ]; do read -rs T; done; echo "got ${#T} chars"`, then write with `sed -i "s|^SD_NTFY_URL=.*|SD_NTFY_URL=https://ntfy.sh/$T|" /etc/silicon-dominoes/collector.env; unset T`.
- Inspect the stored value without revealing it — read the file directly rather than sourcing it (a value with shell metacharacters could execute): `pct exec 109 -- python3 -c "import re; v=[l.split('=',1)[1].strip() for l in open('/etc/silicon-dominoes/collector.env') if l.startswith('SD_NTFY_URL=')][0]; t=v.rsplit('/',1)[-1]; print('len=%d' % len(t), 'valid=%s' % bool(re.fullmatch('[-_A-Za-z0-9]{1,64}', t)), 'prefix_ok=%s' % v.startswith('https://ntfy.sh/'))"`. ntfy topics allow only letters, digits, `-` and `_`, up to 64 characters.
- Status-only delivery test: `pct exec 109 -- bash -c "set -a; . /etc/silicon-dominoes/collector.env; set +a; curl -s -o /dev/null -m 15 -w '%{http_code}\n' -H 'Title: SD test' -d 'manual test' \"\$SD_NTFY_URL\""`. `200` with no buzz means the phone and the server hold different topic strings. Posting to a URL with an empty topic returns `400` ("request body must be valid JSON").
- PowerShell strips inner double quotes when handing a command to `wsl.exe`: a quoted `grep -E "a|b"` turns its `|` into shell pipes. Use `grep -e ^Ran -e ^OK -e ^FAILED -e ^FAIL: -e ^ERROR:` — no inner quotes, no `|` in the pattern.
- Two windows, two kinds of command: `pct exec ...` goes in the **Proxmox** SSH window (`root@homelab`); `git`, `wsl`, `type`, `findstr`, `python` go in **PowerShell** (`PS C:\...`). Instructions should label every block, and say what a step is for before giving the command.
- A log line that says something was sent is not evidence it arrived. Check the far end.

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

**The collector work is done: hang-proof (`2497b85`), every poll recorded (`bbb0ccf`), and a health monitor that reads that record, closes what clears, never mistakes "unknown" for "fine", and speaks only on change (`52a5147`). Back to the Phase 2 schema pass, which still precedes the review UI.**

0. **Check first (five minutes):**
   - ledger and monitor are quietly running: `select feed_id, outcome, count(*), max(finished_at) from feed_runs group by 1,2 order by 1,2` (expect about 12 `ok` rows per feed per day) and `journalctl -u sd-health.service --since '1 day ago' --no-pager | grep -c "done:"` as root (expect about 24);
   - the Monday 08:30 UTC digest arrived and lists exactly gaps 4 and 5;
   - close the unreviewed items: by hand, confirm `test_must_exit` fails with `daemon=True` removed from `fetcher.py` and restore it; read `present_feed_ids` / `sync_feeds` in `config.py` for the empty-config guard.
1. **Replace the two `REPLACE-ME` verify URLs** (Brian — analyst work): canonical S1 pages for WAICO membership and Pax Silica membership/fund status. They are now the only open health gaps and the digest will list them every Monday until fixed. Same pass: resolve the WAICO founding-date conflict (16 vs 17 July 2026) and verify pilot-country membership claims.
2. **Fixture-bloat refactor, as its own commit, before `f3-bundles`.** Must-reject cases become a delta over the passing fixture set. Done = the same 11 cases, same results under `validate.py --self-test`, fewer files.
3. **Branch `f3-bundles`: F-3 + doc 04 L-8/L-9 together** (shared `events.schema.json`). Done = self-test passes and each new must-reject case fails for its intended reason.
4. **F-6 text pass on doc 01** (`sovereign_pull`, `alignment_index`'s `f`, the `third`-is-residual-at-pole-level sentence). The `strategic_salience` note belongs in doc 06, which must be recovered first.
5. **`schema_version` decision:** already `2.0.0`; decide whether F-3/L-8 ride inside it (nothing has ever published) or go to 3.0.0. Record it here.
6. **Then the review UI**, designed against the 10 pending rows and captures 1274 and 1242.

**Standing, Brian's analyst work (not agent tasks):** source recruitment for a two-feed collection layer — at minimum one S1 and one in-country source per pilot country is the shape of the gap; VNM candidates already noted (VnExpress International, Vietnam Investment Review). New feeds are now safe to add: a bad one costs its own slot, not the run.

**Housekeeping (none blocks the above):**
- Extractor `--retry-failed` path for the 48 `LLM extraction failed — retry` rows, plus a distinct status.
- **Brian:** gap 9 closed automatically as "feed retired" on 2026-09-20, so Vietnam's coverage hole has no open record. Open an analyst gap (as `dominoes` or postgres): `INSERT INTO research_gaps (country_iso3, description, origin) VALUES ('VNM', 'No in-country source since rss-mic-vn was retired 2026-09-05; candidates: VnExpress International, Vietnam Investment Review', 'analyst');` The monitor never touches non-managed origins.
- Add `dominoes` to `systemd-journal`, or standardise on reading logs as root.
- `collection/sql/003_url_index.sql.txt` is a stray tracked duplicate: `fc` it against the `.sql`, then `git rm` if identical.

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

### Delta 2026-09-08 → 2026-09-20

**Closed:**
- ~~Corrected `entities.yaml` header commit~~ — landed in `e1edad9`.
- ~~Diagnose and fix GDELT 429~~ — feed retired `e1edad9`; root cause is upstream (see sixth session). Reopens only as the Web NGrams rebuild.
- ~~Check `rss-mic-vn` production-host probe~~ — feed retired `e1edad9`; see VNM coverage gap.
- ~~Fix or drop `rss-e27`~~ → decision made: drop. (Execution still open, below.)
- ~~Doc 07 F-2: controller registry, derived direction~~ — landed `b5cff84` together with F-7.
- ~~`must-reject` fixtures and explicit `synthetic` / `provisional` rejection in `validate.py`~~ — in place; 11 cases as of `b5cff84`.

**New:**
- **Recover docs 05 and 06 into `project-knowledge/`.** Doc 05 (Simple-mode lexicon v0.2) exists in the Claude Project knowledge store. Doc 06 (DIMEFIL provenance) is cited by doc 07 F-3, F-5, F-6 and F-7 and its location is unconfirmed. Until it is back, F-6 is half-blocked.
- Fixture-bloat refactor (next action 1).
- Retire `rss-e27` in `feeds.yaml` with dated inline rationale.
- Remove/disable `rss-techwireasia`, `rss-developingtelecoms`, `rss-imda-sg` until replacement URLs exist.
- `research_gaps` gap 9: set `country_iso3 = 'VNM'`; open a replacement-source gap for Vietnam (S2/S3, English, durable URLs).
- Extractor `--retry-failed` for the 48 LLM-failure rows; distinct status.
- Health-monitor baseline calibration (first-fill burst excluded).
- Grant `dominoes` the `systemd-journal` group or document root-only log reading.
- **Reversal coding:** the extractor has no path for reversal events (Thailand pause coded as T1 intent). The review UI must make "this is a reversal at tier N" a one-click correction; the extractor prompt should be taught the reversal class before Phase 4.
- **GDELT rebuild via Web NGrams** — parked behind Phase 2. Volume probe first. Needs a versioned domain→source-tier map authored by Brian (GDELT spans S3–S4 including state media), built behind a `structured_news` interface so the DOC API can return.
- **D/E structural gap.** The collection layer produces T (flow) only. D and E are stocks needing feed class 3 (Comtrade HS codes, tender portals, registries, cable DBs), specified in ARCHITECTURE §3 and not built. Working answer: hand-code a nine-country stock baseline against S1/S2 sources through the review queue, corroboration enforced, published as cycle zero. Analyst work, not an agent task.
- **WAICO founding-date conflict.** `SYSTEM_PROMPT.md` §1 says 16 July 2026; at least one S3/S4 source citing Xinhua/NDRC says 17 July. Resolve against S1 and flag in the first changelog per §1. Do it in the same pass as replacing the two `REPLACE-ME` verify URLs and verifying pilot-country WAICO / Pax Silica membership.

### Delta 2026-09-20 (eighth session)

**Closed:**
- ~~Retire `rss-e27`~~ and ~~remove/disable `rss-techwireasia`, `rss-developingtelecoms`, `rss-imda-sg`~~ — `8edc18c`.
- ~~Poller HTTP calls lack timeouts; units have no runtime ceiling; journal output buffered~~ — `2497b85`; unit files installed on CT 109 and the live-only drop-in removed.
- ~~`trafilatura` missing from `collection/requirements.txt`~~ — present (`trafilatura==2.2.0`); now guarded by `test_requirements`.
- ~~CT 109 single DNS resolver~~ — second resolver `9.9.9.9` active. Root cause of the first-lookup-after-idle failures still unknown.
- ~~Health-monitor baseline calibration~~ as a standalone item — folded into task B.

**New:**
- ~~Collector hardening task B1: `feed_runs` ledger, circuit breaker, healthy-first ordering, must-exit test~~ — `bbb0ccf`; 004 applied 2026-09-20.
- ~~Collector hardening task B2: rebuild `feed_health.py` on the ledger~~ — `52a5147`; units installed, first live run 2026-09-20 21:54 UTC closed gaps 1–3 and 6–9.
- A rebuilt GDELT (or any non-RSS) poller must write `feed_runs`, or the ledger-based health rules cannot see it.
- ~~Cosmetic: `feed_health --dry-run` ends with `done: ... N closed`~~ — fixed in `9bf5f72`.
- ~~Notifications: `SD_NTFY_URL` empty since 2026-08-16; `notify()` reported attempts as deliveries~~ — wired to ntfy.sh and fixed in `9bf5f72`; verified on the phone 2026-09-20.
- Add retries to `sd-deploy` (three attempts a few seconds apart): every DNS failure so far has been one cold lookup followed by success.
- CT 109 DNS root cause: time a cold `getent hosts github.com` from the Proxmox host and from one other container after idle. 10 s on all of them means the router path (UDR7 / double-NAT list); only CT 109 means its own network config.
- `feed_health --digest` does not log whether its notification was delivered; `run()` does. Small follow-up.
- Optional: self-host ntfy on the tailnet instead of `ntfy.sh` (CT 109 must reach it by LAN IP — MagicDNS does not resolve inside LXC).
- `CLAUDE.md` §1 names the grantee role as `dominoes`; privileges are held by `sd_pipeline` (dominoes is a member). Brian to correct the wording.
- The two `REPLACE-ME` verify URLs now have a cost on record: they make the daily health notification permanently non-empty.
- ~~Expect new false `research_gaps` for the four retired feeds~~ — wrong: each already holds its one open gap and nothing ever closes them. Gaps 1–3 and 6–9 are stale or false; B2 closes them by rule rather than by hand.
- **Source recruitment:** the live feed set is two S2 English trade-press feeds. No S1, no in-country source for any pilot country.
- `rss-imda-sg` is a candidate for `verify_items`-style page-change detection rather than RSS (its URL is an HTML index page).
- CT 109 DNS: if `sd-deploy` fails on resolution again with two resolvers configured, move the problem to the UDR7 / double-NAT list.
- Stray tracked file `collection/sql/003_url_index.sql.txt`.

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
