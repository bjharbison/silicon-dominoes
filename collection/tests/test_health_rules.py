"""Tests a-g from the harden-health spec: collector.health_rules is pure
logic — no I/O, no servers, no store. History rows are just dicts with
'outcome', 'finished_at', 'new_captures'.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import health_rules  # noqa: E402

T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)   # a Monday, 09:00 UTC
THRESHOLD = 5
POLL_STALE_H = 6.0


def _row(outcome: str, at: datetime, new_captures: int = 1) -> dict:
    return {"outcome": outcome, "finished_at": at, "new_captures": new_captures}


def _problems(**kwargs) -> list:
    """Most of this file only cares about evaluate()'s confirmed problems,
    not which origins were undetermined — that half of the contract gets
    its own dedicated tests (UndeterminedEvidenceTests) using
    health_rules.evaluate() directly."""
    return health_rules.evaluate(**kwargs).problems


class CollectorDownAndPollStaleTests(unittest.TestCase):
    def test_all_feeds_stale_is_one_collector_down_zero_poll_stale(self) -> None:
        rss_feeds = [{"feed_id": "f1"}, {"feed_id": "f2"}]
        histories = {
            "f1": [_row("ok", T0 - timedelta(hours=7))],
            "f2": [_row("ok", T0 - timedelta(hours=7))],
        }
        problems = _problems(
            rss_feeds=rss_feeds, histories=histories, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=7), "f2": T0 - timedelta(hours=7)},
            now=T0, poll_stale_h=POLL_STALE_H, breaker_threshold=THRESHOLD)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].origin, "collector_down")
        self.assertIsNone(problems[0].feed_id)

    def test_one_stale_feed_among_fresh_ones_is_a_single_poll_stale(self) -> None:
        rss_feeds = [{"feed_id": "f1"}, {"feed_id": "f2"}]
        histories = {
            "f1": [_row("ok", T0 - timedelta(hours=8))],           # stale, but healthy history
            "f2": [_row("ok", T0 - timedelta(hours=1))],           # fresh
        }
        problems = _problems(
            rss_feeds=rss_feeds, histories=histories, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=8), "f2": T0 - timedelta(hours=1)},
            now=T0, poll_stale_h=POLL_STALE_H, breaker_threshold=THRESHOLD)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].origin, "poll_stale:f1")
        self.assertEqual(problems[0].feed_id, "f1")

    def test_quarantined_feed_with_fresh_skips_is_dead_feed_not_poll_stale(self) -> None:
        history = [
            _row("skipped_quarantined", T0 - timedelta(minutes=5), 0),
            _row("skipped_quarantined", T0 - timedelta(minutes=15), 0),
            _row("failed", T0 - timedelta(minutes=25), 0),
            _row("failed", T0 - timedelta(minutes=35), 0),
            _row("failed", T0 - timedelta(minutes=45), 0),
            _row("failed", T0 - timedelta(minutes=55), 0),
            _row("failed", T0 - timedelta(minutes=65), 0),
        ]
        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": None}, now=T0,
            poll_stale_h=POLL_STALE_H, breaker_threshold=THRESHOLD)

        origins = [p.origin for p in problems]
        self.assertIn("dead_feed:f1", origins)
        self.assertNotIn("poll_stale:f1", origins)
        self.assertNotIn("collector_down", origins)


class VerifyItemAndLedgerUnavailableTests(unittest.TestCase):
    def test_verify_item_never_verified(self) -> None:
        problems = _problems(
            rss_feeds=[], histories={}, verify_items=[{"feed_id": "v1", "max_gap_days": 8}],
            latest_captures={"v1": None}, now=T0)
        self.assertEqual([p.origin for p in problems], ["dead_feed:v1"])

    def test_verify_item_stale_past_max_gap_days(self) -> None:
        problems = _problems(
            rss_feeds=[], histories={}, verify_items=[{"feed_id": "v1", "max_gap_days": 8}],
            latest_captures={"v1": T0 - timedelta(days=9)}, now=T0)
        self.assertEqual([p.origin for p in problems], ["dead_feed:v1"])

    def test_verify_item_within_max_gap_days_is_fine(self) -> None:
        problems = _problems(
            rss_feeds=[], histories={}, verify_items=[{"feed_id": "v1", "max_gap_days": 8}],
            latest_captures={"v1": T0 - timedelta(days=2)}, now=T0)
        self.assertEqual(problems, [])

    def test_histories_none_skips_ledger_rules_but_still_runs_verify_items(self) -> None:
        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}], histories=None,
            verify_items=[{"feed_id": "v1", "max_gap_days": 8}],
            latest_captures={"f1": None, "v1": None}, now=T0)
        origins = [p.origin for p in problems]
        self.assertNotIn("collector_down", origins)
        self.assertFalse(any(o.startswith("poll_stale:") or o.startswith("dead_feed:f1")
                            for o in origins))
        self.assertIn("dead_feed:v1", origins)


def _weekly_history(now: datetime, first_seen: datetime, end: datetime,
                    per_day, skip_weekends: bool = False) -> list[dict]:
    """Builds history rows one per day from first_seen up to (not including)
    end, `per_day` captures each (callable(day_index)->int or a constant),
    most-recent-first. Each day's row lands at the same time-of-day as
    first_seen so week-boundary math stays exact."""
    rows = []
    day = first_seen
    i = 0
    while day < end:
        count = per_day(i) if callable(per_day) else per_day
        if not (skip_weekends and day.weekday() >= 5):
            rows.append(_row("ok", day, count))
        day += timedelta(days=1)
        i += 1
    rows.sort(key=lambda r: r["finished_at"], reverse=True)
    return rows


class LowVolumeTests(unittest.TestCase):
    def test_weekend_only_feed_monday_morning_no_low_volume(self) -> None:
        # T0 is a Monday 09:00 UTC. 6 weeks of history, ~2/weekday, 0/weekend.
        first_seen = T0 - timedelta(days=42)
        history = _weekly_history(T0, first_seen, T0, per_day=2, skip_weekends=True)
        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=1)}, now=T0)
        self.assertEqual([p.origin for p in problems if p.origin.startswith("low_volume")], [])

    def test_first_fill_burst_then_steady_no_low_volume(self) -> None:
        # Feed is 30 days old. A 30-capture burst in the first 48h, then a
        # steady ~1.5/day for the rest — evaluated now, the burst must not
        # inflate the baseline (it's excluded) and must not make the
        # (unrelated, steady) current week look collapsed by comparison.
        first_seen = T0 - timedelta(days=30)
        history = [_row("ok", first_seen + timedelta(hours=1), 30)]
        day = first_seen + timedelta(hours=3)
        while day < T0:
            history.append(_row("ok", day, 2))
            day += timedelta(days=1)
        history.sort(key=lambda r: r["finished_at"], reverse=True)

        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=1)}, now=T0)
        self.assertEqual([p.origin for p in problems if p.origin.startswith("low_volume")], [])

    def test_genuine_collapse_after_four_normal_weeks_fires_low_volume(self) -> None:
        # 4 normal weeks at ~11/week (first_seen well before that, so the
        # first-48h exclusion doesn't touch any of them — clearly above
        # LOW_VOLUME_MIN_MEDIAN=5), then the last 7 days produce almost
        # nothing. A fresh row near `now` keeps this feed's OWN poll-
        # staleness out of the picture; "control" is a second, always-
        # healthy feed so a single stale/near-empty feed can never be
        # mistaken for collector_down (which needs ALL feeds stale).
        first_seen = T0 - timedelta(days=60)
        history = []
        day = first_seen
        while day < T0 - timedelta(days=7):
            history.append(_row("ok", day, 1 if day.weekday() < 5 else 3))  # 5*1+2*3=11/week
            day += timedelta(days=1)
        history.append(_row("ok", T0 - timedelta(hours=1), 1))   # last 7 days: near-empty
        history.sort(key=lambda r: r["finished_at"], reverse=True)
        control = [_row("ok", T0 - timedelta(hours=1), 10)]

        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}, {"feed_id": "control"}],
            histories={"f1": history, "control": control}, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=1), "control": T0 - timedelta(hours=1)},
            now=T0)
        low_volume = [p for p in problems if p.origin == "low_volume:f1"]
        self.assertEqual(len(low_volume), 1, f"expected low_volume to fire; problems={problems}")

    def test_low_volume_skipped_when_feed_is_poll_stale(self) -> None:
        first_seen = T0 - timedelta(days=60)
        history = []
        day = first_seen
        while day < T0 - timedelta(hours=8):
            history.append(_row("ok", day, 10))
            day += timedelta(days=1)
        history.sort(key=lambda r: r["finished_at"], reverse=True)   # newest is 8h+ old -> stale
        control = [_row("ok", T0 - timedelta(hours=1), 10)]          # keeps this from being collector_down

        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}, {"feed_id": "control"}],
            histories={"f1": history, "control": control}, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=8), "control": T0 - timedelta(hours=1)},
            now=T0)
        self.assertEqual([p.origin for p in problems], ["poll_stale:f1"])

    def test_thin_baseline_below_min_median_does_not_fire(self) -> None:
        first_seen = T0 - timedelta(days=40)
        history = []
        day = first_seen
        while day < T0:
            history.append(_row("ok", day, 0))     # essentially nothing, ever
            day += timedelta(days=1)
        history.sort(key=lambda r: r["finished_at"], reverse=True)

        problems = _problems(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": None}, now=T0)
        self.assertEqual([p.origin for p in problems if p.origin.startswith("low_volume")], [])


class ReconcileTests(unittest.TestCase):
    def test_idempotent_second_run_opens_and_closes_nothing(self) -> None:
        gaps = [{"gap_id": 1, "origin": "dead_feed:f1", "opened_at": T0}]
        asserted = [health_rules.Problem("dead_feed:f1", "f1", "still dead", "high")]
        to_open, to_close = health_rules.reconcile(gaps, asserted)
        self.assertEqual(to_open, [])
        self.assertEqual(to_close, [])

    def test_condition_clears_then_returns_opens_a_new_gap(self) -> None:
        # Run 1: asserted, nothing open yet -> opens.
        to_open, to_close = health_rules.reconcile(
            [], [health_rules.Problem("dead_feed:f1", "f1", "dead", "high")])
        self.assertEqual(len(to_open), 1)
        self.assertEqual(to_close, [])

        # Run 2: condition cleared -> the (now-open) gap closes.
        open_gaps = [{"gap_id": 1, "origin": "dead_feed:f1", "opened_at": T0}]
        to_open2, to_close2 = health_rules.reconcile(open_gaps, [])
        self.assertEqual(to_open2, [])
        self.assertEqual(len(to_close2), 1)

        # Run 3: condition returns, but the old gap is closed (not in the
        # open set passed in) -> a brand new gap opens, not a reopen.
        to_open3, to_close3 = health_rules.reconcile(
            [], [health_rules.Problem("dead_feed:f1", "f1", "dead again", "high")])
        self.assertEqual(len(to_open3), 1)
        self.assertEqual(to_close3, [])

    def test_unmanaged_gaps_are_never_touched(self) -> None:
        # An analyst-opened gap (origin='analyst') is simply never passed
        # to reconcile — is_managed_origin is what the caller uses to build
        # open_managed_gaps in the first place.
        self.assertFalse(health_rules.is_managed_origin("analyst"))
        self.assertFalse(health_rules.is_managed_origin("dead_feed"))  # no colon, not managed
        self.assertTrue(health_rules.is_managed_origin("dead_feed:x"))
        self.assertTrue(health_rules.is_managed_origin("poll_stale:x"))
        self.assertTrue(health_rules.is_managed_origin("low_volume:x"))
        self.assertTrue(health_rules.is_managed_origin("collector_down"))

    def test_status_md_legacy_gaps_scenario(self) -> None:
        """The exact situation STATUS.md documents: false dead_feed gaps on
        feeds that are healthy again, gaps belonging to now-retired feeds,
        and two verify-item gaps that are still genuinely failing."""
        open_gaps = [
            {"gap_id": 1, "origin": "dead_feed:rss-datacenterdynamics", "opened_at": T0},  # now healthy
            {"gap_id": 2, "origin": "dead_feed:rss-lightreading", "opened_at": T0},        # now healthy
            {"gap_id": 3, "origin": "dead_feed:rss-techwireasia", "opened_at": T0},        # retired
            {"gap_id": 7, "origin": "dead_feed:rss-imda-sg", "opened_at": T0},             # retired
            {"gap_id": 9, "origin": "dead_feed:rss-mic-vn", "opened_at": T0},              # retired
            {"gap_id": 4, "origin": "dead_feed:verify-waico", "opened_at": T0},            # still failing
            {"gap_id": 5, "origin": "dead_feed:verify-pax-silica", "opened_at": T0},       # still failing
            {"gap_id": 42, "origin": "analyst", "opened_at": T0},                          # unmanaged
        ]
        # This run's evaluate() only ever sees ACTIVE feeds (the retired
        # ones are simply absent from rss_feeds/verify_items, per spec
        # point 4), so their origins never appear in `asserted` regardless.
        asserted = [
            health_rules.Problem("dead_feed:verify-waico", "verify-waico",
                                 "never verified", "high"),
            health_rules.Problem("dead_feed:verify-pax-silica", "verify-pax-silica",
                                 "never verified", "high"),
        ]
        managed_open = [g for g in open_gaps if health_rules.is_managed_origin(g["origin"])]
        to_open, to_close = health_rules.reconcile(managed_open, asserted)

        self.assertEqual(to_open, [])   # both verify gaps already open, nothing new
        closed_ids = {g["gap_id"] for g in to_close}
        self.assertEqual(closed_ids, {1, 2, 3, 7, 9})
        # The unmanaged analyst gap was never in managed_open, so it can't
        # appear in to_close — untouched by construction.
        self.assertNotIn(42, closed_ids)


class RenotifyTests(unittest.TestCase):
    def test_no_renotify_before_the_interval_elapses(self) -> None:
        opened_at = T0 - timedelta(hours=2)
        self.assertFalse(health_rules.should_renotify_collector_down(opened_at, T0, 6.0))

    def test_renotify_once_interval_elapses(self) -> None:
        opened_at = T0 - timedelta(hours=6)
        self.assertTrue(health_rules.should_renotify_collector_down(opened_at, T0, 6.0))

    def test_no_renotify_partway_through_a_later_window(self) -> None:
        opened_at = T0 - timedelta(hours=9)   # 9h in: past one 6h window, not yet at the next
        self.assertFalse(health_rules.should_renotify_collector_down(opened_at, T0, 6.0))

    def test_feed_id_from_origin(self) -> None:
        self.assertEqual(health_rules.feed_id_from_origin("dead_feed:rss-x"), "rss-x")
        self.assertEqual(health_rules.feed_id_from_origin("poll_stale:rss-y"), "rss-y")
        self.assertEqual(health_rules.feed_id_from_origin("low_volume:rss-z"), "rss-z")
        self.assertIsNone(health_rules.feed_id_from_origin("collector_down"))
        self.assertIsNone(health_rules.feed_id_from_origin("analyst"))

    def test_no_renotify_in_the_first_window_after_opening(self) -> None:
        # Regression: elapsed % renotify_h < 1.0 is trivially true for ANY
        # elapsed_h below 1.0 (e.g. 30 minutes after opening), because a
        # small number modulo anything is itself — that fired a renotify on
        # the very first opportunity after opening rather than waiting a
        # full renotify_h. Must stay False until at least renotify_h has
        # elapsed, no matter how the hourly timer's jitter lines up.
        opened_at = T0 - timedelta(minutes=30)
        self.assertFalse(health_rules.should_renotify_collector_down(opened_at, T0, 6.0))

        opened_at = T0 - timedelta(minutes=50)   # a plausible "next run" under jitter
        self.assertFalse(health_rules.should_renotify_collector_down(opened_at, T0, 6.0))


class UndeterminedEvidenceTests(unittest.TestCase):
    """Test 1(a-c) from the harden-health follow-up: a failed read must
    never be represented by the same value as a confirmed 'no' or 'never'.
    """

    def test_a_ledger_unreadable_keeps_open_collector_down_gap_open(self) -> None:
        open_gaps = [{"gap_id": 1, "origin": "collector_down", "opened_at": T0}]
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}, {"feed_id": "f2"}], histories=None,
            verify_items=[], latest_captures={"f1": None, "f2": None}, now=T0)

        self.assertEqual(evaluation.problems, [])
        self.assertIn("collector_down", evaluation.undetermined)

        to_open, to_close = health_rules.reconcile(
            open_gaps, evaluation.problems, evaluation.undetermined)
        self.assertEqual(to_open, [])
        self.assertEqual(to_close, [], "an unreadable ledger must not close an "
                                      "existing collector_down gap")

    def test_b_one_feeds_raw_captures_failure_does_not_taint_other_feeds(self) -> None:
        # f1: healthy history, but its raw_captures read failed this run.
        # f2: healthy history AND a successful raw_captures read — must be
        # evaluated completely normally regardless of f1's failure.
        history_f1 = [_row("ok", T0 - timedelta(hours=1), 3)]
        history_f2 = [_row("ok", T0 - timedelta(hours=1), 3)]
        open_gaps = [{"gap_id": 5, "origin": "dead_feed:f1", "opened_at": T0}]

        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}, {"feed_id": "f2"}],
            histories={"f1": history_f1, "f2": history_f2}, verify_items=[],
            latest_captures={"f1": None, "f2": T0 - timedelta(hours=1)},
            unreadable_captures=frozenset({"f1"}), now=T0)

        self.assertIn("dead_feed:f1", evaluation.undetermined)
        self.assertNotIn("dead_feed:f2", evaluation.undetermined)
        self.assertEqual([p for p in evaluation.problems if p.origin == "dead_feed:f2"], [],
                         "f2 has real evidence and is healthy — must not be flagged")

        to_open, to_close = health_rules.reconcile(
            open_gaps, evaluation.problems, evaluation.undetermined)
        self.assertEqual(to_open, [])
        self.assertEqual(to_close, [], "f1's existing gap must not close on a failed read")

    def test_c_all_evidence_readable_behaves_normally(self) -> None:
        # Sanity: with nothing unreadable, a genuinely dead feed still opens
        # and a genuinely healthy one still doesn't.
        dead_history = [_row("failed", T0 - timedelta(minutes=10 * i)) for i in range(5)]
        healthy_history = [_row("ok", T0 - timedelta(hours=1), 3)]

        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "dead"}, {"feed_id": "healthy"}],
            histories={"dead": dead_history, "healthy": healthy_history}, verify_items=[],
            latest_captures={"dead": None, "healthy": T0 - timedelta(hours=1)}, now=T0)

        self.assertEqual(evaluation.undetermined, frozenset())
        origins = [p.origin for p in evaluation.problems]
        self.assertIn("dead_feed:dead", origins)
        self.assertNotIn("dead_feed:healthy", origins)


class UnreadableCapturesAndLowVolumeTests(unittest.TestCase):
    """Test 11(a-c) from the harden-health follow-up 3: low_volume is
    derived from feed_runs alone, so a raw_captures read failure must never
    block it — only dead_feed's "no captures ever" half actually needs
    latest_captures. Before the fix, `continue` on unreadable_captures
    skipped low_volume entirely, so an open low_volume:<id> gap silently
    fell out of `asserted` and reconcile() closed it as 'cleared' — the
    same class of bug as follow-up item 1, just for a different origin."""

    def test_a_still_low_volume_stays_open_via_reassertion_not_freeze(self) -> None:
        # Same "genuine collapse" shape as LowVolumeTests: 4 normal ~11/week
        # baseline weeks, then a near-empty last 7 days — except this run
        # f1's raw_captures read failed. low_volume must still fire (it
        # only needs `history`), so the existing gap gets RE-ASSERTED, not
        # frozen as undetermined.
        first_seen = T0 - timedelta(days=60)
        history = []
        day = first_seen
        while day < T0 - timedelta(days=7):
            history.append(_row("ok", day, 1 if day.weekday() < 5 else 3))  # 11/week
            day += timedelta(days=1)
        history.append(_row("ok", T0 - timedelta(hours=1), 1))   # last 7 days: near-empty
        history.sort(key=lambda r: r["finished_at"], reverse=True)
        control = [_row("ok", T0 - timedelta(hours=1), 10)]
        open_gaps = [{"gap_id": 1, "origin": "low_volume:f1", "opened_at": T0}]

        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}, {"feed_id": "control"}],
            histories={"f1": history, "control": control}, verify_items=[],
            latest_captures={"f1": None, "control": T0 - timedelta(hours=1)},
            unreadable_captures=frozenset({"f1"}), now=T0)

        self.assertIn("low_volume:f1", [p.origin for p in evaluation.problems],
                      "low_volume must still be asserted — it doesn't need raw_captures")
        self.assertNotIn("low_volume:f1", evaluation.undetermined)

        to_open, to_close = health_rules.reconcile(
            open_gaps, evaluation.problems, evaluation.undetermined)
        self.assertEqual(to_open, [], "already open — reasserting isn't a new open")
        self.assertEqual(to_close, [], "must stay open via assertion, not freeze")

    def test_b_breaker_confirmed_dead_asserts_regardless_of_unreadable_captures(self) -> None:
        history = [_row("failed", T0 - timedelta(minutes=10 * i)) for i in range(5)]
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": None}, unreadable_captures=frozenset({"f1"}), now=T0)

        self.assertIn("dead_feed:f1", [p.origin for p in evaluation.problems],
                      "the ledger alone proves 5 consecutive failures — captures "
                      "being unreadable must not block this")
        self.assertNotIn("dead_feed:f1", evaluation.undetermined)

    def test_c_unreadable_captures_with_healthy_ledger_is_undetermined_only(self) -> None:
        history = [_row("ok", T0 - timedelta(hours=i), 5) for i in range(1, 5)]
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": None}, unreadable_captures=frozenset({"f1"}), now=T0)

        self.assertEqual(evaluation.problems, [])
        self.assertIn("dead_feed:f1", evaluation.undetermined)
        self.assertNotIn("low_volume:f1", evaluation.undetermined,
                         "low_volume was actually evaluated (and found healthy), "
                         "not frozen — it never needed raw_captures at all")

        to_open, to_close = health_rules.reconcile([], evaluation.problems,
                                                    evaluation.undetermined)
        self.assertEqual(to_open, [])
        self.assertEqual(to_close, [])


class NoCapturesEverTests(unittest.TestCase):
    """Test 3 from the harden-health follow-up: a dedicated test for the
    RSS 'no captures ever' -> dead_feed rule, previously only incidentally
    covered."""

    def test_polled_successfully_but_never_captured_anything_is_dead_feed(self) -> None:
        # Feed responds 'ok' every time (no breaker failures at all — this
        # is NOT the consecutive-failure path) but has never produced a
        # single new capture, ever. That's its own distinct failure mode.
        history = [_row("ok", T0 - timedelta(hours=i), 0) for i in range(1, 10)]
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": None}, now=T0)

        self.assertEqual(evaluation.undetermined, frozenset())
        matches = [p for p in evaluation.problems if p.origin == "dead_feed:f1"]
        self.assertEqual(len(matches), 1)
        self.assertIn("no captures ever", matches[0].description)

    def test_never_polled_at_all_is_not_this_rule(self) -> None:
        # Empty history -> poll_stale, not dead_feed (no captures ever
        # requires "history and latest_captures is None" — a feed that has
        # never even been polled is a staleness problem, not this one).
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}, {"feed_id": "f2"}],
            histories={"f1": [], "f2": [_row("ok", T0 - timedelta(hours=1), 3)]},
            verify_items=[], latest_captures={"f1": None, "f2": T0 - timedelta(hours=1)},
            now=T0)
        origins = [p.origin for p in evaluation.problems]
        self.assertIn("poll_stale:f1", origins)
        self.assertNotIn("dead_feed:f1", origins)

    def test_captures_exist_is_not_dead_feed(self) -> None:
        history = [_row("ok", T0 - timedelta(hours=i), 1) for i in range(1, 5)]
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": history}, verify_items=[],
            latest_captures={"f1": T0 - timedelta(hours=1)}, now=T0)
        self.assertEqual([p for p in evaluation.problems if p.origin == "dead_feed:f1"], [])


class PerFeedFreezeTests(unittest.TestCase):
    """Test 7(a-c) from the harden-health follow-up: not being able to see
    a feed (collector_down, or that one feed being poll_stale) is not
    evidence its own dead_feed/low_volume problem went away."""

    def test_a_collector_down_freezes_existing_dead_feed_gap(self) -> None:
        # Both feeds 7h-stale (poll_stale_h default 6h) -> collector_down.
        histories = {
            "x": [_row("ok", T0 - timedelta(hours=7))],
            "y": [_row("ok", T0 - timedelta(hours=7))],
        }
        open_gaps = [{"gap_id": 1, "origin": "dead_feed:x", "opened_at": T0}]

        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "x"}, {"feed_id": "y"}], histories=histories,
            verify_items=[], latest_captures={"x": None, "y": None}, now=T0)

        self.assertIn("collector_down", [p.origin for p in evaluation.problems])
        self.assertIn("dead_feed:x", evaluation.undetermined)

        to_open, to_close = health_rules.reconcile(
            open_gaps, evaluation.problems, evaluation.undetermined)
        self.assertEqual([p.origin for p in to_open], ["collector_down"])
        self.assertEqual(to_close, [], "dead_feed:x must stay open — collector_down "
                                      "is not evidence x itself recovered")

    def test_b_poll_stale_feed_freezes_its_own_low_volume_gap(self) -> None:
        # x is 8h-stale; y is fresh, so this is poll_stale, not collector_down.
        histories = {
            "x": [_row("ok", T0 - timedelta(hours=8), 10)],
            "y": [_row("ok", T0 - timedelta(hours=1), 10)],
        }
        open_gaps = [{"gap_id": 2, "origin": "low_volume:x", "opened_at": T0}]

        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "x"}, {"feed_id": "y"}], histories=histories,
            verify_items=[], latest_captures={"x": T0 - timedelta(hours=8),
                                             "y": T0 - timedelta(hours=1)}, now=T0)

        self.assertIn("poll_stale:x", [p.origin for p in evaluation.problems])
        self.assertIn("low_volume:x", evaluation.undetermined)

        to_open, to_close = health_rules.reconcile(
            open_gaps, evaluation.problems, evaluation.undetermined)
        self.assertEqual([p.origin for p in to_open], ["poll_stale:x"])
        self.assertEqual(to_close, [])

    def test_c_recovery_closes_the_frozen_gap(self) -> None:
        # Same setup as (a), but the collector is healthy again and x is
        # confirmed healthy: dead_feed:x must close now that we can see it.
        open_gaps = [{"gap_id": 1, "origin": "dead_feed:x", "opened_at": T0}]
        histories = {
            "x": [_row("ok", T0 - timedelta(hours=1), 5)],
            "y": [_row("ok", T0 - timedelta(hours=1), 5)],
        }
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "x"}, {"feed_id": "y"}], histories=histories,
            verify_items=[], latest_captures={"x": T0 - timedelta(hours=1),
                                             "y": T0 - timedelta(hours=1)}, now=T0)

        self.assertEqual(evaluation.undetermined, frozenset())
        to_open, to_close = health_rules.reconcile(
            open_gaps, evaluation.problems, evaluation.undetermined)
        self.assertEqual([g["gap_id"] for g in to_close], [1])

    def test_verify_item_dead_feed_is_unaffected_by_collector_down(self) -> None:
        # All RSS feeds stale (collector_down), but the verify item's
        # dead_feed check doesn't depend on feed_runs at all.
        histories = {"x": [_row("ok", T0 - timedelta(hours=7))]}
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "x"}], histories=histories,
            verify_items=[{"feed_id": "v1", "max_gap_days": 8}],
            latest_captures={"x": None, "v1": None}, now=T0)

        self.assertIn("collector_down", [p.origin for p in evaluation.problems])
        self.assertIn("dead_feed:v1", [p.origin for p in evaluation.problems])
        self.assertNotIn("dead_feed:v1", evaluation.undetermined)


class NewFeedGraceTests(unittest.TestCase):
    """Test 9 from the harden-health follow-up: a brand-new feed with no
    feed_runs rows yet is undetermined, not poll_stale, until it has existed
    longer than poll_stale_h. Uses feeds.created_at (db/schema.sql: `feeds`
    has `created_at timestamptz NOT NULL DEFAULT now()`, unaffected by
    sync_feeds' ON CONFLICT upsert — see report)."""

    def test_brand_new_feed_with_no_history_is_undetermined_not_stale(self) -> None:
        created_at = T0 - timedelta(hours=1)     # well within poll_stale_h=6
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1", "created_at": created_at}],
            histories={"f1": []}, verify_items=[], latest_captures={"f1": None}, now=T0)

        self.assertEqual(evaluation.problems, [])
        self.assertIn("poll_stale:f1", evaluation.undetermined)
        self.assertIn("dead_feed:f1", evaluation.undetermined)
        self.assertIn("low_volume:f1", evaluation.undetermined)

    def test_lone_new_feed_does_not_trigger_collector_down(self) -> None:
        created_at = T0 - timedelta(hours=1)
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1", "created_at": created_at}],
            histories={"f1": []}, verify_items=[], latest_captures={"f1": None}, now=T0)
        self.assertNotIn("collector_down",
                         [p.origin for p in evaluation.problems] + list(evaluation.undetermined))

    def test_grace_period_expires_into_poll_stale(self) -> None:
        created_at = T0 - timedelta(hours=7)     # older than poll_stale_h=6, still no history
        # A fresh "control" feed alongside it: without one, f1 being the
        # only (and therefore only judgeable) feed makes it collector_down
        # by definition — this test is specifically about the individual
        # poll_stale verdict, so keep the other signal out of the way.
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1", "created_at": created_at}, {"feed_id": "control"}],
            histories={"f1": [], "control": [_row("ok", T0 - timedelta(hours=1), 5)]},
            verify_items=[], latest_captures={"f1": None, "control": T0 - timedelta(hours=1)},
            now=T0)

        self.assertIn("poll_stale:f1", [p.origin for p in evaluation.problems])
        self.assertNotIn("poll_stale:f1", evaluation.undetermined)

    def test_missing_created_at_falls_back_to_old_behavior(self) -> None:
        # No created_at supplied at all (e.g. an older caller) -> a
        # no-history feed is immediately stale, same as before this fix.
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "f1"}], histories={"f1": []},
            verify_items=[], latest_captures={"f1": None}, now=T0)
        self.assertIn("collector_down", [p.origin for p in evaluation.problems])

    def test_genuinely_stale_feed_still_triggers_collector_down_alongside_a_new_one(self) -> None:
        # x is old and genuinely stale; f-new was created 1h ago with no
        # history yet (grace). Every feed we CAN judge is stale, so this is
        # still collector_down — a new feed shouldn't mask a real outage.
        histories = {
            "x": [_row("ok", T0 - timedelta(hours=20))],
            "f-new": [],
        }
        evaluation = health_rules.evaluate(
            rss_feeds=[{"feed_id": "x"}, {"feed_id": "f-new", "created_at": T0 - timedelta(hours=1)}],
            histories=histories, verify_items=[],
            latest_captures={"x": None, "f-new": None}, now=T0)

        self.assertIn("collector_down", [p.origin for p in evaluation.problems])
        self.assertNotIn("poll_stale:x", [p.origin for p in evaluation.problems])


if __name__ == "__main__":
    unittest.main()
