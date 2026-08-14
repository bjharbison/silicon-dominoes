"""Poll RSS/Atom feeds; archive every new article verbatim (system prompt §3.1).

Per feed: parse the feed, and for each entry not yet captured, fetch the
article and store the raw response bytes plus a raw_captures index row.
If the article fetch fails, the entry's own metadata is captured instead
(parse_status='entry_only') so the item and its true date are not lost.

Run:  python -m collector.poll_rss
"""
from __future__ import annotations

import sys
import traceback

import feedparser
import requests

from . import common, config


def fetch_article(url: str) -> bytes | None:
    try:
        resp = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                            timeout=config.FETCH_TIMEOUT)
        if resp.ok and resp.content:
            return resp.content
    except requests.RequestException:
        pass
    return None


def poll_feed(conn, feed: dict) -> tuple[int, int, str | None]:
    """Returns (entries_seen, new_captures, error)."""
    feed_id, url = feed["feed_id"], feed["url"]
    parsed = feedparser.parse(url, agent=config.USER_AGENT)
    if parsed.get("bozo") and not parsed.entries:
        return 0, 0, f"unparseable feed: {parsed.get('bozo_exception')}"

    new = 0
    for entry in parsed.entries:
        link = entry.get("link")
        if not link:
            continue
        payload = fetch_article(link)
        if payload is not None:
            status, ext = "captured", "html"
        else:
            payload = repr({k: entry.get(k) for k in
                            ("title", "link", "published", "summary")}).encode()
            status, ext = "entry_only", "txt"
        snap_id, snap_url = common.wayback_submit(link) if payload else (None, None)
        if common.insert_capture(conn, feed_id=feed_id, url=link, payload=payload,
                                 ext=ext, parse_status=status,
                                 snapshot_id=snap_id, snapshot_url=snap_url):
            new += 1
    return len(parsed.entries), new, None


def main() -> int:
    cfg = config.load_feeds_config()
    conn = common.connect()
    config.sync_feeds(conn, cfg)

    failures = []
    total_new = 0
    for feed in cfg.get("feeds", []):
        if feed["feed_class"] != "rss":
            continue
        try:
            seen, new, err = poll_feed(conn, feed)
            total_new += new
            print(f"[{feed['feed_id']}] entries={seen} new={new}"
                  + (f" ERROR={err}" if err else ""))
            if err:
                failures.append((feed["feed_id"], err))
        except Exception as exc:            # one bad feed never stops the run
            traceback.print_exc()
            failures.append((feed["feed_id"], str(exc)))

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
