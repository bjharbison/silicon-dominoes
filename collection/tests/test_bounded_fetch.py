"""Tests a-c from the harden-fetch spec: the bounded fetcher itself.

No Postgres, no external network — stub servers bind 127.0.0.1 on ephemeral
ports (see servers.py). Timeouts are 1-2s so the whole file runs in a few
seconds.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import fetcher  # noqa: E402
import servers  # noqa: E402


class BoundedFetchTests(unittest.TestCase):
    def test_black_hole_server_raises_fetch_timeout_within_deadline(self) -> None:
        server = servers.black_hole_server()
        self.addCleanup(server.stop)
        deadline = 2.0
        start = time.monotonic()
        with self.assertRaises(fetcher.FetchTimeout):
            fetcher.fetch(server.url, connect_timeout=1, read_timeout=1, deadline=deadline)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, deadline + 1,
                         "black-hole fetch did not raise within deadline + 1s")

    def test_trickle_server_raises_fetch_timeout_within_total_deadline(self) -> None:
        """The case a read timeout alone cannot catch: the server sends
        headers immediately and then one byte per second forever, so no
        single read ever blocks long enough to trip a per-read timeout.
        Only the wall-clock total deadline, checked while streaming, does —
        this is the assertion that proves that deadline exists and works."""
        server = servers.trickle_server()
        self.addCleanup(server.stop)
        deadline = 2.0
        start = time.monotonic()
        with self.assertRaises(fetcher.FetchTimeout):
            # A read timeout far longer than the deadline: if the deadline
            # were not enforced independently of the read timeout, this
            # fetch would hang well past deadline + 1s.
            fetcher.fetch(server.url, connect_timeout=1, read_timeout=30, deadline=deadline)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, deadline + 1,
                         "trickle fetch did not raise within deadline + 1s — "
                         "the total deadline is not being enforced during streaming")

    def test_oversized_response_raises_fetch_too_large(self) -> None:
        server = servers.oversized_server(size_bytes=200_000)
        self.addCleanup(server.stop)
        with self.assertRaises(fetcher.FetchTooLarge):
            fetcher.fetch(server.url, connect_timeout=2, read_timeout=2, deadline=5,
                      max_bytes=1000)

    def test_healthy_response_returns_normally(self) -> None:
        server = servers.oversized_server(size_bytes=10)
        self.addCleanup(server.stop)
        result = fetcher.fetch(server.url, connect_timeout=2, read_timeout=2, deadline=5,
                            max_bytes=1000)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, b"a" * 10)

    def test_header_trickle_raises_fetch_timeout_within_deadline(self) -> None:
        """The other half of the whole-call deadline: a server that never
        finishes sending its status line/headers. The deadline must cover
        the wait for headers too, not just the body-streaming phase — a
        read_timeout far longer than the deadline proves it's the deadline
        catching this, not the per-op timeout."""
        server = servers.header_trickle_server()
        self.addCleanup(server.stop)
        deadline = 2.0
        start = time.monotonic()
        with self.assertRaises(fetcher.FetchTimeout):
            fetcher.fetch(server.url, connect_timeout=1, read_timeout=30, deadline=deadline)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, deadline + 1,
                         "header-trickle fetch did not raise within deadline + 1s — "
                         "the deadline is not covering the wait for headers")

    def test_5mb_as_fast_as_possible_completes_quickly(self) -> None:
        """Throughput regression: chunk_size=1 (needed at one point to catch
        a byte-at-a-time trickle) costs one Python-level loop iteration per
        byte, which made a legitimate multi-MB response pathologically
        slow. Report the measured time so a regression here is visible in
        the test log, not just a pass/fail."""
        size = 5 * 1024 * 1024
        server = servers.oversized_server(size_bytes=size)
        self.addCleanup(server.stop)
        start = time.monotonic()
        result = fetcher.fetch(server.url, connect_timeout=5, read_timeout=5, deadline=10,
                               max_bytes=size + 1)
        elapsed = time.monotonic() - start
        print(f"    [timing] 5 MiB fetch took {elapsed:.3f}s")
        self.assertEqual(len(result.content), size)
        self.assertLess(elapsed, 3.0,
                         f"5 MiB fetch took {elapsed:.3f}s — should complete in under 3s")

    def test_gzip_content_encoding_is_returned_decoded(self) -> None:
        original = b"repeat me " * 5000
        server = servers.gzip_server(original)
        self.addCleanup(server.stop)
        result = fetcher.fetch(server.url, connect_timeout=2, read_timeout=2, deadline=5,
                               max_bytes=len(original) * 2)
        self.assertEqual(result.content, original,
                         "fetch() should hand back the decoded body, not the raw gzip stream")

    def test_explicit_deadline_overrides_a_small_config_default(self) -> None:
        """config.FETCH_DEADLINE patched to 1s; a call with no override
        should time out against a 3s-slow server, but the same call with an
        explicit deadline=5/read_timeout=5 should succeed — this is exactly
        the override extract.call_llm relies on to give the LLM call its own
        (much longer) budget regardless of the generic default."""
        from collector import config
        original_deadline = config.FETCH_DEADLINE
        config.FETCH_DEADLINE = 1
        self.addCleanup(lambda: setattr(config, "FETCH_DEADLINE", original_deadline))

        server = servers.delayed_response_server(delay=3, body=b"ok")
        self.addCleanup(server.stop)

        with self.assertRaises(fetcher.FetchTimeout):
            fetcher.fetch(server.url, connect_timeout=1, read_timeout=1)

        result = fetcher.fetch(server.url, connect_timeout=1, deadline=5, read_timeout=5)
        self.assertEqual(result.content, b"ok")


if __name__ == "__main__":
    unittest.main()
