"""Poll RSS/Atom feeds; archive every new article verbatim (system prompt §3.1).

Per feed: parse the feed, and for each entry not yet captured, fetch the
article and store the raw response bytes plus a raw_captures index row.
If the article fetch fails, the entry's own metadata is captured instead
(parse_status='entry_only') so the item and its true date are not lost.

Every feed gets a wall-clock budget (config.FEED_BUDGET, checked before each
article fetch) so one slow or hostile host can never consume the whole run —
2026-09-19: sd-rss hung 18 hours on a single feed that accepted a connection
and never responded. On exhaustion the feed stops early and logs how many
URLs it left behind; they are picked up next poll by the existing
(feed_id, url) dedup, nothing is lost. A feed-level fetch failure never
prevents the remaining feeds from running.

Run:  python -m collector.poll_rss
"""
from __future__ import annotations

import sys
import time
import traceback

import feedparser

from . import common, config, fetcher


def fetch_article(url: str) -> bytes | None:
    try:
        resp = fetcher.get(url, headers={"User-Agent": config.USER_AGENT})
        if resp.ok and resp.content:
            return resp.content
    except fetcher.FetchError:
        pass
    return None


class DbStore:
    """Default store: the real database, via the psycopg connection main()
    already opened. See FakeStore in collection/tests for the in-memory
    double used by the poll_feed/run tests — poll_feed and run never touch
    a connection or the common module's DB helpers directly, only this
    interface, so the tests need no Postgres."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def already_captured(self, feed_id: str, url: str) -> bool:
        return common.url_already_captured(self._conn, feed_id, url)

    def record_capture(self, feed_id: str, url: str, payload: bytes, ext: str,
                       status: str, snapshot_id: str | None,
                       snapshot_url: str | None) -> bool:
        return common.insert_capture(
            self._conn, feed_id=feed_id, url=url, payload=payload, ext=ext,
            parse_status=status, snapshot_id=snapshot_id, snapshot_url=snapshot_url)


def poll_feed(feed: dict, store) -> tuple[int, int, str | None]:
    """Returns (entries_seen, new_captures, error)."""
    feed_id, url = feed["feed_id"], feed["url"]
    dedupe_on = feed.get("dedupe_on", "url")
    skipped = 0

    try:
        raw = fetcher.get(url, headers={"User-Agent": config.USER_AGENT}).content
    except fetcher.FetchError as exc:
        return 0, 0, f"feed fetch failed: {exc}"

    parsed = feedparser.parse(raw)
    if parsed.get("bozo") and not parsed.entries:
        return 0, 0, f"unparseable feed: {parsed.get('bozo_exception')}"

    new = 0
    start = time.monotonic()
    entries = parsed.entries
    for i, entry in enumerate(entries):
        if time.monotonic() - start > config.FEED_BUDGET:
            print(f"[{feed_id}] outcome=budget_exhausted urls_remaining={len(entries) - i}")
            break
        link = entry.get("link")
        if not link:
            continue
        if dedupe_on == "url" and store.already_captured(feed_id, link):
            skipped += 1
            continue
        payload = fetch_article(link)
        if payload is not None:
            status, ext = "captured", "html"
        else:
            payload = repr({k: entry.get(k) for k in
                            ("title", "link", "published", "summary")}).encode()
            status, ext = "entry_only", "txt"
        snap_id, snap_url = common.wayback_submit(link) if payload else (None, None)
        if store.record_capture(feed_id, link, payload, ext, status, snap_id, snap_url):
            new += 1
    if skipped:
        print(f"[{feed_id}] skipped {skipped} already-captured URL(s)")
    return len(parsed.entries), new, None


def run(cfg: dict, store) -> tuple[int, list[tuple[str, str]]]:
    """Poll every rss-class feed in cfg. Returns (total_new, failures). A
    feed that raises is recorded as a failure by feed_id and never stops the
    remaining feeds — this is the loop the tests drive directly with a
    FakeStore, no database required."""
    failures: list[tuple[str, str]] = []
    total_new = 0
    for feed in cfg.get("feeds", []):
        if feed["feed_class"] != "rss":
            continue
        try:
            seen, new, err = poll_feed(feed, store)
            total_new += new
            print(f"[{feed['feed_id']}] entries={seen} new={new}"
                  + (f" ERROR={err}" if err else ""))
            if err:
                failures.append((feed["feed_id"], err))
        except Exception as exc:            # one bad feed never stops the run
            traceback.print_exc()
            failures.append((feed["feed_id"], str(exc)))
    return total_new, failures


def main() -> int:
    cfg = config.load_feeds_config()
    conn = common.connect()
    config.sync_feeds(conn, cfg)
    store = DbStore(conn)

    total_new, failures = run(cfg, store)

    if failures:
        common.notify(
            "Collector: RSS poll had failures",
            "; ".join(f"{fid}: {err[:120]}" for fid, err in failures),
            priority="high", tags="warning",
        )
    print(f"done: {total_new} new capture(s), {len(failures)} feed failure(s)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
