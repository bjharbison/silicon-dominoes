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


def sync_feeds(conn, cfg: dict) -> None:
    """Upsert feeds.yaml definitions into the (mutable, by design) feeds
    table, reactivating any that return, and retire (active = false) every
    existing row whose feed_id is no longer present in feeds.yaml —
    feed_health.py evaluates active feeds only, so this is what lets a
    removed feed's open gaps close as 'feed retired' instead of being
    checked forever (the pre-harden-health bug: all ten feeds rows stayed
    active = t no matter what feeds.yaml said)."""
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
