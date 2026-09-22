"""Tests for fetcher.classify_gdelt_body — pure, no I/O, no network. A
throttled GDELT response carries no JSON error object, so an unparseable
body must never be classified 'ok': that silent-throttle-read-as-zero-
coverage is exactly what this classifier exists to prevent.

Decision order under test (see classify_gdelt_body's own docstring):
status 429 -> throttled outright; any other non-2xx -> never 'ok' (sig
match -> throttled, else unknown); 2xx parses JSON FIRST (a real 'ok'
body's own content must never be shadowed by a coincidental signature
substring); only if that fails does a signature match get consulted.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import fetcher  # noqa: E402


class ClassifyGdeltBodyTests(unittest.TestCase):
    def test_well_formed_json_with_articles_is_ok(self) -> None:
        body = json.dumps({"articles": [{"url": "https://fixture.invalid/a"}]}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "ok")

    def test_empty_articles_list_is_ok_not_empty(self) -> None:
        body = json.dumps({"articles": []}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "ok")

    def test_throttle_message_body_is_throttled(self) -> None:
        body = b"You have used your 1 request every 5 seconds quota."
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "throttled")

    def test_throttle_signature_matches_case_insensitively(self) -> None:
        body = b"TOO MANY REQUESTS - slow down"
        self.assertEqual(fetcher.classify_gdelt_body(429, body), "throttled")

    def test_empty_body_is_unknown(self) -> None:
        self.assertEqual(fetcher.classify_gdelt_body(200, b""), "unknown")

    def test_whitespace_only_body_is_unknown(self) -> None:
        self.assertEqual(fetcher.classify_gdelt_body(200, b"   \n\t  "), "unknown")

    def test_html_body_is_unknown(self) -> None:
        body = b"<html><body><h1>503 Service Unavailable</h1></body></html>"
        self.assertEqual(fetcher.classify_gdelt_body(503, body), "unknown")

    def test_malformed_json_is_unknown(self) -> None:
        body = b'{"articles": [ this is not valid json'
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "unknown")

    def test_valid_json_without_articles_key_is_unknown(self) -> None:
        body = json.dumps({"status": "ok", "count": 0}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "unknown")

    def test_json_array_not_object_is_unknown(self) -> None:
        # Valid JSON, but not an object with an 'articles' key.
        body = json.dumps([1, 2, 3]).encode()
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "unknown")

    def test_non_json_body_is_never_ok(self) -> None:
        bodies = [
            b"",
            b"   ",
            b"<html>not json</html>",
            b"plain text response",
            b'{"broken":',
            b"null",
            b"42",
            b'"just a string"',
        ]
        for body in bodies:
            verdict = fetcher.classify_gdelt_body(200, body)
            self.assertNotEqual(verdict, "ok", f"body {body!r} must not classify as ok")

    def test_articles_content_matching_a_signature_is_still_ok(self) -> None:
        # JSON is parsed BEFORE any signature check on a 2xx response — a
        # real article whose own title happens to say "rate limit" or "too
        # many requests" must not be misread as a throttle notice.
        body = json.dumps({"articles": [
            {"title": "Regulator proposes new rate limit on data exports"},
            {"title": "Vendor warns of too many requests during peak hours"},
        ]}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "ok")

    def test_429_with_empty_body_is_throttled(self) -> None:
        self.assertEqual(fetcher.classify_gdelt_body(429, b""), "throttled")

    def test_429_with_valid_articles_json_is_still_throttled(self) -> None:
        # 429 wins outright, even over a body that would otherwise parse
        # as well-formed 'ok' JSON — status is checked before body.
        body = json.dumps({"articles": [{"url": "https://fixture.invalid/a"}]}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(429, body), "throttled")

    def test_503_with_valid_articles_json_is_unknown_not_ok(self) -> None:
        # A non-2xx status can never be 'ok', regardless of body content —
        # only 429 is an unambiguous throttle signal; any other non-2xx
        # with no signature match is 'unknown', not 'ok' and not 'throttled'.
        body = json.dumps({"articles": [{"url": "https://fixture.invalid/a"}]}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(503, body), "unknown")

    def test_articles_value_not_a_list_is_unknown(self) -> None:
        body = json.dumps({"articles": "none"}).encode()
        self.assertEqual(fetcher.classify_gdelt_body(200, body), "unknown")


if __name__ == "__main__":
    unittest.main()
