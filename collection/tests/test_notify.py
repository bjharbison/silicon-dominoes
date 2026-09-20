"""Tests a-h from the notify-honest spec: common.notify() must be honest
about whether an alert was actually delivered (SD_NTFY_URL sat empty in
production from 2026-08-16 to 2026-09-20 and every call site behaved as if
delivery had succeeded), and must never leak SD_NTFY_URL — or any
recognizable fragment of it — into a printed line. Stub servers bind
127.0.0.1 on ephemeral ports; no external network.
"""
from __future__ import annotations

import io
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import common, config, fetcher  # noqa: E402
import servers  # noqa: E402

COLLECTOR_DIR = Path(__file__).resolve().parents[1] / "collector"


def _patch_config(test: unittest.TestCase, **overrides) -> None:
    """Config values are read fresh on every call (never cached at import),
    so overriding them for the duration of a test is a plain attribute
    patch — see collector/config.py and collector/fetcher.py."""
    originals = {name: getattr(config, name) for name in overrides}
    for name, value in overrides.items():
        setattr(config, name, value)
    test.addCleanup(lambda: [setattr(config, n, v) for n, v in originals.items()])


class EmptyUrlTests(unittest.TestCase):
    def test_a_empty_url_returns_false_prints_not_sent_zero_connections(self) -> None:
        _patch_config(self, NTFY_URL="")
        server = servers.notify_stub_server(200)
        self.addCleanup(server.stop)

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = common.notify("Title", "message")
        output = buf.getvalue()

        self.assertFalse(result)
        self.assertIn("[notify] NOT SENT (SD_NTFY_URL is empty): Title", output)
        self.assertIn("[notify] Title: message", output,
                      "the existing summary line must still print")
        self.assertEqual(server.hit_count, 0, "no connection should have been attempted")


class DeliveryOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        _patch_config(self, HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1,
                     FETCH_DEADLINE=2, NTFY_TIMEOUT=2)

    def test_b_200_returns_true_headers_received(self) -> None:
        server = servers.notify_stub_server(200)
        self.addCleanup(server.stop)
        _patch_config(self, NTFY_URL=server.url)

        result = common.notify("Some Title", "some message", priority="high", tags="warning")

        self.assertTrue(result)
        self.assertEqual(server.hit_count, 1)
        self.assertEqual(server.last_headers.get("Title"), "Some Title")
        self.assertEqual(server.last_headers.get("Priority"), "high")
        self.assertEqual(server.last_headers.get("Tags"), "warning")
        self.assertEqual(server.last_body, b"some message")

    def test_c_403_returns_false_prints_refused(self) -> None:
        server = servers.notify_stub_server(403)
        self.addCleanup(server.stop)
        _patch_config(self, NTFY_URL=server.url)

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = common.notify("Title", "message")

        self.assertFalse(result)
        self.assertIn("[notify] delivery refused: HTTP 403", buf.getvalue())

    def test_d_black_hole_returns_false_within_deadline_no_exception(self) -> None:
        server = servers.black_hole_server()
        self.addCleanup(server.stop)
        _patch_config(self, NTFY_URL=server.url)

        start = time.monotonic()
        result = common.notify("Title", "message")
        elapsed = time.monotonic() - start

        self.assertFalse(result)
        self.assertLess(elapsed, config.NTFY_TIMEOUT + 2,
                        "must not block past the notify deadline")

    def test_e_non_fetch_error_exception_returns_false_no_raise(self) -> None:
        original_post = fetcher.post

        def raising_post(*a, **kw):
            raise RuntimeError("boom, not a FetchError")

        fetcher.post = raising_post
        self.addCleanup(lambda: setattr(fetcher, "post", original_post))
        _patch_config(self, NTFY_URL="http://127.0.0.1:1/unused")

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = common.notify("Title", "message")   # must not raise

        self.assertFalse(result)
        self.assertIn("[notify] delivery failed:", buf.getvalue())
        self.assertIn("RuntimeError", buf.getvalue())


class RedactionTests(unittest.TestCase):
    """Test g: with a configured URL containing a recognizable marker
    string, no printed line across the b/c/d/e-equivalent scenarios (plus
    the trivial empty-URL case) contains that marker."""

    def test_g_marker_never_appears_in_output(self) -> None:
        marker = "MARKERXYZ999TOPIC"
        _patch_config(self, HTTP_CONNECT_TIMEOUT=1, HTTP_READ_TIMEOUT=1,
                     FETCH_DEADLINE=2, NTFY_TIMEOUT=2)

        server200 = servers.notify_stub_server(200)
        self.addCleanup(server200.stop)
        server403 = servers.notify_stub_server(403)
        self.addCleanup(server403.stop)
        black_hole = servers.black_hole_server()
        self.addCleanup(black_hole.stop)

        outputs = []

        for server in (server200, server403, black_hole):
            _patch_config(self, NTFY_URL=f"{server.url}{marker}")
            buf = io.StringIO()
            with redirect_stdout(buf):
                common.notify("Title", "message")
            outputs.append(buf.getvalue())

        # A non-FetchError exception whose own message embeds the URL —
        # exactly the case _redact_ntfy_url exists for.
        _patch_config(self, NTFY_URL=f"http://127.0.0.1:1/{marker}")
        original_post = fetcher.post

        def raising_post(url, **kw):
            raise RuntimeError(f"connection refused to {url}")

        fetcher.post = raising_post
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                common.notify("Title", "message")
            outputs.append(buf.getvalue())
        finally:
            fetcher.post = original_post

        _patch_config(self, NTFY_URL="")
        buf = io.StringIO()
        with redirect_stdout(buf):
            common.notify("Title", "message")
        outputs.append(buf.getvalue())

        for output in outputs:
            self.assertNotIn(marker, output)

    def test_bare_topic_without_leading_slash_is_redacted(self) -> None:
        """Follow-up item 1: the bare topic (URL path with no leading
        slash) must be redacted too when it's distinctive enough (>= 8
        chars) — an exception message might reference just the topic name,
        with no scheme/host/slash for the URL-shaped redaction to match."""
        topic = "MARKERXYZ999TOPIC"                 # 18 chars, well over 8
        self.assertGreaterEqual(len(topic), 8)
        _patch_config(self, NTFY_URL=f"http://127.0.0.1:1/{topic}")
        original_post = fetcher.post

        def raising_post(url, **kw):
            # Deliberately contains ONLY the bare topic — no slash, no
            # host, nothing the URL/netloc/path-with-slash checks would
            # catch on their own.
            raise RuntimeError(f"internal reference: topic={topic} rejected")

        fetcher.post = raising_post
        self.addCleanup(lambda: setattr(fetcher, "post", original_post))

        buf = io.StringIO()
        with redirect_stdout(buf):
            common.notify("Title", "message")

        self.assertNotIn(topic, buf.getvalue())


class HeaderSanitizationTests(unittest.TestCase):
    """Follow-up item 2: notify() must still deliver when the title has
    characters that can't go in a raw HTTP header (headers are latin-1;
    ntfy titles are free text someone typed, e.g. with an em dash)."""

    def test_title_with_em_dash_still_delivers(self) -> None:
        server = servers.notify_stub_server(200)
        self.addCleanup(server.stop)
        _patch_config(self, NTFY_URL=server.url, HTTP_CONNECT_TIMEOUT=1,
                     HTTP_READ_TIMEOUT=1, FETCH_DEADLINE=2, NTFY_TIMEOUT=2)

        result = common.notify("Feed health — 3 opened", "message body unaffected")

        self.assertTrue(result, "an unencodable title must not prevent delivery")
        self.assertEqual(server.hit_count, 1)
        self.assertIsNotNone(server.last_headers)
        self.assertIn("Title", server.last_headers,
                      "the stub must have received SOME Title header value")
        self.assertEqual(server.last_body, b"message body unaffected",
                         "the message body must stay UTF-8 and unchanged")


class StartupWarningTests(unittest.TestCase):
    def test_h_warning_when_empty_not_otherwise(self) -> None:
        _patch_config(self, NTFY_URL="")
        buf = io.StringIO()
        with redirect_stdout(buf):
            common.warn_if_ntfy_unconfigured()
        self.assertIn("WARNING: SD_NTFY_URL is empty - notifications are journal-only",
                     buf.getvalue())

        _patch_config(self, NTFY_URL="http://127.0.0.1:9/somewhere")
        buf = io.StringIO()
        with redirect_stdout(buf):
            common.warn_if_ntfy_unconfigured()
        self.assertEqual(buf.getvalue(), "", "must print nothing when a URL is configured")


class SharedHelperStaticTests(unittest.TestCase):
    """'Put the check in one shared helper, not six copies' (spec point 5).
    Static source scan — confirms every entry point calls the shared
    helper, and no file reimplements the inline check it replaces."""

    ENTRY_FILES = ("poll_rss.py", "feed_health.py", "verify_watch.py",
                  "snapshot_retry.py", "poll_gdelt.py", "extract.py")

    def test_every_entry_point_calls_the_shared_helper(self) -> None:
        missing = []
        for name in self.ENTRY_FILES:
            text = (COLLECTOR_DIR / name).read_text(encoding="utf-8")
            if "common.warn_if_ntfy_unconfigured()" not in text:
                missing.append(name)
        self.assertEqual(missing, [], f"missing the startup warning call: {missing}")

    def test_no_duplicate_inline_check_outside_common(self) -> None:
        offenders = []
        for path in sorted(COLLECTOR_DIR.glob("*.py")):
            if path.name == "common.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "SD_NTFY_URL is empty" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"duplicate inline check found in: {offenders}")


if __name__ == "__main__":
    unittest.main()
