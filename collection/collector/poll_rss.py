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

Every poll ATTEMPT — not just successful captures — is recorded in the
feed_runs ledger (collection/sql/004_feed_runs.sql), one row per feed per
run, because "this feed had nothing new" and "the collector never reached
this feed" are otherwise indistinguishable from raw_captures alone (that gap
produced no signal at all during the 2026-09-19 18h outage). collector.
breaker reads that ledger to quarantine a feed that fails/times out
SD_BREAKER_THRESHOLD times in a row, skipping it (no connection attempt)
until one probe succeeds, so one permanently broken feed can't burn the
whole run's time budget forever. The ledger is best-effort: if the table is
missing or unwritable, every feed is just treated as healthy (see
_ledger_calls below) — a down ledger must never stop or fail a poll.

Run:  python -m collector.poll_rss
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import feedparser

from . import breaker, common, config, fetcher

# How far back poll_feed's decision-making looks. Generous relative to
# BREAKER_THRESHOLD (default 5): while quarantined, a probe happens at most
# once per BREAKER_PROBE_H, so consecutive skipped_quarantined rows between
# two real attempts are bounded by (probe interval / poll interval) — at the
# default 2-hourly timer and 24h probe interval that's ~12 rows. 100 leaves
# wide margin without a query cost worth worrying about (idx_feed_runs_feed_
# finished makes this an index scan either way).
HISTORY_LOOKBACK = 100


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

    def record_run(self, *, feed_id: str, started_at: datetime, finished_at: datetime,
                   outcome: str, entries_seen: int, new_captures: int,
                   error: str | None) -> None:
        # psycopg leaves a connection in an aborted-transaction state after
        # ANY failed statement (e.g. feed_runs not existing yet, before
        # 004_feed_runs.sql is applied) — every later statement on the same
        # connection then fails too, until rollback() runs. _make_ledger_
        # calls only swallows the exception; it can't fix the connection,
        # so this must roll back itself before letting the caller's feed
        # (or, worse, every feed after it) inherit a poisoned connection.
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feed_runs
                      (feed_id, started_at, finished_at, outcome, entries_seen,
                       new_captures, error)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (feed_id, started_at, finished_at, outcome, entries_seen,
                     new_captures, error),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def recent_runs(self, feed_id: str, limit: int) -> list[dict]:
        # See record_run's comment: same reasoning, same fix.
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT outcome, started_at, finished_at, entries_seen,
                           new_captures, error
                    FROM feed_runs
                    WHERE feed_id = %s
                    ORDER BY finished_at DESC
                    LIMIT %s
                    """,
                    (feed_id, limit),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            self._conn.rollback()
            raise

    def rollback(self) -> None:
        self._conn.rollback()


def _make_ledger_calls(store):
    """Wraps store.record_run/recent_runs so a broken ledger (missing
    table, permission denied, whatever) is caught, logged ONCE for this
    run(), and otherwise invisible: recent_runs falls back to "no history"
    (every feed treated as healthy — see breaker.decide on empty history),
    record_run just drops the row. A down ledger must never stop or fail a
    poll (spec point 2)."""
    warned = [False]

    def _warn_once(exc: Exception) -> None:
        if not warned[0]:
            print(f"feed_runs unavailable: {exc}")
            warned[0] = True

    def recent_runs(feed_id: str, limit: int) -> list[dict]:
        try:
            return store.recent_runs(feed_id, limit)
        except Exception as exc:            # noqa: BLE001 — ledger must never fail a poll
            _warn_once(exc)
            return []

    def record_run(**kwargs) -> None:
        try:
            store.record_run(**kwargs)
        except Exception as exc:            # noqa: BLE001 — ledger must never fail a poll
            _warn_once(exc)

    return recent_runs, record_run


def poll_feed(feed: dict, store) -> tuple[int, int, str | None, str]:
    """Returns (entries_seen, new_captures, error, outcome). `outcome` is
    exactly one of breaker.OUTCOMES minus 'skipped_quarantined' (that one is
    assigned by run(), never by poll_feed, since poll_feed is never called
    for a skipped feed); `error` is set only for 'failed'/'timeout'."""
    feed_id, url = feed["feed_id"], feed["url"]
    dedupe_on = feed.get("dedupe_on", "url")
    skipped = 0

    try:
        raw = fetcher.get(url, headers={"User-Agent": config.USER_AGENT}).content
    except fetcher.FetchTimeout as exc:
        return 0, 0, f"feed fetch failed: {exc}", "timeout"
    except fetcher.FetchError as exc:
        return 0, 0, f"feed fetch failed: {exc}", "failed"

    parsed = feedparser.parse(raw)
    if parsed.get("bozo") and not parsed.entries:
        return 0, 0, f"unparseable feed: {parsed.get('bozo_exception')}", "failed"

    new = 0
    start = time.monotonic()
    entries = parsed.entries
    budget_hit = False
    for i, entry in enumerate(entries):
        if time.monotonic() - start > config.FEED_BUDGET:
            print(f"[{feed_id}] outcome=budget_exhausted urls_remaining={len(entries) - i}")
            budget_hit = True
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
    return len(parsed.entries), new, None, ("budget_exhausted" if budget_hit else "ok")


def run(cfg: dict, store, *, now: datetime | None = None) -> tuple[int, list[tuple[str, str]]]:
    """Poll every rss-class feed in cfg, in breaker-aware order. Returns
    (total_new, failures). A feed that raises is recorded as a failure by
    feed_id and never stops the remaining feeds — this is the loop the
    tests drive directly with a FakeStore, no database required."""
    now = now or datetime.now(timezone.utc)
    recent_runs, record_run = _make_ledger_calls(store)

    feeds = [f for f in cfg.get("feeds", []) if f["feed_class"] == "rss"]
    histories = {f["feed_id"]: recent_runs(f["feed_id"], HISTORY_LOOKBACK) for f in feeds}
    latest_outcome = {
        feed_id: (breaker.last_real_run(history) or {}).get("outcome")
        for feed_id, history in histories.items()
    }
    ordered = breaker.order_feeds(feeds, latest_outcome)

    failures: list[tuple[str, str]] = []
    total_new = 0
    probe_after = timedelta(hours=config.BREAKER_PROBE_H)

    for feed in ordered:
        feed_id = feed["feed_id"]
        history = histories[feed_id]
        decision = breaker.decide(history, now, threshold=config.BREAKER_THRESHOLD,
                                  probe_after=probe_after)

        if decision.state_change == "quarantined":
            common.notify(
                f"Collector: {feed_id} quarantined",
                f"{feed_id} has failed/timed out {config.BREAKER_THRESHOLD} consecutive "
                f"times; skipping until a probe succeeds (at most once per "
                f"{config.BREAKER_PROBE_H:g}h).",
                priority="high", tags="no_entry")
        elif decision.state_change == "recovered":
            common.notify(
                f"Collector: {feed_id} recovered",
                f"{feed_id} succeeded on a quarantine probe and is polling normally again.",
                priority="default", tags="white_check_mark")

        started_at = datetime.now(timezone.utc)
        if decision.action == "skip":
            finished_at = started_at
            next_probe = breaker.next_probe_at(history, probe_after)
            next_probe_str = next_probe.isoformat() if next_probe else "unknown"
            print(f"[{feed_id}] outcome=skipped_quarantined next_probe={next_probe_str}")
            record_run(feed_id=feed_id, started_at=started_at, finished_at=finished_at,
                      outcome="skipped_quarantined", entries_seen=0, new_captures=0,
                      error=None)
            continue          # skipped-quarantined is not a failure for the notify below

        try:
            seen, new, err, outcome = poll_feed(feed, store)
            total_new += new
            print(f"[{feed_id}] entries={seen} new={new}"
                  + (f" ERROR={err}" if err else ""))
            finished_at = datetime.now(timezone.utc)
            record_run(feed_id=feed_id, started_at=started_at, finished_at=finished_at,
                      outcome=outcome, entries_seen=seen, new_captures=new, error=err)
            if err:
                failures.append((feed_id, err))
        except Exception as exc:            # one bad feed never stops the run
            traceback.print_exc()
            # Whatever just raised may have left the connection in an
            # aborted-transaction state (a real DB error inside poll_feed,
            # not just a ledger one) — roll back BEFORE record_run tries to
            # write anything, or that write fails too and the poison
            # carries over to the next feed in this loop.
            rollback = getattr(store, "rollback", None)
            if rollback is not None:
                rollback()
            finished_at = datetime.now(timezone.utc)
            record_run(feed_id=feed_id, started_at=started_at, finished_at=finished_at,
                      outcome="failed", entries_seen=0, new_captures=0, error=str(exc))
            failures.append((feed_id, str(exc)))
    return total_new, failures


def main() -> int:
    common.warn_if_ntfy_unconfigured()
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
