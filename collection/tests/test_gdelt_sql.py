"""Static checks that collection/sql/005_gdelt.sql matches the code and
follows the conventions collection/sql/004_feed_runs.sql already established
(test_feed_runs_sql.py). Never opens a database connection or applies the
migration — CLAUDE.md §1: DDL is written for Brian to apply by hand, never
run by an agent.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import breaker, poll_gdelt  # noqa: E402

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "005_gdelt.sql"


class GdeltSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SQL_FILE.exists(), f"{SQL_FILE} does not exist")
        self.text = SQL_FILE.read_text(encoding="utf-8")

    def test_outcome_check_adds_exactly_the_two_gdelt_outcomes(self) -> None:
        match = re.search(
            r"ADD CONSTRAINT\s+feed_runs_outcome_check\s+CHECK\s*\(\s*outcome\s+IN\s*\(([^)]*)\)",
            self.text, re.IGNORECASE)
        self.assertIsNotNone(
            match, "no `ADD CONSTRAINT feed_runs_outcome_check CHECK (outcome IN (...))` found")
        values = {v.strip().strip("'\"") for v in match.group(1).split(",")}
        self.assertEqual(
            values, set(breaker.OUTCOMES) | poll_gdelt.GDELT_NEW_OUTCOMES,
            "005's new CHECK list must be exactly 004's original values "
            "(breaker.OUTCOMES) plus poll_gdelt.GDELT_NEW_OUTCOMES — no "
            "value silently dropped, none silently added")

    def test_drops_the_old_constraint_before_adding_the_new_one(self) -> None:
        drop_pos = self.text.find("DROP CONSTRAINT feed_runs_outcome_check")
        add_pos = self.text.find("ADD CONSTRAINT feed_runs_outcome_check")
        self.assertNotEqual(drop_pos, -1, "must DROP the old constraint by name")
        self.assertNotEqual(add_pos, -1, "must ADD the replacement constraint")
        self.assertLess(drop_pos, add_pos, "DROP must come before ADD")

    def test_adds_a_nullable_metadata_column_to_raw_captures(self) -> None:
        self.assertRegex(
            self.text, r"ALTER TABLE raw_captures ADD COLUMN metadata jsonb\s*;",
            "expected a nullable jsonb metadata column (no NOT NULL — RSS "
            "captures never populate it, and existing rows have none)")
        self.assertNotRegex(
            self.text, r"metadata jsonb[^;]*NOT NULL",
            "the new column must be nullable — it is GDELT-only provenance, "
            "not something every capture can supply")

    def test_no_grant_statements(self) -> None:
        # db/schema.sql's schema-wide `GRANT SELECT, INSERT ON ALL TABLES
        # IN SCHEMA public TO sd_pipeline` already covers a new column on
        # an already-granted table, and a CHECK constraint is not a
        # privilege — see this file's own header comment for the reasoning.
        # (The header comment itself discusses GRANT in prose, so this
        # checks only for an actual executable GRANT statement.)
        self.assertNotRegex(
            self.text, r"(?m)^\s*GRANT\s", "005 should issue no GRANT statements itself — "
                                           "see header comment for why none is needed")

    def test_wrapped_in_a_transaction(self) -> None:
        self.assertIn("BEGIN;", self.text)
        self.assertIn("COMMIT;", self.text)

    def test_no_test_file_opens_a_database_connection(self) -> None:
        this_file = Path(__file__).resolve()
        needle = "import " + "psycopg"
        for path in SQL_FILE.parent.parent.glob("tests/*.py"):
            if path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(needle, text,
                             f"{path.name} must not open a database connection")


if __name__ == "__main__":
    unittest.main()
