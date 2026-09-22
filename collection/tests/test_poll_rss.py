"""poll_rss tests: an in-memory fake in place of the database, no Postgres
involved. Exercises the seam added to poll_rss (poll_feed/run take a
`store`, never a conn), the per-feed wall-clock budget, the feed_runs
ledger, and the breaker's integration into run() (tests b, d, e from the
harden-ledger spec — a/c live in test_breaker.py, pure logic only).
"""
from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import common, config, poll_rss  # noqa: E402
import servers  # noqa: E402


class FakeStore:
    """In-memory stand-in for poll_rss.DbStore — no Postgres, no conn."""

    def __init__(self) -> None:
        self.captured: dict[tuple[str, str], bytes] = {}
        self.runs: dict[str, list[dict]] = {}

    def already_captured(self, feed_id: str, url: str) -> bool:
        return (feed_id, url) in self.captured

    def record_capture(self, feed_id: str, url: str, payload: bytes, ext: str,
                       status: str, snapshot_id, snapshot_url) -> bool:
        key = (feed_id, url)
        if key in self.captured:
            return False
        self.captured[key] = payload
        return True

    def record_run(self, *, feed_id, started_at, finished_at, outcome,
                   entries_seen, new_captures, error) -> None:
        row = {"feed_id": feed_id, "started_at": started_at, "finished_at": finished_at,
               "outcome": outcome, "entries_seen": entries_seen,
               "new_captures": new_captures, "error": error}
        self.runs.setdefault(feed_id, []).insert(0, row)   # most-recent-first

    def recent_runs(self, feed_id: str, limit: int) -> list[dict]:
        return self.runs.get(feed_id, [])[:limit]

    def rollback(self) -> None:
        pass   # no transaction state to unwind — see FakeConn in
              # test_transaction_recovery.py for the store that has one

    def seed_run(self, feed_id: str, outcome: str, **extra) -> None:
        """Test helper: pre-seed history without going through a real run()
        call, e.g. to put a feed into quarantine before the test starts.
        finished_at defaults to now (a real datetime, not None) so
        breaker.next_probe_at() can add a timedelta to it without special
        casing — a seeded row is meant to look exactly like a real one."""
        now = datetime.now(timezone.utc)
        row = {"feed_id": feed_id, "started_at": now, "finished_at": now,
               "outcome": outcome, "entries_seen": 0, "new_captures": 0, "error": None}
        row.update(extra)
        self.runs.setdefault(feed_id, []).insert(0, row)


class BrokenLedgerStore:
    """Wraps a FakeStore but makes the ledger calls raise, to exercise the
    'ledger unavailable' fallback (test e) — record_capture/already_captured
    still work normally, since only the ledger is down, not the archive."""

    def __init__(self, inner: FakeStore) -> None:
        self._inner = inner
        self.captured = inner.captured

    def already_captured(self, feed_id, url):
        return self._inner.already_captured(feed_id, url)

    def record_capture(self, *a, **kw):
        return self._inner.record_capture(*a, **kw)

    def record_run(self, **kw):
        raise RuntimeError("permission denied for table feed_runs")

    def recent_runs(self, feed_id, limit):
        raise RuntimeError("permission denied for table feed_runs")


def _patch_config(test: unittest.TestCase, **overrides) -> None:
    """Config values are read fresh on every call (never cached at import),
    so overriding them for the duration of a test is a plain attribute
    patch — see collector/config.py and collector/fetcher.py."""
    originals = {name: getattr(config, name) for name in overrides}
    for name, value in overrides.items():
        setattr(config, name, value)
    test.addCleanup(lambda: [setattr(config, n, v) for n, v in originals.items()])


class PollRunTests(unittest.TestCase):
    def setUp(self) -> None:
        # WAYBACK_ENABLED off: wayback_submit is a real outbound call to
        # web.archive.org and this suite touches no external network.
        _patch_config(self,
                       HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1,
                       FETCH_DEADLINE=2, FEED_BUDGET=300, WAYBACK_ENABLED=False)

    def test_poll_run_over_blackhole_trickle_healthy(self) -> None:
        black_hole = servers.black_hole_server()
        self.addCleanup(black_hole.stop)
        trickle = servers.trickle_server()
        self.addCleanup(trickle.stop)
        healthy = servers.healthy_feed_server(n_articles=2)
        self.addCleanup(healthy.stop)

        cfg = {"feeds": [
            {"feed_id": "blackhole", "feed_class": "rss", "url": black_hole.url},
            {"feed_id": "trickle", "feed_class": "rss", "url": trickle.url},
            {"feed_id": "healthy", "feed_class": "rss", "url": healthy.url + "feed"},
        ]}
        store = FakeStore()

        start = time.monotonic()
        total_new, failures = poll_rss.run(cfg, store)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 15, "poll run did not complete inside a bounded time")
        self.assertEqual(total_new, 2, "the healthy feed's 2 articles should be captured")
        self.assertEqual(
            sum(1 for (feed_id, _url) in store.captured if feed_id == "healthy"), 2)

        failed_ids = {feed_id for feed_id, _err in failures}
        self.assertEqual(failed_ids, {"blackhole", "trickle"},
                         "both the black-hole and trickle feeds should be recorded "
                         "as failures by feed_id, and only those two")

    def test_per_feed_budget_stops_feed_and_next_feed_still_runs(self) -> None:
        # Read timeout/deadline widened well past the 2s article delay so
        # the slow articles succeed rather than timing out — this test is
        # about the cooperative budget check, not the per-call bounds.
        _patch_config(self, FEED_BUDGET=1, HTTP_READ_TIMEOUT=5, FETCH_DEADLINE=5)
        # 3 articles each taking 2s: the budget (1s) is checked before each
        # fetch, so the first fetch is allowed to start (elapsed=0 < budget)
        # and complete, then the loop stops before the second.
        slow = servers.healthy_feed_server(n_articles=3, article_delay=2)
        self.addCleanup(slow.stop)
        fast = servers.healthy_feed_server(n_articles=2)
        self.addCleanup(fast.stop)

        cfg = {"feeds": [
            {"feed_id": "slow", "feed_class": "rss", "url": slow.url + "feed"},
            {"feed_id": "fast", "feed_class": "rss", "url": fast.url + "feed"},
        ]}
        store = FakeStore()

        total_new, failures = poll_rss.run(cfg, store)

        self.assertEqual(failures, [], "budget exhaustion is not a feed failure")
        slow_captures = sum(1 for (feed_id, _url) in store.captured if feed_id == "slow")
        self.assertEqual(slow_captures, 1,
                         "budget is checked before each fetch: the first article "
                         "(elapsed=0 < budget) should complete, the rest should not "
                         "even be attempted")
        fast_captures = sum(1 for (feed_id, _url) in store.captured if feed_id == "fast")
        self.assertEqual(fast_captures, 2, "the next feed must still run to completion")

    def test_poll_rss_never_selects_a_kind_gdelt_feed(self) -> None:
        # kind: gdelt points at a server that WOULD register captures if
        # poll_rss touched it — the assertion is that it never does, not
        # just that it fails gracefully.
        rss_server = servers.healthy_feed_server(n_articles=1)
        self.addCleanup(rss_server.stop)
        gdelt_server = servers.healthy_feed_server(n_articles=3)
        self.addCleanup(gdelt_server.stop)

        cfg = {"feeds": [
            {"feed_id": "rss-a", "feed_class": "rss", "kind": "rss",
             "url": rss_server.url + "feed"},
            {"feed_id": "gdelt-a", "feed_class": "structured_news", "kind": "gdelt",
             "query": "irrelevant here", "timespan": "7d"},
        ]}
        store = FakeStore()

        total_new, failures = poll_rss.run(cfg, store)

        self.assertEqual(total_new, 1, "only the rss feed's article should be captured")
        self.assertEqual(
            sum(1 for (feed_id, _url) in store.captured if feed_id == "gdelt-a"), 0,
            "the gdelt feed must never be touched by poll_rss")
        self.assertEqual(store.runs.get("gdelt-a"), None,
                         "no feed_runs row should be written for a feed poll_rss "
                         "never even selected")
        self.assertEqual(failures, [])


class BreakerIntegrationTests(unittest.TestCase):
    """Test b: a feed failing 5 runs in a row is skipped on the 6th with no
    connection attempt, exactly one quarantine notify across all six runs,
    and a healthy feed alongside it is polled every time."""

    def setUp(self) -> None:
        _patch_config(self,
                       HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1, FETCH_DEADLINE=2,
                       FEED_BUDGET=300, WAYBACK_ENABLED=False,
                       BREAKER_THRESHOLD=5, BREAKER_PROBE_H=24)
        self.notifications: list[tuple[str, str]] = []
        original_notify = common.notify

        def spy_notify(title, message, **kwargs):
            self.notifications.append((title, message))

        common.notify = spy_notify
        self.addCleanup(lambda: setattr(common, "notify", original_notify))

    def test_failing_feed_quarantined_on_sixth_run_healthy_feed_always_polled(self) -> None:
        black_hole = servers.black_hole_server()
        self.addCleanup(black_hole.stop)
        healthy = servers.healthy_feed_server(n_articles=1)
        self.addCleanup(healthy.stop)

        cfg = {"feeds": [
            {"feed_id": "bad", "feed_class": "rss", "url": black_hole.url},
            {"feed_id": "good", "feed_class": "rss", "url": healthy.url + "feed"},
        ]}
        store = FakeStore()

        for _ in range(6):
            poll_rss.run(cfg, store)

        # "good" dedupes on URL like any feed, so its one article is only a
        # NEW capture on the first run — "polled every time" is verified
        # below via its feed_runs rows (outcome='ok' x6), not via capture
        # count, which correctly stays at 1 after the first run.
        self.assertEqual(sum(1 for (fid, _url) in store.captured if fid == "good"), 1)

        bad_runs = store.runs["bad"]
        self.assertEqual(len(bad_runs), 6, "one feed_runs row per run, including the skip")
        outcomes = [r["outcome"] for r in bad_runs]           # most-recent-first
        self.assertEqual(outcomes[0], "skipped_quarantined",
                         "the 6th run should skip — no connection attempt")
        self.assertEqual(outcomes[1:], ["timeout"] * 5,
                         "the first 5 runs should have actually tried and timed out")

        good_runs = store.runs["good"]
        self.assertEqual(len(good_runs), 6, "the healthy feed is polled every run")
        self.assertTrue(all(r["outcome"] == "ok" for r in good_runs))

        quarantine_notifies = [n for n in self.notifications if "quarantined" in n[0]]
        self.assertEqual(len(quarantine_notifies), 1,
                         "exactly one quarantine notify across all six runs")


class LedgerRowTests(unittest.TestCase):
    """Test d: one feed_runs row per feed per run, with the outcome the
    spec assigns to each scenario."""

    def setUp(self) -> None:
        _patch_config(self,
                       HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1, FETCH_DEADLINE=2,
                       FEED_BUDGET=1, WAYBACK_ENABLED=False,
                       BREAKER_THRESHOLD=5, BREAKER_PROBE_H=24)

    def test_ledger_rows_have_the_right_outcome_per_scenario(self) -> None:
        black_hole = servers.black_hole_server()
        self.addCleanup(black_hole.stop)
        malformed = servers.delayed_response_server(delay=0, body=b"this is not a feed at all",
                                                    content_type="text/plain")
        self.addCleanup(malformed.stop)
        budget_stop = servers.healthy_feed_server(n_articles=3, article_delay=2)
        self.addCleanup(budget_stop.stop)
        healthy = servers.healthy_feed_server(n_articles=1)
        self.addCleanup(healthy.stop)

        cfg = {"feeds": [
            {"feed_id": "f-timeout", "feed_class": "rss", "url": black_hole.url},
            {"feed_id": "f-malformed", "feed_class": "rss", "url": malformed.url},
            {"feed_id": "f-budget", "feed_class": "rss", "url": budget_stop.url + "feed"},
            {"feed_id": "f-healthy", "feed_class": "rss", "url": healthy.url + "feed"},
            {"feed_id": "f-quarantined", "feed_class": "rss", "url": black_hole.url},
        ]}
        store = FakeStore()
        for _ in range(5):
            store.seed_run("f-quarantined", "failed")

        poll_rss.run(cfg, store)

        def outcome_of(feed_id: str) -> str:
            runs = store.runs[feed_id]
            self.assertEqual(len(runs), 1 if feed_id != "f-quarantined" else 6,
                             f"{feed_id}: expected exactly one NEW row this run "
                             f"(plus the 5 pre-seeded for f-quarantined)")
            return runs[0]["outcome"]

        self.assertEqual(outcome_of("f-timeout"), "timeout")
        self.assertEqual(outcome_of("f-malformed"), "failed")
        self.assertEqual(outcome_of("f-budget"), "budget_exhausted")
        self.assertEqual(outcome_of("f-healthy"), "ok")
        self.assertEqual(outcome_of("f-quarantined"), "skipped_quarantined")


class LedgerUnavailableTests(unittest.TestCase):
    """Test e: a store whose record_run/recent_runs raise never stops or
    fails a poll; the healthy feed still gets captured, and the warning is
    printed exactly once for the whole run()."""

    def setUp(self) -> None:
        _patch_config(self,
                       HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1, FETCH_DEADLINE=2,
                       FEED_BUDGET=300, WAYBACK_ENABLED=False)

    def test_broken_ledger_never_stops_a_poll_and_warns_once(self) -> None:
        healthy = servers.healthy_feed_server(n_articles=2)
        self.addCleanup(healthy.stop)
        black_hole = servers.black_hole_server()
        self.addCleanup(black_hole.stop)

        cfg = {"feeds": [
            {"feed_id": "bad", "feed_class": "rss", "url": black_hole.url},
            {"feed_id": "good", "feed_class": "rss", "url": healthy.url + "feed"},
        ]}
        store = BrokenLedgerStore(FakeStore())

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            total_new, failures = poll_rss.run(cfg, store)
        output = buf.getvalue()

        self.assertEqual(total_new, 2, "the healthy feed should still be fully captured")
        self.assertEqual({fid for fid, _ in failures}, {"bad"})
        self.assertEqual(output.count("feed_runs unavailable:"), 1,
                         "the ledger-unavailable warning must print exactly once per run()")


if __name__ == "__main__":
    unittest.main()
