"""Silicon Dominoes collector — shared configuration.

Environment (from /etc/silicon-dominoes/collector.env via systemd EnvironmentFile):
  SD_DB_URL       postgresql:///silicon_dominoes   (peer auth over unix socket)
  SD_ARCHIVE_DIR  /mnt/nas-archive/silicon-dominoes (NFS mount from the Synology)
  SD_NTFY_URL     https://ntfy.sh/<topic> or self-hosted ntfy endpoint ('' disables)
  SD_WAYBACK      1 to submit URLs to the Wayback Machine at capture time

  SD_HTTP_CONNECT_TIMEOUT  seconds to establish a TCP/TLS connection (default 10)
  SD_HTTP_READ_TIMEOUT     seconds of silence on the socket before giving up (default 30)
  SD_FETCH_DEADLINE        total wall-clock seconds for one collector.fetcher.fetch()
                           call — connect, waiting for headers, and reading the
                           body all count against it (default 60)
  SD_FETCH_MAX_BYTES       response body cap in bytes (default 10485760 / 10 MiB)
  SD_FEED_BUDGET           wall-clock seconds poll_rss spends per feed before
                           moving on and leaving the rest for next poll (default 300)
  SD_LLM_DEADLINE          total wall-clock seconds for the extract.py LLM call
                           (default 300)
  SD_BREAKER_THRESHOLD     consecutive failed/timeout runs before a feed is
                           quarantined by collector/breaker.py (default 5)
  SD_BREAKER_PROBE_H       hours between quarantine probes (default 24)
  SD_POLL_STALE_H          hours since an RSS feed's newest feed_runs row (any
                           outcome) before feed_health.py calls it poll-stale
                           (default 6)
  SD_COLLECTOR_DOWN_RENOTIFY_H  hours between re-notifications while a
                           collector_down gap stays open (default 6)
  SD_GDELT_BUDGET          wall-clock seconds poll_gdelt spends fetching
                           article bodies per invocation, mirroring
                           SD_FEED_BUDGET's role in poll_rss (default 300)
  SD_GDELT_QUERY_MIN_GAP_DAYS  days a kind: gdelt query must wait after an
                           'ok' run before it is eligible to run again —
                           GDELT DOC 2.0 results are retroactive, so
                           polling a query more often than this buys no
                           additional coverage, only additional risk of a
                           throttle (default 7)
  SD_GDELT_COOLDOWN_H      hours after any kind: gdelt query is throttled
                           before ANY kind: gdelt query is attempted again
                           — the block is sticky and IP-wide (default 24)
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PKG_ROOT = Path(__file__).resolve().parent.parent   # .../collection
FEEDS_FILE = PKG_ROOT / "feeds.yaml"
FACETS_DIR = PKG_ROOT / "facets"

DB_URL = os.environ.get("SD_DB_URL", "postgresql:///silicon_dominoes")
ARCHIVE_DIR = Path(os.environ.get("SD_ARCHIVE_DIR", "/var/lib/silicon-dominoes/archive"))
NTFY_URL = os.environ.get("SD_NTFY_URL", "")
WAYBACK_ENABLED = os.environ.get("SD_WAYBACK", "1") == "1"

USER_AGENT = "SiliconDominoesCollector/0.1 (open-source research; contact via repo)"

# ---------------------------------------------------------- bounded fetch --
# Every outbound call goes through collector.fetcher.fetch(), which enforces
# all four of these. Read fresh on every call (never cached into another
# module's globals at import time) so tests can override by patching this
# module's attributes directly. See collector/fetcher.py for why the total
# deadline exists alongside the read timeout: a server trickling one byte
# every few seconds never trips a read timeout (2026-09-19 incident).
HTTP_CONNECT_TIMEOUT = float(os.environ.get("SD_HTTP_CONNECT_TIMEOUT", "10"))
HTTP_READ_TIMEOUT = float(os.environ.get("SD_HTTP_READ_TIMEOUT", "30"))
FETCH_DEADLINE = float(os.environ.get("SD_FETCH_DEADLINE", "60"))
FETCH_MAX_BYTES = int(os.environ.get("SD_FETCH_MAX_BYTES", str(10 * 1024 * 1024)))

# Per-feed wall-clock budget in poll_rss: a feed that can't finish its
# article fetches within this many seconds is stopped (not failed) and the
# remaining URLs are left for the next poll via the existing (feed_id, url)
# dedup — never silently dropped.
FEED_BUDGET = float(os.environ.get("SD_FEED_BUDGET", "300"))

# ------------------------------------------------------------------ breaker --
# collector/breaker.py: a feed that fails/times out this many consecutive
# REAL (non-skipped) polls in a row is quarantined — further runs skip it
# without attempting a connection until one probe succeeds, tried at most
# once per BREAKER_PROBE_H. breaker.py itself takes these as explicit
# arguments (it does no I/O, including no config import); poll_rss.py is the
# only reader of these two names.
BREAKER_THRESHOLD = int(os.environ.get("SD_BREAKER_THRESHOLD", "5"))
BREAKER_PROBE_H = float(os.environ.get("SD_BREAKER_PROBE_H", "24"))

# -------------------------------------------------------------------- health --
# collector/feed_health.py + collector/health_rules.py. Like breaker.py,
# health_rules.py takes these as explicit arguments rather than importing
# config — it does no I/O at all.
POLL_STALE_H = float(os.environ.get("SD_POLL_STALE_H", "6"))
COLLECTOR_DOWN_RENOTIFY_H = float(os.environ.get("SD_COLLECTOR_DOWN_RENOTIFY_H", "6"))

# --------------------------------------------------------------------- gdelt --
# collector/poll_gdelt.py + collector/breaker.py's gdelt_cooldown_active.
# Like BREAKER_THRESHOLD/BREAKER_PROBE_H above, these are read here and
# passed in explicitly — gdelt_cooldown_active takes no config import, only
# arguments, same discipline as breaker.decide().
GDELT_BUDGET = float(os.environ.get("SD_GDELT_BUDGET", "300"))
GDELT_QUERY_MIN_GAP_DAYS = float(os.environ.get("SD_GDELT_QUERY_MIN_GAP_DAYS", "7"))
GDELT_COOLDOWN_H = float(os.environ.get("SD_GDELT_COOLDOWN_H", "24"))

# ----------------------------------------------------------------------- ntfy --
# common.notify()'s own connect/read timeout and total deadline — smaller
# than the general HTTP_* defaults on purpose (a notification should never
# block a whole collector run for as long as an ordinary fetch is allowed
# to). One value for both: notify()'s payload is tiny, so there's no
# meaningful difference between "slow to connect" and "slow overall" here.
NTFY_TIMEOUT = float(os.environ.get("SD_NTFY_TIMEOUT", "15"))


def load_feeds_config() -> dict:
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_facet(name: str) -> dict:
    with open(FACETS_DIR / f"{name}.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def present_feed_ids(cfg: dict) -> set[str]:
    """feed_ids that feeds.yaml currently declares — both `feeds:` and
    `verify_items:` count as present. Pure set logic, no I/O: this is what
    sync_feeds uses to decide which existing `feeds` rows to retire, kept
    separate so it's directly testable without a database."""
    ids = {feed["feed_id"] for feed in cfg.get("feeds", [])}
    ids |= {item["feed_id"] for item in cfg.get("verify_items", [])}
    return ids


# ---------------------------------------------------------------- kind --
# A `feeds:` entry's `kind` (gdelt-slow) selects which poller acts on it —
# poll_rss only 'rss', poll_gdelt only 'gdelt' — orthogonal to `feed_class`
# (still read separately by sync_feeds below, unchanged, for the `feeds`
# table's DB column). Absent `kind` means 'rss', for backward compatibility
# with every feeds.yaml entry that predates this field.
VALID_KINDS = frozenset({"rss", "gdelt"})


def feed_kind(feed: dict) -> str:
    """feed['kind'], defaulting to 'rss'. Assumes validate_feed_kinds() has
    already run (sync_feeds calls it first, before any SQL) — does not
    itself raise, so a caller that skips validation and hands this a bad
    kind just gets the bad string back rather than a KeyError; the actual
    hard-stop lives in validate_feed_kinds()."""
    return feed.get("kind", "rss")


# kind <-> feed_class must agree. Pollers select rows purely by `kind`
# (poll_rss only kind: rss, poll_gdelt only kind: gdelt); feed_class is a
# separate, pre-existing DB enum (see sync_feeds below) that feed_health.py
# and everything else reading the `feeds` table relies on for monitoring
# and display, and has no idea `kind` even exists. Before this map, nothing
# made the two agree: a feeds.yaml entry with kind: gdelt but a typo'd
# feed_class: rss would poll exactly as intended while being monitored and
# displayed as an ordinary RSS feed everywhere else — a silent split
# between what actually runs and what the rest of the system believes is
# running.
KIND_TO_FEED_CLASS = {"rss": "rss", "gdelt": "structured_news"}


def validate_feed_kinds(cfg: dict) -> None:
    """Every `feeds:` entry's kind must be 'rss', 'gdelt', or absent
    (-> 'rss'), and: kind and feed_class must agree per KIND_TO_FEED_CLASS
    (see its own comment for why), and a kind: gdelt entry must carry a
    non-empty string `query` (poll_gdelt.py's _gdelt_params reads
    feed['query'] directly, with no default — a missing/blank one must
    fail here, loudly, rather than surface later as a KeyError or an empty
    request body deep inside a live poll). All three are hard errors,
    raised here — BEFORE sync_feeds touches SQL — so a bad feeds.yaml entry
    can never silently become a path by which sync_feeds retires a live
    feed, or by which a poller and the rest of the system quietly disagree
    about what a feed even is. This is the same class of residual already
    accepted for a partially broken feeds.yaml (present_feed_ids()
    returning empty skips the retirement UPDATE rather than retiring
    everything) — deliberately NOT widened to also swallow any of these
    silently; all three abort instead."""
    for feed in cfg.get("feeds", []):
        feed_id = feed.get("feed_id")
        kind = feed.get("kind", "rss")
        if kind not in VALID_KINDS:
            raise ValueError(
                f"feeds.yaml: feed_id {feed_id!r} has unrecognised "
                f"kind {kind!r} (expected one of {sorted(VALID_KINDS)} or omitted)")

        expected_feed_class = KIND_TO_FEED_CLASS[kind]
        feed_class = feed.get("feed_class")
        if feed_class != expected_feed_class:
            raise ValueError(
                f"feeds.yaml: feed_id {feed_id!r} has kind {kind!r} but "
                f"feed_class {feed_class!r} (expected {expected_feed_class!r} — "
                f"kind and feed_class must agree, see config.KIND_TO_FEED_CLASS)")

        if kind == "gdelt":
            query = feed.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(
                    f"feeds.yaml: feed_id {feed_id!r} has kind 'gdelt' but no "
                    f"non-empty 'query' string")


def sync_feeds(conn, cfg: dict) -> None:
    """Upsert feeds.yaml definitions into the (mutable, by design) feeds
    table, reactivating any that return, and retire (active = false) every
    existing row whose feed_id is no longer present in feeds.yaml —
    feed_health.py evaluates active feeds only, so this is what lets a
    removed feed's open gaps close as 'feed retired' instead of being
    checked forever (the pre-harden-health bug: all ten feeds rows stayed
    active = t no matter what feeds.yaml said)."""
    validate_feed_kinds(cfg)             # before any SQL — see its docstring
    rows = []
    for feed in cfg.get("feeds", []):
        rows.append((feed["feed_id"], feed["feed_class"], feed.get("url")))
    for item in cfg.get("verify_items", []):
        rows.append((item["feed_id"], "watchlist", item["url"]))
    present = present_feed_ids(cfg)
    with conn.cursor() as cur:
        for feed_id, feed_class, url in rows:
            cur.execute(
                """
                INSERT INTO feeds (feed_id, feed_class, url, active)
                VALUES (%s, %s, %s, true)
                ON CONFLICT (feed_id)
                DO UPDATE SET url = EXCLUDED.url, active = true, updated_at = now()
                """,
                (feed_id, feed_class, url),
            )
        if present:
            cur.execute(
                "UPDATE feeds SET active = false, updated_at = now() "
                "WHERE active AND NOT (feed_id = ANY(%s))",
                (list(present),),
            )
        else:
            # present_feed_ids(cfg) == set() almost certainly means
            # feeds.yaml failed to parse into what was expected (empty or
            # malformed), not "retire every feed" — skip the deactivation
            # rather than act on it.
            print("sync_feeds: feeds.yaml declares zero feed_ids — skipping "
                 "the retirement UPDATE (this looks like a broken config, "
                 "not an instruction to retire everything)")
    conn.commit()


# --------------------------------------------------------------------- LLM --
# Step 4a extraction. Routed through LiteLLM on CT 102 to the Mac mini per the
# homelab principle (Mac = inference only). Temperature is fixed at 0 in
# extract.py, not configurable — reproducibility is a requirement, not a knob.
LLM_BASE = os.environ.get("SD_LLM_BASE", "http://192.168.1.190:4000")
LLM_MODEL = os.environ.get("SD_LLM_MODEL", "qwen3.6:35b-mlx")
LLM_KEY = os.environ.get("SD_LLM_KEY", "")
# Total wall-clock budget for one call, not a read timeout: a local model
# generating a long completion sends nothing back until it's done, so the
# call's read_timeout is set equal to this rather than the short default —
# see extract.call_llm().
LLM_DEADLINE = float(os.environ.get("SD_LLM_DEADLINE", "300"))
