"""Test for item 4 of the harden-fetch follow-up: extract.py's call_llm()
must give its HTTP call its own (long) deadline/read_timeout rather than
inheriting the short generic default — a local model sends nothing back
until the whole completion is ready, so a short read timeout would trip on
every slow-but-healthy generation.

No Postgres, no external network — a stub server stands in for the LLM
endpoint (see servers.py).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import config, extract  # noqa: E402
import servers  # noqa: E402


def _patch_config(test: unittest.TestCase, **overrides) -> None:
    originals = {name: getattr(config, name) for name in overrides}
    for name, value in overrides.items():
        setattr(config, name, value)
    test.addCleanup(lambda: [setattr(config, n, v) for n, v in originals.items()])


class CallLlmDeadlineTests(unittest.TestCase):
    def test_call_llm_survives_a_slow_response_under_a_tiny_generic_deadline(self) -> None:
        """FETCH_DEADLINE (the generic default every other caller relies on)
        is patched to 1s — far shorter than the LLM stub's 3s delay. If
        call_llm() used the generic default instead of LLM_DEADLINE, this
        call would raise/return None. It must succeed because call_llm()
        passes its own read_timeout/deadline explicitly."""
        server = servers.llm_stub_server(
            delay=3, content='{"relevant": false, "events": []}')
        self.addCleanup(server.stop)
        _patch_config(self,
                       FETCH_DEADLINE=1, HTTP_READ_TIMEOUT=1,
                       LLM_DEADLINE=10, LLM_BASE=server.url.rstrip("/"))

        result = extract.call_llm("some article text about a data center", "http://x.invalid/a")

        self.assertIsNotNone(result, "call_llm() returned None — it is not giving its "
                                     "HTTP call a deadline long enough for a 3s response")
        self.assertEqual(result, {"relevant": False, "events": []})


if __name__ == "__main__":
    unittest.main()
