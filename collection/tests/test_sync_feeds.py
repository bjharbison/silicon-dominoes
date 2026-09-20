"""Test j from the harden-health spec: config.present_feed_ids — the pure
set logic sync_feeds uses to decide which `feeds` rows to retire. No
database: sync_feeds itself still needs one (it writes), but the set logic
that decides absent -> inactive / returned -> active is split out precisely
so it doesn't.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import config  # noqa: E402


class PresentFeedIdsTests(unittest.TestCase):
    def test_feeds_and_verify_items_both_count_as_present(self) -> None:
        cfg = {
            "feeds": [{"feed_id": "rss-a"}, {"feed_id": "rss-b"}],
            "verify_items": [{"feed_id": "verify-x"}],
        }
        self.assertEqual(config.present_feed_ids(cfg), {"rss-a", "rss-b", "verify-x"})

    def test_a_feed_id_removed_from_feeds_yaml_is_absent(self) -> None:
        cfg = {"feeds": [{"feed_id": "rss-a"}], "verify_items": []}
        present = config.present_feed_ids(cfg)
        self.assertIn("rss-a", present)
        self.assertNotIn("rss-retired", present)

    def test_a_feed_id_that_returns_is_present_again(self) -> None:
        before = config.present_feed_ids({"feeds": [], "verify_items": []})
        after = config.present_feed_ids({"feeds": [{"feed_id": "rss-a"}], "verify_items": []})
        self.assertNotIn("rss-a", before)
        self.assertIn("rss-a", after)

    def test_empty_config(self) -> None:
        self.assertEqual(config.present_feed_ids({}), set())


if __name__ == "__main__":
    unittest.main()
