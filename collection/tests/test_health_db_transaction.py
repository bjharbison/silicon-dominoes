"""Test k from the harden-health spec: DbHealthStore rolls back before
re-raising on any exception, so a failing feed_runs read (e.g. because a
migration hasn't been applied yet — the exact B1 scenario, see STATUS.md
and test_transaction_recovery.py) doesn't poison the connection for every
later read in the same run. Same fake-psycopg-connection technique as
test_transaction_recovery.py, applied to the new store.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import feed_health  # noqa: E402


class AbortedTransaction(Exception):
    """Stands in for psycopg.errors.InFailedSqlTransaction."""


class UndefinedTable(Exception):
    """Stands in for psycopg.errors.UndefinedTable — feed_runs before
    004_feed_runs.sql has been applied."""


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn
        self._result: list[tuple] = []
        self.description: list[tuple] | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, sql: str, params=()) -> None:
        if self._conn.aborted:
            raise AbortedTransaction(
                "current transaction is aborted, commands ignored until "
                "end of transaction block")
        if "feed_runs" in sql and self._conn.fail_feed_runs:
            self._conn.aborted = True
            raise UndefinedTable('relation "feed_runs" does not exist')

        self._result, self.description = [], None
        if "FROM feeds WHERE active" in sql:
            self._result = self._conn.feeds_rows
            self.description = [("feed_id",), ("feed_class",), ("created_at",)]
        elif "FROM feed_runs" in sql:
            feed_id = params[0]
            self._result = self._conn.feed_runs_rows.get(feed_id, [])
            self.description = [("outcome",), ("finished_at",), ("new_captures",)]
        elif "max(retrieved_at)" in sql:
            feed_id = params[0]
            self._result = [(self._conn.latest_captures.get(feed_id),)]
        elif "FROM research_gaps WHERE status" in sql:
            self._result = self._conn.gap_rows
            self.description = [("gap_id",), ("origin",), ("description",), ("opened_at",)]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConn:
    def __init__(self, *, fail_feed_runs: bool) -> None:
        self.aborted = False
        self.fail_feed_runs = fail_feed_runs
        self.feeds_rows = [("rss-a", "rss", datetime.now(timezone.utc))]
        self.feed_runs_rows: dict[str, list[tuple]] = {
            "rss-a": [("ok", datetime.now(timezone.utc), 3)],
        }
        self.latest_captures: dict[str, datetime] = {"rss-a": datetime.now(timezone.utc)}
        self.gap_rows: list[tuple] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1
        self.aborted = False


class DbHealthStoreTransactionTests(unittest.TestCase):
    def test_failing_feed_runs_read_rolls_back_and_later_reads_succeed(self) -> None:
        conn = FakeConn(fail_feed_runs=True)
        store = feed_health.DbHealthStore(conn)

        with self.assertRaises(UndefinedTable):
            store.recent_runs("rss-a", datetime.now(timezone.utc))
        self.assertGreaterEqual(conn.rollback_calls, 1,
                                "DbHealthStore.recent_runs must roll back before re-raising")

        # A later, unrelated read on the SAME connection must succeed —
        # this is exactly the bug: without the rollback, this would raise
        # AbortedTransaction instead of returning normally.
        active = store.active_feeds()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["feed_id"], "rss-a")
        self.assertEqual(active[0]["feed_class"], "rss")

        latest = store.latest_capture_at("rss-a")
        self.assertIsNotNone(latest)

    def test_healthy_connection_never_rolls_back(self) -> None:
        conn = FakeConn(fail_feed_runs=False)
        store = feed_health.DbHealthStore(conn)

        store.active_feeds()
        store.recent_runs("rss-a", datetime.now(timezone.utc))
        store.latest_capture_at("rss-a")

        self.assertEqual(conn.rollback_calls, 0)


if __name__ == "__main__":
    unittest.main()
