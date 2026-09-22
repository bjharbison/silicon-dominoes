"""Tests for breaker.gdelt_cooldown_active — pure, no I/O. `now` is always
injected explicitly (never datetime.now()), per the harden-health-era
lesson that a fake store reading the wall clock made a test's result depend
on when it happened to run.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import breaker  # noqa: E402

T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def _run(outcome: str, at: datetime) -> dict:
    return {"outcome": outcome, "finished_at": at}


class GdeltCooldownActiveTests(unittest.TestCase):
    def test_throttled_run_one_hour_ago_is_active(self) -> None:
        runs = [_run("throttled", T0 - timedelta(hours=1))]
        self.assertTrue(breaker.gdelt_cooldown_active(runs, T0))

    def test_throttled_run_twenty_five_hours_ago_is_not_active(self) -> None:
        runs = [_run("throttled", T0 - timedelta(hours=25))]
        self.assertFalse(breaker.gdelt_cooldown_active(runs, T0))

    def test_throttled_run_exactly_at_the_boundary_is_active(self) -> None:
        runs = [_run("throttled", T0 - timedelta(hours=24))]
        self.assertTrue(breaker.gdelt_cooldown_active(runs, T0))

    def test_rss_style_failures_never_activate_cooldown(self) -> None:
        # 'failed'/'timeout' are the RSS breaker's concern (decide()), not
        # this one — gdelt_cooldown_active only ever looks for 'throttled'.
        runs = [_run("failed", T0 - timedelta(minutes=5)),
               _run("timeout", T0 - timedelta(minutes=10)),
               _run("failed", T0 - timedelta(minutes=15))]
        self.assertFalse(breaker.gdelt_cooldown_active(runs, T0))

    def test_ok_runs_never_activate_cooldown(self) -> None:
        runs = [_run("ok", T0 - timedelta(minutes=5))]
        self.assertFalse(breaker.gdelt_cooldown_active(runs, T0))

    def test_empty_runs_is_not_active(self) -> None:
        self.assertFalse(breaker.gdelt_cooldown_active([], T0))

    def test_mixed_runs_across_multiple_feeds_one_throttled_activates_all(self) -> None:
        # Represents runs concatenated across every kind: gdelt feed — one
        # feed's throttle gates every other one too (shared IP).
        runs = [
            _run("ok", T0 - timedelta(hours=2)),        # feed-a
            _run("throttled", T0 - timedelta(hours=3)),  # feed-b
            _run("ok", T0 - timedelta(hours=1)),         # feed-c
        ]
        self.assertTrue(breaker.gdelt_cooldown_active(runs, T0))

    def test_custom_cooldown_window(self) -> None:
        runs = [_run("throttled", T0 - timedelta(hours=2))]
        self.assertTrue(breaker.gdelt_cooldown_active(runs, T0, cooldown=timedelta(hours=3)))
        self.assertFalse(breaker.gdelt_cooldown_active(runs, T0, cooldown=timedelta(hours=1)))


if __name__ == "__main__":
    unittest.main()
