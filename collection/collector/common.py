"""Silicon Dominoes collector — small shared helpers (db, archive, wayback, ntfy)."""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from . import config, fetcher

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
                   snapshot_url: str | None = None,
                   metadata: dict | None = None) -> bool:
    """Archive payload and insert the raw_captures index row.
    Returns True if this is NEW content (row inserted), False if the
    (feed_id, sha256) pair was already captured — the dedupe rule.

    `metadata` is optional, per-capture provenance as a plain dict (e.g.
    poll_gdelt.py's query_id/seendate/sourcecountry/language/domain) —
    unused by RSS callers, added for gdelt-slow. Dumped with
    ensure_ascii=False, matching extract.py's enqueue(): this database is
    SQL_ASCII and rejects \\u escapes above 0x7F in jsonb, so json.dumps'
    default ensure_ascii=True output can fail to insert on non-ASCII
    content (e.g. a non-Latin sourcecountry or domain).

    The `metadata` column only exists once collection/sql/005_gdelt.sql
    has been applied — every earlier schema lacks it entirely. The INSERT
    below therefore names the column ONLY when metadata is not None: every
    RSS caller passes metadata=None (the default) and must keep working
    against an un-migrated database exactly as before, so the column can
    never appear in the statement text unless a caller actually asked for
    it. Passing a real metadata dict against an un-migrated database still
    fails at INSERT time, same as before — that part is unavoidable and
    correct; what changed is that NOT passing one no longer fails too."""
    digest = sha256_hex(payload)
    object_key = write_archive(feed_id, payload, ext)
    # retrieved_at is always now() — a SQL literal, not a parameter — kept
    # as its own fixed column/placeholder pair, separate from the
    # parameterized columns below so the two lists can never drift out of
    # step with each other.
    columns = ["feed_id", "retrieved_at", "url", "sha256", "object_key",
              "snapshot_id", "snapshot_url", "parse_status"]
    placeholders = ["%s", "now()", "%s", "%s", "%s", "%s", "%s", "%s"]
    values = [feed_id, url, digest, object_key, snapshot_id, snapshot_url, parse_status]
    if metadata is not None:
        columns.append("metadata")
        placeholders.append("%s")
        values.append(json.dumps(metadata, ensure_ascii=False))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO raw_captures ({", ".join(columns)})
            VALUES ({", ".join(placeholders)})
            ON CONFLICT (feed_id, sha256) DO NOTHING
            """,
            tuple(values),
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
        # Save Page Now can be slow; give it its own longer allowance rather
        # than the general-purpose defaults.
        resp = fetcher.get(
            "https://web.archive.org/save/" + url,
            headers={"User-Agent": config.USER_AGENT},
            allow_redirects=True, read_timeout=90, deadline=90,
        )
        final = resp.url or ""
        if "/web/" in final:
            ts = final.split("/web/")[1].split("/")[0]
            return ts, final
    except fetcher.FetchError:
        pass
    return None, None


# -------------------------------------------------------------------- ntfy --
def _redact_ntfy_url(text: str) -> str:
    """Never let SD_NTFY_URL — or any recognizable fragment of it — reach a
    printed line, even via an exception message that happens to embed it.
    Our own fetcher exceptions always embed the full URL (f"{method} {url}:
    ..."); a lower-level requests/urllib3 exception might embed just the
    host, the path, or (ntfy's URL shape is host/topic) the bare topic name
    with no leading slash at all — e.g. an error string built from parsing
    the URL itself rather than repeating it verbatim. All four are redacted
    independently. The bare topic is only redacted when it's at least 8
    characters: shorter than that it's too likely to be an ordinary
    substring of unrelated text (redacting every occurrence of "a" would
    mangle everything)."""
    url = config.NTFY_URL
    if not url:
        return text
    redacted = text.replace(url, "<SD_NTFY_URL>")
    parsed = urlparse(url)
    if parsed.netloc:
        redacted = redacted.replace(parsed.netloc, "<SD_NTFY_URL>")
    if parsed.path and parsed.path != "/":
        redacted = redacted.replace(parsed.path, "<SD_NTFY_URL>")
        topic = parsed.path.lstrip("/")
        if len(topic) >= 8:
            redacted = redacted.replace(topic, "<SD_NTFY_URL>")
    return redacted


def _sanitize_header_value(value: str) -> str:
    """HTTP header values are latin-1 (ISO-8859-1) — code points 0-255
    only. The ntfy Title header carries the alert title verbatim, which is
    free text someone typed and can contain characters outside that range
    (an em dash, a smart quote, ...); left unsanitized, requests raises a
    UnicodeEncodeError trying to put it on the wire, and notify() would
    report a perfectly good alert as a delivery failure over one character.
    Anything unencodable becomes '-'; the message body is unaffected — it's
    sent as UTF-8 bytes in the request body, not a header."""
    return "".join(c if ord(c) <= 0xFF else "-" for c in value)


def notify(title: str, message: str, priority: str = "default",
           tags: str = "satellite") -> bool:
    """Send an ntfy notification. Returns True only if the server actually
    answered 2xx — SD_NTFY_URL was empty in production from 2026-08-16 to
    2026-09-20, during which every call here printed its "[notify] ..."
    line and returned, so journals and feed_health's old notified=True read
    as if alerts had gone out when not one ever had. A caller that cares
    whether an alert was actually delivered (not just attempted) must check
    the return value; never raises regardless of what goes wrong."""
    print(f"[notify] {title}: {message}")
    if not config.NTFY_URL:
        print(f"[notify] NOT SENT (SD_NTFY_URL is empty): {title}")
        return False
    try:
        resp = fetcher.post(
            config.NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": _sanitize_header_value(title), "Priority": priority,
                     "Tags": tags, "User-Agent": config.USER_AGENT},
            read_timeout=config.NTFY_TIMEOUT, deadline=config.NTFY_TIMEOUT,
        )
    except Exception as exc:            # noqa: BLE001 — notify() must never raise
        detail = _redact_ntfy_url(f"{type(exc).__name__}: {exc}")
        print(f"[notify] delivery failed: {detail}")
        return False
    if not (200 <= resp.status_code < 300):
        print(f"[notify] delivery refused: HTTP {resp.status_code}")
        return False
    return True


def warn_if_ntfy_unconfigured() -> None:
    """Call once near the top of every collector entry point's main() —
    poll_rss, feed_health, verify_watch, snapshot_retry, poll_gdelt,
    extract. One shared check instead of six copies of the same `if not
    config.NTFY_URL` guard."""
    if not config.NTFY_URL:
        print("WARNING: SD_NTFY_URL is empty - notifications are journal-only")
