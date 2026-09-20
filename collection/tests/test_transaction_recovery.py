"""Regression test for the transaction-abort bug reported on the
harden-ledger branch: psycopg leaves a connection in an aborted-transaction
state after any failed statement. DbStore.recent_runs/record_run can fail
(e.g. UndefinedTable before 004_feed_runs.sql is applied) and
_make_ledger_calls swallows that — but nothing called rollback(), so every
LATER query on the same connection (already_captured, insert_capture) then
failed with "current transaction is aborted", even for feeds that have
nothing to do with the ledger. A missing ledger table would fail every
feed — the exact opposite of harden-ledger spec point 2 ("a down ledger
must never stop or fail a poll").

FakeStore has no transaction state, so nothing else in this suite can see
this — it only shows up with something that behaves like a real psycopg
connection: one failed statement poisons every subsequent one until
rollback(). This drives the REAL poll_rss.DbStore (not FakeStore) through
run() against such a fake connection.
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import config, poll_rss  # noqa: E402
import servers  # noqa: E402


class AbortedTransaction(Exception):
    """Stands in for psycopg.errors.InFailedSqlTransaction."""


class UndefinedTable(Exception):
    """Stands in for psycopg.errors.UndefinedTable — e.g. feed_runs before
    004_feed_runs.sql has been applied."""


class FakeCursor:
    """Just enough of a psycopg cursor to drive DbStore's real SQL text
    through common.insert_capture / common.url_already_captured, while
    reproducing the one behavior this test is about: once the connection is
    aborted, EVERY execute() raises, regardless of what it is, until
    rollback() runs."""

    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn
        self._result: list[tuple] = []
        self.rowcount = 0
        self.description: list | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, sql: str, params=()) -> None:
        self._conn.executed.append(sql)
        if self._conn.aborted:
            raise AbortedTransaction(
                "current transaction is aborted, commands ignored until "
                "end of transaction block")
        if "feed_runs" in sql:
            self._conn.aborted = True
            raise UndefinedTable('relation "feed_runs" does not exist')

        self._result, self.rowcount, self.description = [], 0, None
        if "SELECT 1 FROM raw_captures" in sql:
            feed_id, url = params
            if (feed_id, url) in self._conn.captured_urls:
                self._result = [(1,)]
        elif "INSERT INTO raw_captures" in sql:
            feed_id, url, sha256 = params[0], params[1], params[2]
            if (feed_id, sha256) not in self._conn.captured_sha:
                self._conn.captured_sha.add((feed_id, sha256))
                self._conn.captured_urls.add((feed_id, url))
                self.rowcount = 1
        elif "UPDATE feeds" in sql:
            self.rowcount = 1
        # anything else (e.g. a future statement) just succeeds as a no-op

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConn:
    """A fake psycopg connection reproducing exactly the transaction-abort
    behavior the bug report describes, and nothing else."""

    def __init__(self) -> None:
        self.aborted = False
        self.executed: list[str] = []
        self.captured_urls: set[tuple[str, str]] = set()
        self.captured_sha: set[tuple[str, str]] = set()
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1
        self.aborted = False


def _patch_config(test: unittest.TestCase, **overrides) -> None:
    originals = {name: getattr(config, name) for name in overrides}
    for name, value in overrides.items():
        setattr(config, name, value)
    test.addCleanup(lambda: [setattr(config, n, v) for n, v in originals.items()])


class TransactionRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        # This test is the first to exercise the REAL DbStore, which routes
        # record_capture through common.insert_capture -> write_archive,
        # a real filesystem write to config.ARCHIVE_DIR (default
        # /var/lib/silicon-dominoes/archive, not writable/present here) —
        # point it at a throwaway temp dir instead.
        tmp_archive = tempfile.mkdtemp(prefix="sd-archive-")
        self.addCleanup(shutil.rmtree, tmp_archive, True)
        _patch_config(self,
                       HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1, FETCH_DEADLINE=2,
                       FEED_BUDGET=300, WAYBACK_ENABLED=False,
                       ARCHIVE_DIR=Path(tmp_archive))

    def test_ledger_failure_does_not_poison_the_connection_for_later_feeds(self) -> None:
        healthy = servers.healthy_feed_server(n_articles=1)
        self.addCleanup(healthy.stop)

        cfg = {"feeds": [
            {"feed_id": "good", "feed_class": "rss", "url": healthy.url + "feed"},
        ]}
        conn = FakeConn()
        store = poll_rss.DbStore(conn)          # the REAL store, not FakeStore

        buf = io.StringIO()
        with redirect_stdout(buf):
            total_new, failures = poll_rss.run(cfg, store)
        output = buf.getvalue()

        self.assertEqual(failures, [], "the healthy feed must not be recorded as a failure")
        self.assertEqual(total_new, 1, "the healthy feed's capture must still be recorded "
                                       "despite the ledger being unavailable")
        self.assertEqual(output.count("feed_runs unavailable:"), 1,
                         "the ledger-unavailable warning must print exactly once")
        self.assertGreaterEqual(conn.rollback_calls, 1,
                                "DbStore must roll back the connection after a failed "
                                "ledger statement, or it stays aborted for every later query")


if __name__ == "__main__":
    unittest.main()
