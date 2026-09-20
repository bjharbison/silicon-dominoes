"""Test f from the harden-ledger spec (MUST-EXIT): fetcher.fetch()'s
watchdog thread must be a daemon thread. If it isn't, a process that calls
fetch() against a peer that never finishes responding, catches the
resulting FetchTimeout, and returns from main can still hang indefinitely —
fetch() itself returns on time, but Python does not exit the interpreter
while a non-daemon thread is still alive, even after main() has returned.

This has to be a real subprocess, not a thread inside this test process:
the property under test is "does the whole PROCESS exit," which an in-
process thread can't demonstrate (this test runner's own process would mask
it — it has plenty of other reasons to still be alive).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

import servers  # noqa: E402

COLLECTION_DIR = Path(__file__).resolve().parents[1]
DEADLINE = 2.0


class MustExitTests(unittest.TestCase):
    def test_process_exits_within_deadline_plus_3s_after_a_timed_out_fetch(self) -> None:
        server = servers.trickle_server()
        self.addCleanup(server.stop)

        script = textwrap.dedent(f"""\
            import sys
            sys.path.insert(0, {str(COLLECTION_DIR)!r})
            from collector import fetcher
            try:
                fetcher.fetch({server.url!r}, connect_timeout=1, read_timeout=30,
                              deadline={DEADLINE})
            except fetcher.FetchTimeout:
                pass
            print("child-done")
        """)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True, text=True, timeout=DEADLINE + 3,
                )
            except subprocess.TimeoutExpired:
                self.fail(
                    f"child process did not exit within deadline+3s ({DEADLINE + 3}s) — "
                    "fetcher.fetch()'s watchdog thread is not a daemon thread (or "
                    "something else is keeping the process alive)")
        finally:
            Path(script_path).unlink(missing_ok=True)

        self.assertIn("child-done", result.stdout,
                     f"child process did not reach the end of main — stderr:\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
