"""Static checks on sd-gdelt.service/.timer (gdelt-slow) — the timer, not
the code, is what paces GDELT requests (collector/poll_gdelt.py's module
docstring), so the cadence itself is worth pinning down with a test.
Nothing here installs, enables, or runs a unit (test_systemd_units.py's
generic TimeoutStartSec/PYTHONUNBUFFERED check already covers this
.service file, unmodified).
"""
from __future__ import annotations

import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "systemd"


def _on_calendar(text: str) -> str | None:
    return next(
        (line.split("=", 1)[1].strip() for line in text.splitlines()
        if line.strip().startswith("OnCalendar=")), None)


class GdeltTimerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timer_text = (SYSTEMD_DIR / "sd-gdelt.timer").read_text(encoding="utf-8")
        self.service_text = (SYSTEMD_DIR / "sd-gdelt.service").read_text(encoding="utf-8")

    def test_fires_every_fifteen_minutes(self) -> None:
        on_calendar = _on_calendar(self.timer_text)
        self.assertIsNotNone(on_calendar, "sd-gdelt.timer has no OnCalendar=")
        self.assertIn("/15", on_calendar,
                      f"expected a 15-minute step in OnCalendar ({on_calendar!r})")

    def test_window_is_restricted_to_overnight_hours(self) -> None:
        # Every-15-minutes ALL DAY would be ~96 fires/day — the spec calls
        # for ~16/night, so the hour field must be a bounded range, not a
        # bare wildcard.
        on_calendar = _on_calendar(self.timer_text)
        self.assertNotRegex(on_calendar, r"\*-\*-\* \*:00/15",
                            "OnCalendar must restrict to a bounded overnight hour "
                            "range, not fire every 15 minutes around the clock")
        self.assertRegex(on_calendar, r"\d{2}\.\.\d{2}",
                         f"expected an hour RANGE (HH..HH) in OnCalendar ({on_calendar!r})")

    def test_randomized_delay_is_well_under_the_fifteen_minute_spacing(self) -> None:
        # A RandomizedDelaySec anywhere near 15 minutes (900s) risks two
        # adjacent scheduled fires landing on top of each other, defeating
        # the deliberate spacing the whole design relies on.
        delay_line = next(
            (line for line in self.timer_text.splitlines()
            if line.strip().startswith("RandomizedDelaySec=")), None)
        self.assertIsNotNone(delay_line, "sd-gdelt.timer has no RandomizedDelaySec=")
        seconds = int(delay_line.split("=", 1)[1].strip())
        self.assertLess(seconds, 300,
                        f"RandomizedDelaySec={seconds}s is too close to the 900s "
                        f"(15min) fire spacing")

    def test_persistent_catch_up_is_disabled(self) -> None:
        # Deliberately the opposite of this project's usual convention
        # (every other timer uses Persistent=true). GDELT results are
        # retroactive, so a missed fire costs nothing, while a catch-up
        # fire could land outside the 02:00-05:45 UTC window or seconds
        # before the next regularly scheduled fire — see the timer file's
        # own comment. (That comment itself discusses Persistent=true in
        # prose, so this checks only the actual directive line.)
        persistent_line = next(
            (line for line in self.timer_text.splitlines()
            if line.strip().startswith("Persistent=")), None)
        self.assertIsNotNone(persistent_line, "sd-gdelt.timer has no Persistent= directive")
        self.assertEqual(persistent_line.strip(), "Persistent=false")

    def test_service_timeout_is_well_under_the_old_thirty_minutes(self) -> None:
        # The old 6-hourly, multi-request design justified a 30min ceiling;
        # one-request-per-fire needs far less. Not a hard number, just a
        # sanity check that it actually shrank.
        timeout_line = next(
            (line for line in self.service_text.splitlines()
            if line.strip().startswith("TimeoutStartSec=")), None)
        self.assertIsNotNone(timeout_line, "sd-gdelt.service has no TimeoutStartSec=")
        self.assertNotIn("30min", timeout_line,
                         "TimeoutStartSec should have shrunk from the old multi-request "
                         "design's 30min ceiling")

    def test_service_still_runs_poll_gdelt_with_no_flags(self) -> None:
        # The timer-driven unit must run the real (non---dry-run) poll —
        # --dry-run is for the hand-run probe/testing path only.
        self.assertIn("collector.poll_gdelt", self.service_text)
        self.assertNotIn("--dry-run", self.service_text)


if __name__ == "__main__":
    unittest.main()
