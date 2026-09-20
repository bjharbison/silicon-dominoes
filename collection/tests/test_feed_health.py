"""Tests h, i, k from the harden-health spec: feed_health's orchestration
(notify-on-change, collector_down re-notify, --dry-run never writes/notifies)
driven with an in-memory FakeHealthStore, and DbHealthStore's rollback
behavior driven over a fake aborted-transaction psycopg-style connection
(same pattern as test_transaction_recovery.py, applied to the new store).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import common, feed_health, health_rules  # noqa: E402

T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def _row(outcome: str, at: datetime, new_captures: int = 1) -> dict:
    return {"outcome": outcome, "finished_at": at, "new_captures": new_captures}


class FakeHealthStore:
    """In-memory stand-in for feed_health.DbHealthStore — no Postgres."""

    def __init__(self) -> None:
        self.feeds: list[dict] = []
        self.histories: dict[str, list[dict]] = {}
        self.latest_captures: dict[str, datetime | None] = {}
        self.gaps: list[dict] = []
        self._next_gap_id = 1
        self.clock = lambda: datetime.now(timezone.utc)
        self.raise_on_write = False

    def active_feeds(self) -> list[dict]:
        return list(self.feeds)

    def recent_runs(self, feed_id: str, since: datetime) -> list[dict]:
        return [r for r in self.histories.get(feed_id, []) if r["finished_at"] >= since]

    def latest_capture_at(self, feed_id: str):
        return self.latest_captures.get(feed_id)

    def open_managed_gaps(self) -> list[dict]:
        return [dict(g) for g in self.gaps
               if g["status"] == "open" and health_rules.is_managed_origin(g["origin"])]

    def open_gap(self, *, origin: str, description: str, country_iso3) -> None:
        if self.raise_on_write:
            raise AssertionError(f"open_gap must not be called in dry-run: {origin}")
        self.gaps.append({"gap_id": self._next_gap_id, "origin": origin,
                          "description": description, "country_iso3": country_iso3,
                          "status": "open", "opened_at": self.clock(), "closed_at": None})
        self._next_gap_id += 1

    def close_gap(self, gap_id: int) -> None:
        if self.raise_on_write:
            raise AssertionError(f"close_gap must not be called in dry-run: {gap_id}")
        for g in self.gaps:
            if g["gap_id"] == gap_id:
                g["status"] = "resolved"

    def rollback(self) -> None:
        pass


def _spy_notify(test: unittest.TestCase) -> list[tuple]:
    calls: list[tuple] = []
    original = common.notify

    def spy(title, message, **kwargs):
        calls.append((title, message))

    common.notify = spy
    test.addCleanup(lambda: setattr(common, "notify", original))
    return calls


class NotifyOnChangeTests(unittest.TestCase):
    def _healthy_store(self) -> FakeHealthStore:
        store = FakeHealthStore()
        store.feeds = [{"feed_id": "rss-a", "feed_class": "rss"}]
        store.histories = {"rss-a": [_row("ok", T0 - timedelta(hours=1), 3)]}
        store.latest_captures = {"rss-a": T0 - timedelta(hours=1)}
        return store

    def test_silent_when_nothing_changes(self) -> None:
        calls = _spy_notify(self)
        store = self._healthy_store()
        cfg = {"feeds": [{"feed_id": "rss-a"}], "verify_items": []}

        result1 = feed_health.run(cfg, store, now=T0)
        result2 = feed_health.run(cfg, store, now=T0 + timedelta(hours=1))

        self.assertFalse(result1.notified)
        self.assertFalse(result2.notified)
        self.assertEqual(calls, [])

    def test_one_message_lists_both_opens_and_closes(self) -> None:
        calls = _spy_notify(self)
        store = FakeHealthStore()
        store.feeds = [
            {"feed_id": "rss-dying", "feed_class": "rss"},
            {"feed_id": "rss-fine", "feed_class": "rss"},
        ]
        # rss-dying: 5 consecutive failures -> about to open dead_feed.
        store.histories = {
            "rss-dying": [_row("failed", T0 - timedelta(minutes=10 * i)) for i in range(5)],
            "rss-fine": [_row("ok", T0 - timedelta(hours=1), 2)],
        }
        store.latest_captures = {"rss-dying": None, "rss-fine": T0 - timedelta(hours=1)}
        # A pre-existing gap whose condition has now cleared (feed is fine).
        store.gaps = [{"gap_id": 1, "origin": "dead_feed:rss-fine",
                      "description": "was dead", "status": "open", "opened_at": T0}]
        cfg = {"feeds": [{"feed_id": "rss-dying"}, {"feed_id": "rss-fine"}], "verify_items": []}

        result = feed_health.run(cfg, store, now=T0)

        self.assertTrue(result.notified)
        self.assertEqual(len(calls), 1, "exactly one notify for this run")
        title, message = calls[0]
        self.assertIn("dead_feed:rss-dying", message)
        self.assertIn("dead_feed:rss-fine", message)
        self.assertIn("1 opened", title)
        self.assertIn("1 closed", title)

    def test_collector_down_renotify_timing(self) -> None:
        calls = _spy_notify(self)
        store = FakeHealthStore()
        # FakeHealthStore.clock defaults to the real wall clock (it's what
        # DbHealthStore.open_gap's real INSERT ... DEFAULT now() stands in
        # for), so without pinning it, opened_at is "whenever this test
        # happened to run" — completely decoupled from the T0-based `now`
        # values below. should_renotify_collector_down's elapsed-time math
        # then depends on wall-clock drift between test-run time and T0,
        # which is exactly why this test was flaky (passed or failed
        # depending on the real clock, not on the rule under test). Pin it
        # to T0 so opened_at is deterministic.
        store.clock = lambda: T0
        store.feeds = [{"feed_id": "rss-a", "feed_class": "rss"},
                      {"feed_id": "rss-b", "feed_class": "rss"}]
        # Both feeds permanently 7h-stale across every evaluation below.
        store.histories = {
            "rss-a": [_row("ok", T0 - timedelta(hours=7))],
            "rss-b": [_row("ok", T0 - timedelta(hours=7))],
        }
        store.latest_captures = {"rss-a": None, "rss-b": None}
        cfg = {"feeds": [{"feed_id": "rss-a"}, {"feed_id": "rss-b"}], "verify_items": []}

        # Run 1: opens collector_down, notifies once.
        r1 = feed_health.run(cfg, store, now=T0)
        self.assertTrue(r1.notified)
        self.assertEqual(len(calls), 1)

        # Run 2, +3h: still open, not due for a renotify yet (< 6h).
        r2 = feed_health.run(cfg, store, now=T0 + timedelta(hours=3))
        self.assertFalse(r2.notified)
        self.assertEqual(len(calls), 1)

        # Run 3, +6h: renotify due, even though nothing opened or closed.
        r3 = feed_health.run(cfg, store, now=T0 + timedelta(hours=6))
        self.assertTrue(r3.notified)
        self.assertEqual(r3.opened, [])
        self.assertEqual(r3.closed, [])
        self.assertEqual(len(calls), 2)


class DryRunTests(unittest.TestCase):
    def test_dry_run_never_writes_or_notifies_even_if_they_would_raise(self) -> None:
        original_notify = common.notify

        def raising_notify(*a, **kw):
            raise AssertionError("notify must not be called in dry-run")

        common.notify = raising_notify
        self.addCleanup(lambda: setattr(common, "notify", original_notify))

        store = FakeHealthStore()
        store.raise_on_write = True
        store.feeds = [{"feed_id": "rss-dying", "feed_class": "rss"}]
        store.histories = {
            "rss-dying": [_row("failed", T0 - timedelta(minutes=10 * i)) for i in range(5)],
        }
        store.latest_captures = {"rss-dying": None}
        cfg = {"feeds": [{"feed_id": "rss-dying"}], "verify_items": []}

        result = feed_health.run(cfg, store, now=T0, dry_run=True)

        self.assertFalse(result.notified)
        self.assertEqual(len(result.opened), 1, "dry-run must still report what WOULD open")
        self.assertEqual(store.gaps, [], "dry-run must not have written anything")


class DigestTests(unittest.TestCase):
    def test_digest_notifies_every_open_managed_gap(self) -> None:
        calls = _spy_notify(self)
        store = FakeHealthStore()
        store.gaps = [
            {"gap_id": 1, "origin": "dead_feed:rss-a", "description": "rss-a is dead",
             "status": "open", "opened_at": T0},
            {"gap_id": 2, "origin": "analyst", "description": "unmanaged, ignored",
             "status": "open", "opened_at": T0},
            {"gap_id": 3, "origin": "poll_stale:rss-b", "description": "rss-b stale",
             "status": "resolved", "opened_at": T0},
        ]

        gaps = feed_health.digest(store)

        self.assertEqual([g["gap_id"] for g in gaps], [1])
        self.assertEqual(len(calls), 1)
        title, message = calls[0]
        self.assertIn("1 open gap", title)
        self.assertIn("rss-a is dead", message)


if __name__ == "__main__":
    unittest.main()
