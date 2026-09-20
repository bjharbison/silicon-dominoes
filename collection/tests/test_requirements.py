"""Test h from the harden-fetch spec: every third-party module imported
anywhere under collection/collector/ is listed in collection/requirements.txt.
Static source scan against the standard library list for this interpreter,
so it doesn't need the packages themselves to be importable.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

COLLECTOR_DIR = Path(__file__).resolve().parents[1] / "collector"
REQUIREMENTS_FILE = Path(__file__).resolve().parents[1] / "requirements.txt"

# Import name -> distribution name, where they differ.
IMPORT_TO_DIST = {
    "yaml": "PyYAML",
    "psycopg": "psycopg",
}

try:
    STDLIB_NAMES = set(sys.stdlib_module_names)  # 3.10+
except AttributeError:
    STDLIB_NAMES = set(sys.builtin_module_names)


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:            # skip relative imports
                names.add(node.module.split(".")[0])
    return names


def _requirements_names() -> set[str]:
    names = set()
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=\[!~; ]", line, maxsplit=1)[0]
        names.add(name.lower())
    return names


class RequirementsTests(unittest.TestCase):
    def test_every_third_party_import_is_listed(self) -> None:
        requirements = _requirements_names()
        missing = []
        for path in sorted(COLLECTOR_DIR.glob("*.py")):
            for name in _top_level_imports(path):
                if name in STDLIB_NAMES or name == "collector":
                    continue
                dist = IMPORT_TO_DIST.get(name, name)
                if dist.lower() not in requirements:
                    missing.append(f"{path.name}: import {name!r} (expected {dist!r} "
                                    f"in requirements.txt)")
        self.assertFalse(missing, "\n".join(missing))

    def test_scan_finds_an_import_nested_inside_a_function(self) -> None:
        """Regression guard for the scan itself: extract.py imports
        trafilatura inside read_capture(), not at module level (it's behind
        a bare except so a missing install degrades gracefully — see
        collector/extract.py). A scan that only looked at module.body would
        miss it silently and this test would still pass the main assertion
        above even if the scan were broken, since a module-level-only scan
        would just find zero third-party imports in extract.py to check."""
        found = _top_level_imports(COLLECTOR_DIR / "extract.py")
        self.assertIn("trafilatura", found,
                     "AST scan did not find the trafilatura import nested inside "
                     "extract.read_capture() — it must use ast.walk(), not tree.body")
        self.assertIn("trafilatura", _requirements_names())


if __name__ == "__main__":
    unittest.main()
