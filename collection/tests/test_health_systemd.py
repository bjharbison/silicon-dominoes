"""Test l from the harden-health spec: sd-health.timer is hourly, and the
new sd-health-digest service/timer exist and run --digest weekly. Static
file checks only — nothing here installs or runs a unit (test_systemd_
units.py's generic TimeoutStartSec/PYTHONUNBUFFERED check already covers
these two new .service files, unmodified).
"""
from __future__ import annotations

import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "systemd"


class HealthTimerTests(unittest.TestCase):
    def test_sd_health_timer_is_hourly(self) -> None:
        text = (SYSTEMD_DIR / "sd-health.timer").read_text(encoding="utf-8")
        on_calendar = next(
            (line.split("=", 1)[1].strip() for line in text.splitlines()
            if line.strip().startswith("OnCalendar=")), None)
        self.assertIsNotNone(on_calendar, "sd-health.timer has no OnCalendar=")
        # Hourly means the hour field is a wildcard, not a fixed value like
        # "08:05" — accept the usual "*:MM" / "*-*-* *:MM" spellings.
        self.assertRegex(on_calendar, r"\*:\d{2}(:\d{2})?$",
                         f"sd-health.timer's OnCalendar ({on_calendar!r}) doesn't "
                         f"look hourly — expected an '*:MM' hour wildcard")

    def test_sd_health_digest_units_exist(self) -> None:
        service = SYSTEMD_DIR / "sd-health-digest.service"
        timer = SYSTEMD_DIR / "sd-health-digest.timer"
        self.assertTrue(service.exists(), f"{service} does not exist")
        self.assertTrue(timer.exists(), f"{timer} does not exist")

    def test_sd_health_digest_service_runs_the_digest_flag(self) -> None:
        text = (SYSTEMD_DIR / "sd-health-digest.service").read_text(encoding="utf-8")
        self.assertIn("collector.feed_health", text)
        self.assertIn("--digest", text)

    def test_sd_health_digest_timer_runs_monday(self) -> None:
        text = (SYSTEMD_DIR / "sd-health-digest.timer").read_text(encoding="utf-8")
        on_calendar = next(
            (line.split("=", 1)[1].strip() for line in text.splitlines()
            if line.strip().startswith("OnCalendar=")), None)
        self.assertIsNotNone(on_calendar, "sd-health-digest.timer has no OnCalendar=")
        self.assertTrue(on_calendar.startswith("Mon"),
                        f"expected a Monday OnCalendar, got {on_calendar!r}")
        self.assertIn("08:30", on_calendar)


if __name__ == "__main__":
    unittest.main()
