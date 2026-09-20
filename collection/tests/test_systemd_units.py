"""Test g from the harden-fetch spec: every collector systemd unit has a
runtime ceiling and an unbuffered journal. Static file check — nothing here
installs or runs a unit.
"""
from __future__ import annotations

import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "systemd"


class SystemdUnitTests(unittest.TestCase):
    def test_every_service_has_timeout_and_unbuffered_env(self) -> None:
        service_files = sorted(SYSTEMD_DIR.glob("*.service"))
        self.assertTrue(service_files, f"no .service files found under {SYSTEMD_DIR}")
        missing = []
        for path in service_files:
            text = path.read_text(encoding="utf-8")
            if not any(line.strip().startswith("TimeoutStartSec=") for line in text.splitlines()):
                missing.append(f"{path.name}: missing TimeoutStartSec=")
            if "PYTHONUNBUFFERED=1" not in text:
                missing.append(f"{path.name}: missing Environment=PYTHONUNBUFFERED=1")
        self.assertFalse(missing, "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
