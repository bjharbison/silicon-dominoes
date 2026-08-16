"""Silicon Dominoes collector — small shared helpers (db, archive, wayback, ntfy)."""
from __future__ import annotations

import gzip
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import requests

from . import config

# ---------------------------------------------------------------- database --
def connect() -> psycopg.Connection:
    # The database was created with SQL_ASCII encoding, under which psycopg
    # cannot infer a text encoding and returns every text column as bytes.
    # Pinning the client encoding makes text arrive as str everywhere.
    return psycopg.connect(config.DB_URL, client_encoding="utf8")


# ----------------------------------------------------------------- archive --
def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_archive(feed_id: str, payload: bytes, ext: str = "bin") -> str:
    """Write payload to the content-addressed raw archive; return the object_key
    (path relative to SD_ARCHIVE_DIR). Idempotent: same content = same path."""
    digest = sha256_hex(payload)
    now = datetime.now(timezone.utc)
    rel = (Path("raw") / feed_id / f"{now:%Y}" / f"{now:%m}" / digest[:2]
           / f"{digest}.{ext}.gz")
    dest = config.ARCHIVE_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(gzip.compress(payload))
        tmp.rename(dest)
    return str(rel)


def insert_capture(conn, *, feed_id: str, url: str, payload: bytes, ext: str,
                   parse_status: str, snapshot_id: str | None = None,
                   snapshot_url: str | None = None) -> bool:
    """Archive payload and insert the raw_captures index row.
    Returns True if this is NEW content (row inserted), False if the
    (feed_id, sha256) pair was already captured — the dedupe rule."""
    digest = sha256_hex(payload)
    object_key = write_archive(feed_id, payload, ext)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_captures
              (feed_id, retrieved_at, url, sha256, object_key,
               snapshot_id, snapshot_url, parse_status)
            VALUES (%s, now(), %s, %s, %s, %s, %s, %s)
            ON CONFLICT (feed_id, sha256) DO NOTHING
            """,
            (feed_id, url, digest, object_key, snapshot_id, snapshot_url, parse_status),
        )
        inserted = cur.rowcount == 1
        cur.execute("UPDATE feeds SET last_capture_at = now() WHERE feed_id = %s", (feed_id,))
    conn.commit()
    return inserted


def url_already_captured(conn, feed_id: str, url: str) -> bool:
    """True if this (feed_id, url) was ever captured. Used to skip re-fetching
    news articles whose bytes change on every render (request UIDs, ad slots)
    while their content does not. Feeds whose URL is stable but whose content
    is the signal (membership pages, tender portals) set dedupe_on: content
    in feeds.yaml and are exempt."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM raw_captures WHERE feed_id = %s AND url = %s LIMIT 1",
            (feed_id, url),
        )
        return cur.fetchone() is not None


def latest_sha(conn, feed_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sha256 FROM raw_captures WHERE feed_id = %s "
            "ORDER BY retrieved_at DESC LIMIT 1",
            (feed_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


# ----------------------------------------------------------------- wayback --
_last_submit = 0.0
_MIN_INTERVAL_S = 12  # be polite to the Save Page Now endpoint


def wayback_submit(url: str) -> tuple[str | None, str | None]:
    """Best-effort submission to the Wayback Machine. Returns
    (snapshot_id, snapshot_url) or (None, None). Never raises."""
    if not config.WAYBACK_ENABLED:
        return None, None
    global _last_submit
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_submit)
    if wait > 0:
        time.sleep(wait)
    _last_submit = time.monotonic()
    try:
        resp = requests.get(
            "https://web.archive.org/save/" + url,
            headers={"User-Agent": config.USER_AGENT},
            timeout=90, allow_redirects=True,
        )
        final = resp.url or ""
        if "/web/" in final:
            ts = final.split("/web/")[1].split("/")[0]
            return ts, final
    except requests.RequestException:
        pass
    return None, None


# -------------------------------------------------------------------- ntfy --
def notify(title: str, message: str, priority: str = "default",
           tags: str = "satellite") -> None:
    """Send an ntfy notification; logs to stdout and never raises."""
    print(f"[notify] {title}: {message}")
    if not config.NTFY_URL:
        return
    try:
        requests.post(
            config.NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags,
                     "User-Agent": config.USER_AGENT},
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"[notify] delivery failed: {exc}")
