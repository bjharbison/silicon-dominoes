"""Tests for config.feed_kind / config.validate_feed_kinds (gdelt-slow) —
pure, no I/O, no database. The one behavior that matters most here: an
unrecognised kind must abort BEFORE sync_feeds touches any SQL, never as a
silent skip (that residual is reserved for present_feed_ids() returning
empty on a broken feeds.yaml — see validate_feed_kinds' own docstring for
why this is deliberately not widened to also swallow a bad kind).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import config  # noqa: E402


class FeedKindTests(unittest.TestCase):
    def test_absent_kind_is_rss(self) -> None:
        self.assertEqual(config.feed_kind({"feed_id": "x"}), "rss")

    def test_explicit_rss_kind(self) -> None:
        self.assertEqual(config.feed_kind({"feed_id": "x", "kind": "rss"}), "rss")

    def test_explicit_gdelt_kind(self) -> None:
        self.assertEqual(config.feed_kind({"feed_id": "x", "kind": "gdelt"}), "gdelt")


class ValidateFeedKindsTests(unittest.TestCase):
    def test_absent_kind_does_not_raise(self) -> None:
        cfg = {"feeds": [{"feed_id": "rss-a", "feed_class": "rss"}]}
        config.validate_feed_kinds(cfg)  # must not raise

    def test_known_kinds_do_not_raise(self) -> None:
        cfg = {"feeds": [
            {"feed_id": "rss-a", "feed_class": "rss", "kind": "rss"},
            {"feed_id": "gdelt-a", "feed_class": "structured_news", "kind": "gdelt",
             "query": "5G sourcecountry:VM"},
        ]}
        config.validate_feed_kinds(cfg)  # must not raise

    def test_unrecognised_kind_raises(self) -> None:
        cfg = {"feeds": [{"feed_id": "bad-a", "feed_class": "rss", "kind": "gdlet"}]}
        with self.assertRaises(ValueError) as ctx:
            config.validate_feed_kinds(cfg)
        self.assertIn("bad-a", str(ctx.exception))
        self.assertIn("gdlet", str(ctx.exception))

    def test_no_feeds_key_does_not_raise(self) -> None:
        config.validate_feed_kinds({})  # must not raise

    def test_verify_items_are_not_checked(self) -> None:
        # kind only applies to `feeds:` entries — verify_items have no
        # poller-selection concept, so a stray/absent kind there is inert.
        cfg = {"feeds": [], "verify_items": [{"feed_id": "verify-a", "kind": "nonsense"}]}
        config.validate_feed_kinds(cfg)  # must not raise

    def test_kind_rss_with_wrong_feed_class_raises(self) -> None:
        cfg = {"feeds": [{"feed_id": "mismatch-a", "feed_class": "structured_news",
                          "kind": "rss"}]}
        with self.assertRaises(ValueError) as ctx:
            config.validate_feed_kinds(cfg)
        self.assertIn("mismatch-a", str(ctx.exception))

    def test_kind_gdelt_with_wrong_feed_class_raises(self) -> None:
        cfg = {"feeds": [{"feed_id": "mismatch-b", "feed_class": "rss", "kind": "gdelt",
                          "query": "5G"}]}
        with self.assertRaises(ValueError) as ctx:
            config.validate_feed_kinds(cfg)
        self.assertIn("mismatch-b", str(ctx.exception))

    def test_absent_kind_with_wrong_feed_class_raises(self) -> None:
        # Absent kind defaults to 'rss' for feed_kind()'s own purposes, but
        # it still must agree with feed_class — an rss-kind entry mislabeled
        # feed_class: structured_news is exactly the split this check exists
        # to catch, whether or not `kind` was written out explicitly.
        cfg = {"feeds": [{"feed_id": "mismatch-c", "feed_class": "structured_news"}]}
        with self.assertRaises(ValueError):
            config.validate_feed_kinds(cfg)

    def test_kind_gdelt_missing_query_raises(self) -> None:
        cfg = {"feeds": [{"feed_id": "no-query", "feed_class": "structured_news",
                          "kind": "gdelt"}]}
        with self.assertRaises(ValueError) as ctx:
            config.validate_feed_kinds(cfg)
        self.assertIn("no-query", str(ctx.exception))

    def test_kind_gdelt_blank_query_raises(self) -> None:
        cfg = {"feeds": [{"feed_id": "blank-query", "feed_class": "structured_news",
                          "kind": "gdelt", "query": "   "}]}
        with self.assertRaises(ValueError):
            config.validate_feed_kinds(cfg)

    def test_kind_gdelt_non_string_query_raises(self) -> None:
        cfg = {"feeds": [{"feed_id": "bad-query-type", "feed_class": "structured_news",
                          "kind": "gdelt", "query": ["not", "a", "string"]}]}
        with self.assertRaises(ValueError):
            config.validate_feed_kinds(cfg)

    def test_kind_rss_does_not_require_query(self) -> None:
        cfg = {"feeds": [{"feed_id": "rss-a", "feed_class": "rss", "kind": "rss"}]}
        config.validate_feed_kinds(cfg)  # must not raise


class _RaisingCursorConn:
    """A fake psycopg-style connection whose cursor() raises if ever
    called — proves sync_feeds aborts on an unrecognised kind strictly
    before touching SQL, not merely before committing."""

    def cursor(self):
        raise AssertionError("sync_feeds must not reach conn.cursor() "
                             "when a feed's kind is invalid")

    def commit(self):
        raise AssertionError("sync_feeds must not reach conn.commit() "
                             "when a feed's kind is invalid")


class SyncFeedsAbortsBeforeSqlTests(unittest.TestCase):
    def test_unrecognised_kind_aborts_before_any_sql(self) -> None:
        cfg = {"feeds": [{"feed_id": "bad-a", "feed_class": "rss",
                          "kind": "gdlet", "url": "https://fixture.invalid/a"}]}
        conn = _RaisingCursorConn()
        with self.assertRaises(ValueError):
            config.sync_feeds(conn, cfg)

    def test_absent_kind_reaches_sql(self) -> None:
        # Sanity check on the fake itself: a VALID config should reach
        # conn.cursor() (and therefore hit the fake's AssertionError,
        # proving the raise above was actually about validation ordering
        # and not just "the fake always raises").
        cfg = {"feeds": [{"feed_id": "ok-a", "feed_class": "rss",
                          "url": "https://fixture.invalid/a"}]}
        conn = _RaisingCursorConn()
        with self.assertRaises(AssertionError):
            config.sync_feeds(conn, cfg)


if __name__ == "__main__":
    unittest.main()
