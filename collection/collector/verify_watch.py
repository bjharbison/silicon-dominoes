"""Verify-at-every-run items: WAICO and Pax Silica membership status
(system prompt §1, §4 — a missed verification is itself an alert).

Each check fetches the configured page, archives it as a capture, and
compares the content hash against the previous capture for that item:
  - fetch failure          -> immediate high-priority ntfy alert
  - content changed        -> ntfy alert ("membership page changed — review")
  - staleness (no success within max_gap_days) is handled by feed_health.

Run:  python -m collector.verify_watch
"""
from __future__ import annotations

import sys

import requests

from . import common, config


def main() -> int:
    cfg = config.load_feeds_config()
    conn = common.connect()
    config.sync_feeds(conn, cfg)

    failures = 0
    for item in cfg.get("verify_items", []):
        feed_id, url, label = item["feed_id"], item["url"], item.get("label", item["feed_id"])
        try:
            resp = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                                timeout=config.FETCH_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            failures += 1
            common.notify(f"VERIFY FAILED: {label}",
                          f"{url} — {exc}. Membership status is unverified this run.",
                          priority="high", tags="rotating_light")
            continue

        prev = common.latest_sha(conn, feed_id)
        new = common.insert_capture(conn, feed_id=feed_id, url=url,
                                    payload=resp.content, ext="html",
                                    parse_status="captured")
        if new and prev is not None:
            common.notify(f"MEMBERSHIP PAGE CHANGED: {label}",
                          f"{url} content differs from previous verification — "
                          f"review for membership additions/withdrawals.",
                          priority="high", tags="bell")
        else:
            print(f"[{feed_id}] verified, {'first capture' if prev is None else 'unchanged'}")
    conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
