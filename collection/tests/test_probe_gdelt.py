"""Static/structural checks on collection/probe_gdelt.py's PROBE_QUERIES,
plus one behavioral test for --query (item 2 of a gdelt-slow review round):
no real network — --query's request is driven through a monkeypatched
_issue_one, never a socket. The probe itself is hand-run only (see its own
module docstring); this just guards the query set's shape and the --query
short-circuit's behavior.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection — for
                                                                 # `import probe_gdelt`
                                                                 # and its own `from
                                                                 # collector import ...`
import probe_gdelt  # noqa: E402
from collector import fetcher  # noqa: E402


class ProbeQueriesShapeTests(unittest.TestCase):
    def test_at_most_max_requests(self) -> None:
        self.assertLessEqual(len(probe_gdelt.PROBE_QUERIES), probe_gdelt.MAX_REQUESTS)

    def test_first_query_is_the_certain_match_phrase_and_critical(self) -> None:
        label, params, critical = probe_gdelt.PROBE_QUERIES[0]
        self.assertEqual(label, "vnm_datacenter_phrase")
        self.assertTrue(critical, "the first probe must stop the run on a non-ok verdict")
        self.assertIn('"data center"', params["query"])

    def test_second_query_is_the_nonsense_no_match_probe_and_critical(self) -> None:
        label, params, critical = probe_gdelt.PROBE_QUERIES[1]
        self.assertEqual(label, "nonsense_no_match")
        self.assertTrue(critical)

    def test_third_query_is_last_not_critical_and_labelled_accordingly(self) -> None:
        # The only probe whose short term ("5G") makes a rejection an
        # EXPECTED POSSIBLE outcome must run LAST (so a rejection here
        # never costs learning what the first two probes prove) and must
        # never stop the run or change the exit code.
        label, params, critical = probe_gdelt.PROBE_QUERIES[2]
        self.assertEqual(label, "vnm_5g_vietnamese_lang_may_be_rejected")
        self.assertFalse(critical)
        self.assertIn("5G", params["query"])
        self.assertIn("sourcelang:vietnamese", params["query"])

    def test_critical_queries_have_no_term_under_three_characters(self) -> None:
        # A crude but sufficient check: split each critical query's non-
        # GDELT-operator words and confirm none of the "content" terms are
        # under 3 characters — operators like sourcecountry:VM/sourcelang:
        # vietnamese are excluded since they aren't free-text search terms.
        for label, params, critical in probe_gdelt.PROBE_QUERIES:
            if not critical:
                continue
            words = [w for w in params["query"].split()
                     if ":" not in w and w.lower() != "xyzzy1701nonexistentquerystring9999"]
            for word in words:
                self.assertGreaterEqual(len(word.strip('"')), 3,
                                        f"{label}: term {word!r} is under 3 characters")

    def test_all_probes_share_the_fixed_params(self) -> None:
        for label, params, _critical in probe_gdelt.PROBE_QUERIES:
            with self.subTest(label=label):
                for key, value in probe_gdelt.FIXED_PARAMS.items():
                    self.assertEqual(params[key], value)


class AdhocQueryTests(unittest.TestCase):
    """--query TEXT: exactly one request, never touching PROBE_QUERIES."""

    def setUp(self) -> None:
        self.output_dir = Path(tempfile.mkdtemp(prefix="sd-probe-test-"))
        self.addCleanup(shutil.rmtree, self.output_dir, True)
        self.calls: list[dict] = []
        original = probe_gdelt._issue_one

        def fake_issue_one(params: dict):
            self.calls.append(params)
            resp = fetcher.FetchResult(
                status_code=200, content=b'{"articles": []}',
                url="http://fixture.invalid/", headers={}, ok=True)
            return resp, 0.01

        probe_gdelt._issue_one = fake_issue_one
        self.addCleanup(lambda: setattr(probe_gdelt, "_issue_one", original))

    def test_query_issues_exactly_one_request(self) -> None:
        rc = probe_gdelt.main(["--query", "Viettel sourcecountry:VM", str(self.output_dir)])

        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["query"], "Viettel sourcecountry:VM")

    def test_query_never_touches_probe_queries(self) -> None:
        probe_gdelt.main(["--query", "Viettel sourcecountry:VM", str(self.output_dir)])

        called_queries = {call["query"] for call in self.calls}
        fixed_queries = {params["query"] for _label, params, _critical in probe_gdelt.PROBE_QUERIES}
        self.assertEqual(called_queries & fixed_queries, set(),
                         "an adhoc --query run must never issue any PROBE_QUERIES entry")
        saved_bodies = sorted(p.name for p in self.output_dir.glob("*.body"))
        self.assertEqual(saved_bodies, ["01_adhoc.body"],
                         "exactly one saved response, labelled 'adhoc' — no fixed-probe "
                         "labels present")

    def test_query_uses_the_same_fixed_params_as_the_probes(self) -> None:
        probe_gdelt.main(["--query", "irrelevant text", str(self.output_dir)])

        for key, value in probe_gdelt.FIXED_PARAMS.items():
            self.assertEqual(self.calls[0][key], value)

    def test_non_ok_verdict_on_adhoc_query_returns_nonzero(self) -> None:
        def fake_throttled(params: dict):
            resp = fetcher.FetchResult(
                status_code=429, content=b"", url="http://fixture.invalid/",
                headers={}, ok=False)
            return resp, 0.01

        probe_gdelt._issue_one = fake_throttled
        rc = probe_gdelt.main(["--query", "anything", str(self.output_dir)])

        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
