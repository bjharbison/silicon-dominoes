"""Test a & c from the harden-ledger spec: collector.breaker is pure logic —
no I/O, no servers, no store. History is just a list of dicts with at least
'outcome' and 'finished_at', most-recent-first, exactly as Store.recent_runs
returns it.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import breaker  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
THRESHOLD = 5
PROBE_AFTER = timedelta(hours=24)


def _run(outcome: str, at: datetime) -> dict:
    return {"outcome": outcome, "started_at": at, "finished_at": at}


def _history(outcomes_most_recent_first: list[str], start: datetime,
            step: timedelta = timedelta(hours=2)) -> list[dict]:
    """A most-recent-first history where entry i happened i*step before
    `start` — index 0 is the most recent, matching Store.recent_runs."""
    return [_run(o, start - i * step) for i, o in enumerate(outcomes_most_recent_first)]


class DecideTests(unittest.TestCase):
    def _decide(self, history, now):
        return breaker.decide(history, now, threshold=THRESHOLD, probe_after=PROBE_AFTER)

    def test_four_consecutive_failures_still_polls(self) -> None:
        history = _history(["failed"] * 4, T0)
        self.assertEqual(self._decide(history, T0), ("poll", None))

    def test_fifth_consecutive_failure_quarantines_exactly_once(self) -> None:
        history = _history(["failed"] * 5, T0)
        decision = self._decide(history, T0)
        self.assertEqual(decision.action, "skip")
        self.assertEqual(decision.state_change, "quarantined")

    def test_sixth_evaluation_after_a_skip_stays_quarantined_silently(self) -> None:
        # The skipped_quarantined row from the 5th evaluation's own decision
        # is now the most recent entry; the same 5 failures sit behind it.
        history = _history(["skipped_quarantined"], T0) + _history(["failed"] * 5, T0 - timedelta(hours=2))
        decision = self._decide(history, T0)
        self.assertEqual(decision.action, "skip")
        self.assertIsNone(decision.state_change,
                          "already-quarantined evaluations must not re-announce it")

    def test_probe_not_yet_allowed_before_24h(self) -> None:
        last_real_at = T0
        history = _history(["failed"] * 5, last_real_at)
        now = last_real_at + PROBE_AFTER - timedelta(minutes=1)
        self.assertEqual(self._decide(history, now).action, "skip")

    def test_probe_allowed_after_24h_since_last_real_attempt(self) -> None:
        # Skips recorded since the last real attempt don't move the probe
        # clock — only a real (non-skipped) attempt does.
        last_real_at = T0
        skips = _history(["skipped_quarantined"] * 3, last_real_at + timedelta(hours=23, minutes=59),
                         step=timedelta(minutes=1))
        history = skips + _history(["failed"] * 5, last_real_at)
        now = last_real_at + PROBE_AFTER
        decision = self._decide(history, now)
        self.assertEqual(decision.action, "poll")
        self.assertIsNone(decision.state_change, "the probe attempt itself announces nothing")

    def test_probe_ok_recovers_exactly_once(self) -> None:
        skips = _history(["skipped_quarantined"] * 3, T0 - timedelta(minutes=1),
                         step=timedelta(minutes=1))
        history = [_run("ok", T0)] + skips + _history(["failed"] * 5, T0 - timedelta(hours=2))
        decision = self._decide(history, T0)
        self.assertEqual(decision.action, "poll")
        self.assertEqual(decision.state_change, "recovered")

    def test_evaluation_after_recovery_has_no_further_state_change(self) -> None:
        history = [_run("ok", T0), _run("ok", T0 - timedelta(hours=2))] + \
            _history(["failed"] * 5, T0 - timedelta(hours=4))
        decision = self._decide(history, T0)
        self.assertEqual(decision.action, "poll")
        self.assertIsNone(decision.state_change)

    def test_budget_exhausted_resets_the_count_but_is_not_a_recovery_notification(self) -> None:
        # Literal spec wording: "ok and budget_exhausted reset the count"
        # (polling resumes) but "a probe that returns ok -> recovered"
        # (only 'ok' fires the notification) — budget_exhausted exits
        # quarantine silently.
        history = [_run("budget_exhausted", T0)] + _history(["failed"] * 10, T0 - timedelta(hours=2))
        self.assertEqual(self._decide(history, T0), ("poll", None))

    def test_mixed_history_with_skipped_rows_counts_correctly(self) -> None:
        history = [
            _run("failed", T0),
            _run("skipped_quarantined", T0 - timedelta(hours=2)),
            _run("failed", T0 - timedelta(hours=4)),
            _run("skipped_quarantined", T0 - timedelta(hours=6)),
            _run("failed", T0 - timedelta(hours=8)),
            _run("failed", T0 - timedelta(hours=10)),
            _run("failed", T0 - timedelta(hours=12)),
            _run("ok", T0 - timedelta(hours=14)),
        ]
        self.assertEqual(breaker.consecutive_failures(history), 5)

    def test_empty_history_polls_with_no_state_change(self) -> None:
        self.assertEqual(self._decide([], T0), ("poll", None))


class OrderFeedsTests(unittest.TestCase):
    def test_ordering_groups_and_is_stable(self) -> None:
        feeds = [{"feed_id": fid} for fid in ["a", "b", "c", "d", "e"]]
        latest_outcome = {
            "a": "failed", "b": "ok", "c": None,
            "d": "budget_exhausted", "e": "timeout",
        }
        ordered = [f["feed_id"] for f in breaker.order_feeds(feeds, latest_outcome)]
        self.assertEqual(ordered, ["b", "d", "c", "a", "e"])

    def test_ordering_is_stable_within_the_failing_group_too(self) -> None:
        feeds = [{"feed_id": fid} for fid in ["x", "y", "z"]]
        latest_outcome = {"x": "failed", "y": "timeout", "z": "failed"}
        ordered = [f["feed_id"] for f in breaker.order_feeds(feeds, latest_outcome)]
        self.assertEqual(ordered, ["x", "y", "z"])


if __name__ == "__main__":
    unittest.main()
