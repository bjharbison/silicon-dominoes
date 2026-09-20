"""Tests d-e from the harden-fetch spec: poll_rss with an in-memory fake in
place of the database, no Postgres involved. Exercises the seam added to
poll_rss (poll_feed/run take a `store`, never a conn) and the per-feed
wall-clock budget.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import config, poll_rss  # noqa: E402
import servers  # noqa: E402


class FakeStore:
    """In-memory stand-in for poll_rss.DbStore — no Postgres, no conn."""

    def __init__(self) -> None:
        self.captured: dict[tuple[str, str], bytes] = {}
        self.failures: list[str] = []

    def already_captured(self, feed_id: str, url: str) -> bool:
        return (feed_id, url) in self.captured

    def record_capture(self, feed_id: str, url: str, payload: bytes, ext: str,
                       status: str, snapshot_id, snapshot_url) -> bool:
        key = (feed_id, url)
        if key in self.captured:
            return False
        self.captured[key] = payload
        return True


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


if __name__ == "__main__":
    unittest.main()
