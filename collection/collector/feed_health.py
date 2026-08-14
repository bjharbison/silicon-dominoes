"""Feed health monitor (ARCHITECTURE.md §4).

A dead feed silently starves countries of evidence and biases scores toward
zero movement, so this runs daily and checks, per active feed:

  RSS / structured feeds:
    - rolling baseline = captures/day over the last 30 days (needs >= 7 days
      of history before it can judge);
    - alert if the last 48h produced under 25% of the expected volume;
    - alert if there has been NO capture for longer than 3x the feed's own
      historical maximum gap (covers "success but zero items").
  Verify items (watchlist class):
    - alert if no successful verification within max_gap_days (default 8) —
      the staleness rule for WAICO / Pax Silica.

Every dead-feed alert also opens a research_gap row (origin 'dead_feed:<id>')
unless one is already open, so the analytic layer can see the coverage hole.

Run:  python -m collector.feed_health
"""
from __future__ import annotations

import sys

from . import common, config

RECENT_WINDOW_H = 48
BASELINE_DAYS = 30
MIN_HISTORY_DAYS = 7
LOW_FRACTION = 0.25


def open_gap_if_needed(conn, feed_id: str, description: str) -> bool:
    origin = f"dead_feed:{feed_id}"
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM research_gaps WHERE origin = %s AND status = 'open'",
                    (origin,))
        if cur.fetchone():
            return False
        cur.execute(
            "INSERT INTO research_gaps (description, origin) VALUES (%s, %s)",
            (description, origin))
    conn.commit()
    return True


def check_rss_feed(conn, feed_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE retrieved_at > now() - interval '%s hours'),
              count(*) FILTER (WHERE retrieved_at > now() - interval '%s days'),
              min(retrieved_at),
              max(retrieved_at),
              coalesce(extract(epoch FROM max(gap)) / 3600.0, 0)
            FROM (
              SELECT retrieved_at,
                     retrieved_at - lag(retrieved_at) OVER (ORDER BY retrieved_at) AS gap
              FROM raw_captures WHERE feed_id = %%s
            ) t
            """ % (RECENT_WINDOW_H, BASELINE_DAYS),
            (feed_id,),
        )
        recent, month, first, last, max_gap_h = cur.fetchone()

    if first is None:
        return f"no captures ever recorded — feed may be misconfigured or dead since setup"

    with conn.cursor() as cur:
        cur.execute("SELECT extract(day FROM now() - %s)", (first,))
        history_days = float(cur.fetchone()[0] or 0)
    if history_days < MIN_HISTORY_DAYS:
        return None  # too young to judge

    baseline_per_day = month / min(history_days, BASELINE_DAYS)
    expected_recent = baseline_per_day * (RECENT_WINDOW_H / 24.0)
    if expected_recent >= 1 and recent < LOW_FRACTION * expected_recent:
        return (f"capture rate collapsed: {recent} in last {RECENT_WINDOW_H}h vs "
                f"~{expected_recent:.1f} expected from its 30-day baseline")

    with conn.cursor() as cur:
        cur.execute("SELECT extract(epoch FROM now() - %s) / 3600.0", (last,))
        silent_h = float(cur.fetchone()[0])
    if max_gap_h > 0 and silent_h > max(3 * max_gap_h, 72):
        return (f"silent for {silent_h:.0f}h — over 3x its historical maximum "
                f"gap of {max_gap_h:.0f}h")
    return None


def check_verify_item(conn, feed_id: str, max_gap_days: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT max(retrieved_at) FROM raw_captures WHERE feed_id = %s",
                    (feed_id,))
        last = cur.fetchone()[0]
        if last is None:
            return "never successfully verified"
        cur.execute("SELECT now() - %s > make_interval(days => %s)", (last, max_gap_days))
        stale = cur.fetchone()[0]
    return f"no successful verification in over {max_gap_days} days" if stale else None


def main() -> int:
    cfg = config.load_feeds_config()
    conn = common.connect()
    problems = []

    verify_ids = {i["feed_id"]: int(i.get("max_gap_days", 8))
                  for i in cfg.get("verify_items", [])}

    with conn.cursor() as cur:
        cur.execute("SELECT feed_id, feed_class FROM feeds WHERE active")
        feeds = cur.fetchall()

    for feed_id, feed_class in feeds:
        if feed_id in verify_ids:
            issue = check_verify_item(conn, feed_id, verify_ids[feed_id])
        elif feed_class in ("rss", "structured_news", "structured_data"):
            issue = check_rss_feed(conn, feed_id)
        else:
            issue = None
        if issue:
            problems.append((feed_id, issue))
            opened = open_gap_if_needed(conn, feed_id, f"Feed health: {feed_id} — {issue}")
            print(f"[{feed_id}] PROBLEM: {issue}" + (" (research_gap opened)" if opened else ""))
        else:
            print(f"[{feed_id}] ok")

    if problems:
        common.notify(
            f"Feed health: {len(problems)} problem(s)",
            "\n".join(f"{fid}: {issue}" for fid, issue in problems),
            priority="high", tags="warning")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
