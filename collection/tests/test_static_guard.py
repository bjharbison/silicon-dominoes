"""Test f from the harden-fetch spec: no code path outside collector/fetcher.py
may make an unbounded HTTP call. Static source scan, not an import-time
check, so it also catches dead/unused code that would otherwise pass every
other test.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests

COLLECTOR_DIR = Path(__file__).resolve().parents[1] / "collector"
FETCHER_MODULE = "fetcher.py"

RAW_CALL_PATTERNS = [
    re.compile(r"\brequests\.(get|post|put|delete|patch|head|request)\s*\("),
    re.compile(r"\burllib\.request\.urlopen\s*\("),
]

# feedparser must never fetch a URL itself — only ever parse bytes/text this
# package already fetched through fetcher.fetch(). A call whose argument looks
# like a bytes-ish variable (content/payload/raw/data/bytes/...) is fine; a
# bare url/link variable, or a string literal, is exactly the bug this test
# exists to catch.
FEEDPARSER_CALL = re.compile(r"feedparser\.parse\s*\(\s*([^,)\n]*)")
BYTES_LIKE = re.compile(r"(content|bytes|payload|raw|data)", re.IGNORECASE)
URL_LIKE = re.compile(r"^(['\"]|f['\"]|url\b|link\b|feed\[|entry\[)")


class StaticGuardTests(unittest.TestCase):
    def _source_files(self):
        return sorted(p for p in COLLECTOR_DIR.glob("*.py"))

    def test_no_raw_http_calls_outside_fetcher_module(self) -> None:
        violations = []
        for path in self._source_files():
            if path.name == FETCHER_MODULE:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in RAW_CALL_PATTERNS:
                for match in pattern.finditer(text):
                    line_no = text[:match.start()].count("\n") + 1
                    violations.append(f"{path.name}:{line_no}: {match.group(0)!r}")
        self.assertFalse(
            violations,
            "unbounded HTTP calls found outside collector/fetcher.py:\n" + "\n".join(violations))

    def test_feedparser_never_fetches_a_url_itself(self) -> None:
        violations = []
        for path in self._source_files():
            text = path.read_text(encoding="utf-8")
            for match in FEEDPARSER_CALL.finditer(text):
                arg = match.group(1).strip()
                if not arg:
                    continue
                if BYTES_LIKE.search(arg):
                    continue
                if URL_LIKE.match(arg) or arg in ("url", "link"):
                    line_no = text[:match.start()].count("\n") + 1
                    violations.append(f"{path.name}:{line_no}: feedparser.parse({arg})")
        self.assertFalse(
            violations,
            "feedparser.parse() called with something other than fetched bytes "
            "(feedparser must never fetch a URL itself):\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
