"""Feed health monitor, rebuilt on the feed_runs ledger (STATUS.md, "Task
B1" health-monitor findings; collector/health_rules.py's module docstring
has the full list of what was wrong and why).

All decision logic lives in collector.health_rules (pure, no I/O, no config
import — that's what makes it testable without Postgres). This module is
the thin DB-facing orchestration: read the ledger and raw_captures,
evaluate, reconcile against currently-open managed gaps, write the diff,
and send at most one notification per run.

Run:  python -m collector.feed_health              # normal run
      python -m collector.feed_health --dry-run     # evaluate + print only
      python -m collector.feed_health --digest      # summarize open gaps
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from . import common, config, health_rules

# recent_runs looks back this far for the low_volume weekly computation:
# 4 prior full weeks + the current partial week + the first-48h exclusion
# margin needs 35 days; a few extra days of slack costs nothing (feed_id,
# finished_at) is indexed either way) and keeps the query one fixed window.
HISTORY_WINDOW_DAYS = 40


class DbHealthStore:
    """Thin DB access for feed_health. Every method rolls back before
    re-raising on any exception — same reasoning as poll_rss.DbStore (see
    its comment and test_transaction_recovery.py): psycopg leaves a
    connection in an aborted-transaction state after any failed statement,
    and run() below needs to keep going — other feeds, other checks — after
    one read fails, not inherit a poisoned connection from it."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def active_feeds(self) -> list[dict]:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT feed_id, feed_class, created_at FROM feeds WHERE active")
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            self._conn.rollback()
            raise

    def recent_runs(self, feed_id: str, since: datetime) -> list[dict]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT outcome, finished_at, new_captures
                    FROM feed_runs
                    WHERE feed_id = %s AND finished_at >= %s
                    ORDER BY finished_at DESC
                    """,
                    (feed_id, since),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            self._conn.rollback()
            raise

    def latest_capture_at(self, feed_id: str) -> datetime | None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT max(retrieved_at) FROM raw_captures WHERE feed_id = %s",
                    (feed_id,),
                )
                return cur.fetchone()[0]
        except Exception:
            self._conn.rollback()
            raise

    def open_managed_gaps(self) -> list[dict]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT gap_id, origin, description, opened_at "
                    "FROM research_gaps WHERE status = 'open'")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            return [r for r in rows if health_rules.is_managed_origin(r["origin"] or "")]
        except Exception:
            self._conn.rollback()
            raise

    def open_gap(self, *, origin: str, description: str, country_iso3: str | None) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO research_gaps (description, origin, country_iso3) "
                    "VALUES (%s, %s, %s)",
                    (description, origin, country_iso3),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close_gap(self, gap_id: int) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE research_gaps SET status = 'resolved', closed_at = now() "
                    "WHERE gap_id = %s",
                    (gap_id,),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def rollback(self) -> None:
        self._conn.rollback()


class RunResult(NamedTuple):
    opened: list          # list[health_rules.Problem]
    closed: list           # list[(gap: dict, reason: str)]
    notified: bool


def _country_for(feed_id: str, feed_cfg_by_id: dict, verify_cfg_by_id: dict) -> str | None:
    entry = feed_cfg_by_id.get(feed_id) or verify_cfg_by_id.get(feed_id) or {}
    return entry.get("country_iso3")


def _close_reason(gap: dict, active_ids: set[str]) -> str:
    feed_id = health_rules.feed_id_from_origin(gap["origin"])
    if feed_id is not None and feed_id not in active_ids:
        return "feed retired"
    return "condition cleared"


def _build_notification(to_open: list, closures: list,
                        renotify: bool, collector_down_gap: dict | None
                        ) -> tuple[str, str, str, str] | None:
    """(title, message, priority, tags) for this run's single
    notification, or None if nothing changed and no renotify is due.
    Priority is "high"/warning only when something opened or a
    collector_down re-notify is due — an active problem, urgent by
    construction. A closures-only message (things got BETTER) is
    "default" priority with a neutral tag, not a warning."""
    if not to_open and not closures and not renotify:
        return None

    title_bits = []
    if to_open:
        title_bits.append(f"{len(to_open)} opened")
    if closures:
        title_bits.append(f"{len(closures)} closed")
    if renotify and not to_open and not closures:
        title_bits.append("collector_down still open")
    title = "Feed health: " + ", ".join(title_bits)

    lines = [f"OPENED {p.origin}: {p.description}" for p in to_open]
    lines += [f"CLOSED {gap['origin']} ({reason})" for gap, reason in closures]
    if renotify and not to_open and not closures and collector_down_gap is not None:
        lines.append(f"collector_down has been open since "
                     f"{collector_down_gap['opened_at'].isoformat()}")

    urgent = bool(to_open) or renotify
    priority, tags = ("high", "warning") if urgent else ("default", "white_check_mark")
    return title, "\n".join(lines), priority, tags


def run(cfg: dict, store, *, now: datetime | None = None, dry_run: bool = False) -> RunResult:
    """One health-check pass. Reads active feeds and their ledger/capture
    state, evaluates health_rules.evaluate(), reconciles against currently
    open managed gaps, writes the diff (unless dry_run), and sends at most
    one notification (unless dry_run, which never writes or notifies —
    only evaluates and prints)."""
    now = now or datetime.now(timezone.utc)

    active = store.active_feeds()
    # "feed retired" must mean genuinely inactive — every active feed_id
    # counts, not just rss/watchlist, so an active structured_news/
    # structured_data feed's gap (if it somehow has one) is never closed as
    # retired just because this rebuild doesn't evaluate that feed_class.
    active_ids = {f["feed_id"] for f in active}
    rss_ids = [f["feed_id"] for f in active if f["feed_class"] == "rss"]
    verify_ids = [f["feed_id"] for f in active if f["feed_class"] == "watchlist"]

    feed_cfg_by_id = {f["feed_id"]: f for f in cfg.get("feeds", [])}
    verify_cfg_by_id = {v["feed_id"]: v for v in cfg.get("verify_items", [])}

    since = now - timedelta(days=HISTORY_WINDOW_DAYS)
    histories: dict[str, list] = {}
    ledger_error: Exception | None = None
    for feed_id in rss_ids:
        try:
            histories[feed_id] = store.recent_runs(feed_id, since)
        except Exception as exc:               # noqa: BLE001 — see class docstring
            ledger_error = exc
            break
    if ledger_error is not None:
        print(f"feed_runs unavailable: {ledger_error}")
        histories_arg = None
    else:
        histories_arg = histories

    # A feed_id whose raw_captures read fails still gets a None entry (some
    # value has to go in the dict) but is ALSO recorded as unreadable — the
    # None must never be mistaken for a confirmed "no captures ever" (that
    # would open a false dead_feed gap on a feed we simply couldn't check).
    latest_captures: dict[str, datetime | None] = {}
    unreadable_captures: set[str] = set()
    for feed_id in rss_ids + verify_ids:
        try:
            latest_captures[feed_id] = store.latest_capture_at(feed_id)
        except Exception as exc:                # noqa: BLE001 — one feed's read failing
            print(f"[{feed_id}] raw_captures unavailable: {exc}")
            latest_captures[feed_id] = None
            unreadable_captures.add(feed_id)

    created_at_by_id = {f["feed_id"]: f.get("created_at") for f in active}
    rss_feeds = [{"feed_id": fid, "created_at": created_at_by_id.get(fid)} for fid in rss_ids]
    verify_items = [{"feed_id": fid,
                     "max_gap_days": verify_cfg_by_id.get(fid, {}).get("max_gap_days", 8)}
                    for fid in verify_ids]

    evaluation = health_rules.evaluate(
        rss_feeds=rss_feeds, histories=histories_arg, verify_items=verify_items,
        latest_captures=latest_captures, now=now,
        unreadable_captures=frozenset(unreadable_captures),
        poll_stale_h=config.POLL_STALE_H, breaker_threshold=config.BREAKER_THRESHOLD)

    open_gaps = store.open_managed_gaps()
    to_open, to_close = health_rules.reconcile(
        open_gaps, evaluation.problems, evaluation.undetermined)
    closures = [(gap, _close_reason(gap, active_ids)) for gap in to_close]

    renotify = False
    collector_down_gap = None
    if "collector_down" not in evaluation.undetermined:
        to_close_origins = {gap["origin"] for gap in to_close}
        for gap in open_gaps:
            if gap["origin"] == "collector_down" and gap["origin"] not in to_close_origins:
                collector_down_gap = gap
                renotify = health_rules.should_renotify_collector_down(
                    gap["opened_at"], now, config.COLLECTOR_DOWN_RENOTIFY_H)
                break

    if dry_run:
        print(f"[dry-run] would open {len(to_open)}:")
        for p in to_open:
            print(f"  {p.origin}: {p.description}")
        print(f"[dry-run] would close {len(to_close)}: "
             + ", ".join(f"{g['origin']} ({r})" for g, r in closures))
        if evaluation.undetermined:
            print(f"[dry-run] undetermined (no evidence either way this run): "
                 + ", ".join(sorted(evaluation.undetermined)))
        notification = _build_notification(to_open, closures, renotify, collector_down_gap)
        if notification:
            title, message, priority, tags = notification
            print(f"[dry-run] would notify ({priority}): {title}\n{message}")
        else:
            print("[dry-run] would notify: nothing")
        return RunResult(to_open, closures, False)

    for problem in to_open:
        country = None
        if problem.feed_id is not None:
            country = _country_for(problem.feed_id, feed_cfg_by_id, verify_cfg_by_id)
        store.open_gap(origin=problem.origin, description=problem.description,
                       country_iso3=country)
    for gap, _reason in closures:
        store.close_gap(gap["gap_id"])

    notification = _build_notification(to_open, closures, renotify, collector_down_gap)
    notified = False
    if notification:
        title, message, priority, tags = notification
        common.notify(title, message, priority=priority, tags=tags)
        notified = True

    return RunResult(to_open, closures, notified)


def digest(store) -> list[dict]:
    """--digest: print and notify every currently open managed gap at
    default priority. No evaluation, no writes."""
    gaps = store.open_managed_gaps()
    lines = [f"{g['origin']}: {g['description']}" for g in gaps]
    for line in lines:
        print(line)
    if not lines:
        print("no open managed gaps")
    common.notify(f"Feed health digest: {len(gaps)} open gap(s)",
                 "\n".join(lines) if lines else "No open managed gaps.",
                 priority="default", tags="clipboard")
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="evaluate and print what would open/close/notify; no writes, no notify")
    ap.add_argument("--digest", action="store_true",
                    help="print and notify every open managed gap; nothing else")
    args = ap.parse_args()

    cfg = config.load_feeds_config()
    conn = common.connect()
    store = DbHealthStore(conn)

    if args.digest:
        digest(store)
        conn.close()
        return 0

    result = run(cfg, store, dry_run=args.dry_run)
    print(f"done: {len(result.opened)} opened, {len(result.closed)} closed, "
         f"notified={result.notified}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
