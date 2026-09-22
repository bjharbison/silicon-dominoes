"""Tests for poll_gdelt: the pure query-selection function on its own, then
the orchestration in run() driven with an in-memory FakeStore and a fake
fetch_query callable — no Postgres, no EXTERNAL network. `now` is always
injected explicitly (never datetime.now()), per the harden-health-era
lesson (see test_gdelt_breaker.py's module docstring). RealFetchPathTests
is the one exception to "no fetch_query injected", and even it only ever
talks to a local 127.0.0.1 ephemeral-port stub server (tests/servers.py) —
the same pattern test_poll_rss.py already uses throughout, never the real
internet or the real GDELT endpoint.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import config, fetcher, poll_gdelt  # noqa: E402
import servers  # noqa: E402

T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def _run(outcome: str, at: datetime, **extra) -> dict:
    row = {"outcome": outcome, "started_at": at, "finished_at": at,
          "entries_seen": 0, "new_captures": 0, "error": None}
    row.update(extra)
    return row


def _feed(feed_id: str, **extra) -> dict:
    feed = {"feed_id": feed_id, "feed_class": "structured_news", "kind": "gdelt",
           "query": "5G", "timespan": "7d"}
    feed.update(extra)
    return feed


class SelectNextQueryTests(unittest.TestCase):
    def test_never_attempted_feed_sorts_first(self) -> None:
        feeds = [_feed("a"), _feed("b")]
        histories = {"a": [_run("ok", T0 - timedelta(days=1))], "b": []}
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertEqual(selection.feed["feed_id"], "b")
        self.assertEqual(selection.reason, "never_attempted")

    def test_ok_within_min_gap_is_skipped(self) -> None:
        feeds = [_feed("a")]
        histories = {"a": [_run("ok", T0 - timedelta(days=2))]}
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertIsNone(selection)

    def test_least_recently_attempted_wins_among_eligible(self) -> None:
        feeds = [_feed("a"), _feed("b")]
        histories = {
            "a": [_run("ok", T0 - timedelta(days=10))],
            "b": [_run("ok", T0 - timedelta(days=20))],
        }
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertEqual(selection.feed["feed_id"], "b")
        self.assertEqual(selection.reason, "least_recently_attempted")

    def test_all_recent_means_nothing_to_do(self) -> None:
        feeds = [_feed("a"), _feed("b")]
        histories = {
            "a": [_run("ok", T0 - timedelta(days=1))],
            "b": [_run("ok", T0 - timedelta(days=3))],
        }
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertIsNone(selection)

    def test_exactly_at_min_gap_boundary_is_eligible(self) -> None:
        # Eligibility is (now - last_ok) < min_gap — exactly AT the
        # boundary is not strictly less than min_gap, so "inside 7 days"
        # has just ended and the query becomes eligible again.
        feeds = [_feed("a")]
        histories = {"a": [_run("ok", T0 - timedelta(days=7))]}
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertIsNotNone(selection)
        self.assertEqual(selection.feed["feed_id"], "a")

    def test_failed_and_throttled_runs_do_not_count_as_ok_for_eligibility(self) -> None:
        # A feed whose only history is failures/throttles has never had an
        # 'ok', so it's never excluded by the min_gap eligibility check —
        # but (unlike the old buggy version) it's NOT still 'never
        # attempted' either: it has real attempts, so it sorts by their
        # recency like any other query with a real-attempt history (see
        # the head-of-line-blocking test below for why this distinction
        # matters).
        feeds = [_feed("a")]
        histories = {"a": [_run("throttled", T0 - timedelta(hours=1)),
                          _run("failed", T0 - timedelta(hours=2))]}
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertEqual(selection.reason, "least_recently_attempted")

    def test_head_of_line_blocking_fixed_never_selects_the_same_chronic_failure_twice_running(
            self) -> None:
        # Regression test for the bug the ORDER change exists to fix: A
        # always fails every time it's picked; B starts out never having
        # been attempted at all. The old "no ok yet sorts first" rule tied
        # A and B forever (neither ever gets an 'ok'), so feeds.yaml order
        # picked A every single time, starving B completely. The fix:
        # after each selection, A's real attempt (however it turned out)
        # moves it out of the "never attempted" bucket, so the OTHER
        # query — whichever one hasn't had its turn most recently —
        # naturally comes up next. This drives several real selections in
        # a row (feeding each result back in as seeded history, exactly as
        # poll_gdelt.py's run() would from one invocation to the next) and
        # asserts A is never chosen twice with no B in between.
        feeds = [_feed("a"), _feed("b")]  # feeds.yaml order: a before b
        histories: dict[str, list[dict]] = {"a": [], "b": []}
        min_gap = timedelta(days=7)
        chosen: list[str] = []
        now = T0
        for _ in range(6):
            selection = poll_gdelt.select_next_query(feeds, histories, now, min_gap=min_gap)
            self.assertIsNotNone(selection)
            feed_id = selection.feed["feed_id"]
            chosen.append(feed_id)
            now = now + timedelta(minutes=15)
            # A always fails; B (for this test) always fails too — the
            # property under test is about ORDER, not about either query
            # ever succeeding.
            histories[feed_id] = [_run("failed", now)] + histories[feed_id]

        self.assertEqual(chosen, ["a", "b", "a", "b", "a", "b"],
                         "ties on the first pick go to feeds.yaml order (a first); "
                         "after that, strict alternation — A must never run twice "
                         "in a row while B waits")
        for i in range(len(chosen) - 1):
            self.assertFalse(chosen[i] == "a" and chosen[i + 1] == "a",
                             "A was selected twice in a row — head-of-line blocking regressed")

    def test_a_throttled_attempt_moves_a_query_out_of_never_attempted(self) -> None:
        # A has one 'throttled' real attempt; B has never been attempted at
        # all. B must be selected — a throttled attempt still counts as
        # "this query had its turn" for ordering purposes, exactly like any
        # other real outcome (the shared cooldown gate, checked separately
        # in run(), is what actually prevents a new request soon after a
        # throttle — select_next_query itself doesn't need to know that).
        feeds = [_feed("a"), _feed("b")]
        histories = {
            "a": [_run("throttled", T0 - timedelta(hours=1))],
            "b": [],
        }
        selection = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=timedelta(days=7))
        self.assertEqual(selection.feed["feed_id"], "b")
        self.assertEqual(selection.reason, "never_attempted")

    def test_budget_exhausted_query_is_revisited_after_others_not_before(self) -> None:
        # X has never been attempted; Y's last real attempt (an 'ok'
        # outside min_gap) is older than Z's; Z was JUST budget_exhausted.
        # budget_exhausted must not jump the queue as "unfinished work" —
        # it's an ordinary real attempt like any other, so Z (most recently
        # attempted) goes LAST, after X (never attempted) and Y (staler
        # than Z).
        feeds = [_feed("x"), _feed("y"), _feed("z")]
        histories = {
            "x": [],
            "y": [_run("ok", T0 - timedelta(days=20))],
            "z": [_run("budget_exhausted", T0 - timedelta(minutes=1))],
        }
        min_gap = timedelta(days=7)

        first = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=min_gap)
        self.assertEqual(first.feed["feed_id"], "x")

        histories["x"] = [_run("failed", T0)]
        second = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=min_gap)
        self.assertEqual(second.feed["feed_id"], "y")

        histories["y"] = [_run("ok", T0)] + histories["y"]
        third = poll_gdelt.select_next_query(feeds, histories, T0, min_gap=min_gap)
        self.assertEqual(third.feed["feed_id"], "z",
                         "z (just budget_exhausted) must be revisited only after "
                         "x and y have had their turn, not before")


class FakeStore:
    """In-memory stand-in for poll_gdelt.DbStore — no Postgres, no conn."""

    def __init__(self) -> None:
        self.runs: dict[str, list[dict]] = {}
        self.captured: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict] = {}
        self.rollback_calls = 0

    def recent_runs(self, feed_id: str, limit: int) -> list[dict]:
        return self.runs.get(feed_id, [])[:limit]

    def recent_throttles(self, since: datetime) -> list[dict]:
        # Mirrors DbStore.recent_throttles: outcome='throttled' and
        # finished_at >= since, across EVERY feed_id, no cap — see that
        # method's docstring for why a LIMIT-based approach isn't safe.
        return [row for rows in self.runs.values() for row in rows
               if row["outcome"] == "throttled" and row["finished_at"] >= since]

    def record_run(self, *, feed_id, started_at, finished_at, outcome,
                   entries_seen, new_captures, error) -> None:
        row = {"feed_id": feed_id, "started_at": started_at, "finished_at": finished_at,
               "outcome": outcome, "entries_seen": entries_seen,
               "new_captures": new_captures, "error": error}
        self.runs.setdefault(feed_id, []).insert(0, row)

    def already_captured(self, feed_id: str, url: str) -> bool:
        return (feed_id, url) in self.captured

    def record_capture(self, feed_id, url, payload, ext, status, snapshot_id,
                       snapshot_url, metadata=None) -> bool:
        key = (feed_id, url)
        if key in self.captured:
            return False
        self.captured[key] = payload
        self.metadata[key] = metadata
        return True

    def rollback(self) -> None:
        self.rollback_calls += 1

    def seed_run(self, feed_id: str, outcome: str, at: datetime, **extra) -> None:
        row = _run(outcome, at, **extra)
        self.runs.setdefault(feed_id, []).insert(0, row)


class FakeFetchQuery:
    """Records call count and args; returns a canned FetchResult so no
    network is ever touched."""

    def __init__(self, status_code: int = 200, body: bytes = b'{"articles": []}') -> None:
        self.status_code = status_code
        self.body = body
        self.calls: list[dict] = []

    def __call__(self, feed: dict) -> fetcher.FetchResult:
        self.calls.append(feed)
        return fetcher.FetchResult(status_code=self.status_code, content=self.body,
                                   url="https://api.gdeltproject.org/api/v2/doc/doc",
                                   headers={}, ok=200 <= self.status_code < 300)


def _patch_config(test: unittest.TestCase, **overrides) -> None:
    originals = {name: getattr(config, name) for name in overrides}
    for name, value in overrides.items():
        setattr(config, name, value)
    test.addCleanup(lambda: [setattr(config, n, v) for n, v in originals.items()])


class RunOneRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)

    def test_one_invocation_issues_exactly_one_request(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a"), _feed("gdelt-b")]}
        store = FakeStore()
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(len(fake_fetch.calls), 1)
        self.assertEqual(outcome.requests_issued, 1)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(len(store.runs), 1, "exactly one feed_runs row this invocation")

    def test_never_run_feed_is_selected_over_one_with_recent_ok(self) -> None:
        cfg = {"feeds": [_feed("gdelt-old"), _feed("gdelt-new")]}
        store = FakeStore()
        store.seed_run("gdelt-old", "ok", T0 - timedelta(days=10))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.feed_id, "gdelt-new")
        self.assertEqual(outcome.reason, "never_attempted")
        self.assertEqual(fake_fetch.calls[0]["feed_id"], "gdelt-new")

    def test_nothing_to_do_issues_zero_requests_and_writes_no_row(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        store.seed_run("gdelt-a", "ok", T0 - timedelta(hours=1))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "nothing_to_do")
        self.assertEqual(fake_fetch.calls, [])
        self.assertEqual(store.runs.get("gdelt-a", [])[0]["outcome"], "ok",
                         "no new row written — only the seeded one remains")
        self.assertEqual(len(store.runs.get("gdelt-a", [])), 1)


class CooldownPathTests(unittest.TestCase):
    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)

    def test_cooldown_active_writes_skipped_throttled_and_issues_zero_requests(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a"), _feed("gdelt-b")]}
        store = FakeStore()
        # gdelt-b was throttled 1h ago — the shared-IP cooldown must block
        # gdelt-a's otherwise-eligible (never-run) selection too.
        store.seed_run("gdelt-b", "throttled", T0 - timedelta(hours=1))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "skipped_throttled")
        self.assertEqual(fake_fetch.calls, [], "cooldown must issue zero requests")
        written = store.runs["gdelt-a"][0]
        self.assertEqual(written["outcome"], "skipped_throttled")

    def test_cooldown_twenty_five_hours_ago_does_not_block(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        store.seed_run("gdelt-a", "throttled", T0 - timedelta(hours=25))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(len(fake_fetch.calls), 1)
        self.assertNotEqual(outcome.status, "skipped_throttled")

    def test_throttled_row_is_found_behind_150_newer_skipped_throttled_rows(self) -> None:
        # Regression test for switching the cooldown lookup from a capped
        # per-feed recent_runs(feed_id, 100) concatenation to a dedicated,
        # uncapped, time-windowed store.recent_throttles query. A LIMIT-
        # based approach on this SAME feed's own history would have missed
        # the throttled row entirely: 150 more-recent skipped_throttled
        # rows sit in front of it, comfortably exceeding the old LIMIT 100.
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        store.seed_run("gdelt-a", "throttled", T0 - timedelta(hours=1))
        for i in range(150):
            store.seed_run("gdelt-a", "skipped_throttled", T0 - timedelta(seconds=i))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "skipped_throttled")
        self.assertEqual(fake_fetch.calls, [], "the buried throttled row must still "
                                               "gate this invocation")


class DryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)

    def test_dry_run_never_issues_requests_or_writes(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, dry_run=True, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "dry_run")
        self.assertEqual(outcome.feed_id, "gdelt-a")
        self.assertEqual(outcome.reason, "never_attempted")
        self.assertEqual(fake_fetch.calls, [], "dry-run must never issue a request")
        self.assertEqual(store.runs, {}, "dry-run must never write a feed_runs row")
        self.assertEqual(outcome.new_captures, 0)
        self.assertEqual(outcome.requests_issued, 0)

    def test_dry_run_reports_would_run_even_under_cooldown(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        store.seed_run("gdelt-a", "throttled", T0 - timedelta(hours=1))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, dry_run=True, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "dry_run")
        self.assertEqual(outcome.feed_id, "gdelt-a")
        self.assertEqual(fake_fetch.calls, [])
        self.assertEqual(len(store.runs["gdelt-a"]), 1,
                         "still just the pre-seeded row — dry-run wrote nothing new")


class ThrottledResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)

    def test_throttled_body_records_throttled_outcome_not_ok(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        fake_fetch = FakeFetchQuery(status_code=200,
                                    body=b"You have used your 1 request every 5 seconds quota.")

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "throttled")
        self.assertEqual(len(fake_fetch.calls), 1, "still exactly one request — throttle is "
                                                    "discovered only after the response arrives")
        self.assertEqual(store.captured, {}, "a throttled body must never be captured as data")

    def test_unknown_body_also_records_throttled_outcome(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        fake_fetch = FakeFetchQuery(status_code=200, body=b"<html>nope</html>")

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "throttled")
        written = store.runs["gdelt-a"][0]
        self.assertEqual(written["outcome"], "throttled")
        self.assertIn("classify=unknown", written["error"])


class OkResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)

    def test_ok_response_with_zero_articles_is_a_clean_ok(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        fake_fetch = FakeFetchQuery(body=json.dumps({"articles": []}).encode())

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.entries_seen, 0)
        self.assertEqual(outcome.new_captures, 0)

    def test_articles_without_url_are_skipped_not_captured(self) -> None:
        # No network servers in this suite — an article dict with no 'url'
        # key is the one shape _capture_articles skips without fetching.
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        body = json.dumps({"articles": [{"title": "no url here"}]}).encode()
        fake_fetch = FakeFetchQuery(body=body)

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.entries_seen, 1)
        self.assertEqual(outcome.new_captures, 0)
        self.assertEqual(store.captured, {})


class RecentThrottlesSqlTextTests(unittest.TestCase):
    """Static check on DbStore.recent_throttles' own SQL text (source, not
    a live query — no Postgres): it must filter by time, not by a row-count
    LIMIT, and must not scope to one feed_id. Complements test_gdelt_sql.py
    (which checks the .sql migration files), same 'read the source, no
    database' style as test_feed_runs_sql.py.
    """

    def setUp(self) -> None:
        path = Path(__file__).resolve().parents[1] / "collector" / "poll_gdelt.py"
        self.text = path.read_text(encoding="utf-8")
        method_match = re.search(
            r"def recent_throttles\(self.*?\n(?=    def |\Z)", self.text, re.DOTALL)
        self.assertIsNotNone(method_match, "DbStore.recent_throttles not found")
        self.method_text = method_match.group(0)
        # The actual SQL string only — cur.execute("""...""") — excluding
        # the method's own docstring, which discusses LIMIT/feed_id in
        # prose and would otherwise false-positive the checks below.
        sql_match = re.search(r'cur\.execute\(\s*"""(.*?)"""', self.method_text, re.DOTALL)
        self.assertIsNotNone(sql_match, "no cur.execute(...) SQL string found")
        self.sql_text = sql_match.group(1)

    def test_has_a_time_predicate(self) -> None:
        self.assertRegex(self.sql_text, r"finished_at\s*>=\s*%s")

    def test_filters_on_throttled_outcome(self) -> None:
        self.assertIn("outcome = 'throttled'", self.sql_text)

    def test_no_limit_clause(self) -> None:
        self.assertNotRegex(self.sql_text, r"(?i)\bLIMIT\b")

    def test_not_scoped_to_a_single_feed_id(self) -> None:
        self.assertNotIn("feed_id", self.sql_text)


class NotifyDecisionTests(unittest.TestCase):
    """Pure tests for gdelt_notify_decision — gated on state CHANGE, not on
    every real attempt (up to ~16 alerts/night otherwise)."""

    def test_throttled_always_notifies_regardless_of_previous(self) -> None:
        for previous in (None, "ok", "failed", "timeout", "throttled", "budget_exhausted"):
            with self.subTest(previous=previous):
                decision = poll_gdelt.gdelt_notify_decision(previous, "throttled")
                self.assertIsNotNone(decision)
                title, priority = decision
                self.assertEqual(title, "Collector: GDELT throttled")
                self.assertEqual(priority, "high")

    def test_failed_after_ok_notifies(self) -> None:
        decision = poll_gdelt.gdelt_notify_decision("ok", "failed")
        self.assertIsNotNone(decision)
        self.assertEqual(decision[0], "Collector: GDELT poll failed")
        self.assertEqual(decision[1], "high")

    def test_failed_after_never_attempted_notifies(self) -> None:
        decision = poll_gdelt.gdelt_notify_decision(None, "failed")
        self.assertIsNotNone(decision)

    def test_failed_after_failed_does_not_notify(self) -> None:
        self.assertIsNone(poll_gdelt.gdelt_notify_decision("failed", "failed"))

    def test_failed_after_timeout_does_not_notify(self) -> None:
        # Both are the same "a real attempt didn't work" bucket — either
        # one as the PREVIOUS outcome suppresses a new failed/timeout alert.
        self.assertIsNone(poll_gdelt.gdelt_notify_decision("timeout", "failed"))

    def test_timeout_after_timeout_does_not_notify(self) -> None:
        self.assertIsNone(poll_gdelt.gdelt_notify_decision("timeout", "timeout"))

    def test_failed_after_throttled_notifies(self) -> None:
        # throttled is not in the failed/timeout suppression bucket.
        decision = poll_gdelt.gdelt_notify_decision("throttled", "failed")
        self.assertIsNotNone(decision)

    def test_ok_after_failed_notifies_recovered_at_default_priority(self) -> None:
        decision = poll_gdelt.gdelt_notify_decision("failed", "ok")
        self.assertIsNotNone(decision)
        self.assertEqual(decision[0], "Collector: GDELT recovered")
        self.assertEqual(decision[1], "default")

    def test_ok_after_throttled_notifies_recovered(self) -> None:
        decision = poll_gdelt.gdelt_notify_decision("throttled", "ok")
        self.assertIsNotNone(decision)
        self.assertEqual(decision[0], "Collector: GDELT recovered")

    def test_budget_exhausted_after_timeout_notifies_recovered(self) -> None:
        decision = poll_gdelt.gdelt_notify_decision("timeout", "budget_exhausted")
        self.assertIsNotNone(decision)
        self.assertEqual(decision[0], "Collector: GDELT recovered")

    def test_ok_after_ok_does_not_notify(self) -> None:
        self.assertIsNone(poll_gdelt.gdelt_notify_decision("ok", "ok"))

    def test_budget_exhausted_after_budget_exhausted_does_not_notify(self) -> None:
        self.assertIsNone(
            poll_gdelt.gdelt_notify_decision("budget_exhausted", "budget_exhausted"))

    def test_ok_after_never_attempted_does_not_notify(self) -> None:
        # The very first attempt ever succeeding isn't a "recovery" — there
        # was nothing to recover from.
        self.assertIsNone(poll_gdelt.gdelt_notify_decision(None, "ok"))

    def test_non_real_attempt_outcomes_never_notify(self) -> None:
        for current in ("nothing_to_do", "skipped_throttled", "dry_run"):
            with self.subTest(current=current):
                for previous in (None, "failed", "throttled"):
                    self.assertIsNone(poll_gdelt.gdelt_notify_decision(previous, current))


class PreviousOutcomeTests(unittest.TestCase):
    """run()'s Outcome.previous_outcome must reflect the most recent real
    attempt across ALL kind: gdelt feeds, computed from state as of BEFORE
    this invocation's own row is written."""

    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)

    def test_previous_outcome_is_none_on_a_truly_fresh_pipeline(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertIsNone(outcome.previous_outcome)

    def test_previous_outcome_looks_across_every_gdelt_feed_not_just_the_selected_one(
            self) -> None:
        # gdelt-b's failure is the most recent real attempt anywhere, even
        # though gdelt-a (never attempted) is the one selected this round.
        cfg = {"feeds": [_feed("gdelt-a"), _feed("gdelt-b")]}
        store = FakeStore()
        store.seed_run("gdelt-b", "failed", T0 - timedelta(minutes=5))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.feed_id, "gdelt-a")
        self.assertEqual(outcome.previous_outcome, "failed")

    def test_previous_outcome_ignores_skipped_throttled_rows(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a")]}
        store = FakeStore()
        store.seed_run("gdelt-a", "skipped_throttled", T0 - timedelta(minutes=1))
        store.seed_run("gdelt-a", "ok", T0 - timedelta(days=10))
        fake_fetch = FakeFetchQuery()

        outcome = poll_gdelt.run(cfg, store, now=T0, fetch_query=fake_fetch)

        self.assertEqual(outcome.previous_outcome, "ok")


class RealFetchPathTests(unittest.TestCase):
    """The one test in this file that does NOT inject fetch_query — every
    other test proves poll_gdelt.py's own logic without ever touching a
    socket, which also means the REAL default fetch_query (fetcher.get
    against GDELT_ENDPOINT) has never actually executed. This drives run()
    with the default against a local stub server (tests/servers.py) and
    inspects the exact query string the real path sent."""

    def setUp(self) -> None:
        _patch_config(self, GDELT_BUDGET=300, WAYBACK_ENABLED=False,
                      GDELT_QUERY_MIN_GAP_DAYS=7, GDELT_COOLDOWN_H=24)
        self.server = servers.gdelt_stub_server()
        self.addCleanup(self.server.stop)
        self._original_endpoint = poll_gdelt.GDELT_ENDPOINT
        poll_gdelt.GDELT_ENDPOINT = self.server.url
        self.addCleanup(lambda: setattr(poll_gdelt, "GDELT_ENDPOINT", self._original_endpoint))

    def test_default_fetch_query_sends_the_expected_query_string(self) -> None:
        cfg = {"feeds": [_feed("gdelt-a", query="Viettel sourcecountry:VM",
                               timespan="7d", maxrecords=25)]}
        store = FakeStore()

        outcome = poll_gdelt.run(cfg, store, now=T0)   # no fetch_query injected

        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.requests_issued, 1)
        self.assertIsNotNone(self.server.last_path, "the stub server never saw a request")
        parsed = urllib.parse.urlparse(self.server.last_path)
        query_params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query_params["query"], ["Viettel sourcecountry:VM"])
        self.assertEqual(query_params["mode"], ["artlist"])
        self.assertEqual(query_params["format"], ["json"])
        self.assertEqual(query_params["timespan"], ["7d"])
        self.assertEqual(query_params["maxrecords"], ["25"])


if __name__ == "__main__":
    unittest.main()
