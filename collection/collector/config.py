"""Silicon Dominoes collector — shared configuration.

Environment (from /etc/silicon-dominoes/collector.env via systemd EnvironmentFile):
  SD_DB_URL       postgresql:///silicon_dominoes   (peer auth over unix socket)
  SD_ARCHIVE_DIR  /mnt/nas-archive/silicon-dominoes (NFS mount from the Synology)
  SD_NTFY_URL     https://ntfy.sh/<topic> or self-hosted ntfy endpoint ('' disables)
  SD_WAYBACK      1 to submit URLs to the Wayback Machine at capture time
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
FETCH_TIMEOUT = 30


def load_feeds_config() -> dict:
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_facet(name: str) -> dict:
    with open(FACETS_DIR / f"{name}.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sync_feeds(conn, cfg: dict) -> None:
    """Upsert feeds.yaml definitions into the (mutable, by design) feeds table."""
    rows = []
    for feed in cfg.get("feeds", []):
        rows.append((feed["feed_id"], feed["feed_class"], feed.get("url")))
    for item in cfg.get("verify_items", []):
        rows.append((item["feed_id"], "watchlist", item["url"]))
    with conn.cursor() as cur:
        for feed_id, feed_class, url in rows:
            cur.execute(
                """
                INSERT INTO feeds (feed_id, feed_class, url)
                VALUES (%s, %s, %s)
                ON CONFLICT (feed_id)
                DO UPDATE SET url = EXCLUDED.url, updated_at = now()
                """,
                (feed_id, feed_class, url),
            )
    conn.commit()


# --------------------------------------------------------------------- LLM --
# Step 4a extraction. Routed through LiteLLM on CT 102 to the Mac mini per the
# homelab principle (Mac = inference only). Temperature is fixed at 0 in
# extract.py, not configurable — reproducibility is a requirement, not a knob.
LLM_BASE = os.environ.get("SD_LLM_BASE", "http://192.168.1.190:4000")
LLM_MODEL = os.environ.get("SD_LLM_MODEL", "qwen3.6:35b-mlx")
LLM_KEY = os.environ.get("SD_LLM_KEY", "")
LLM_TIMEOUT = int(os.environ.get("SD_LLM_TIMEOUT", "180"))
