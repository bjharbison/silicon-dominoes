"""Test g from the harden-ledger spec: static checks that
collection/sql/004_feed_runs.sql matches the code and follows the existing
immutability/GRANT conventions (db/schema.sql, collection/sql/002). This
never opens a database connection or applies the migration — CLAUDE.md §1:
DDL is never run by an agent, only written for Brian to apply by hand.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import breaker  # noqa: E402

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "004_feed_runs.sql"


class FeedRunsSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SQL_FILE.exists(), f"{SQL_FILE} does not exist")
        self.text = SQL_FILE.read_text(encoding="utf-8")

    def test_outcome_check_matches_breaker_outcomes_exactly(self) -> None:
        match = re.search(
            r"outcome\s+text\s+NOT NULL\s+CHECK\s*\(\s*outcome\s+IN\s*\(([^)]*)\)",
            self.text, re.IGNORECASE)
        self.assertIsNotNone(
            match, "no `outcome text NOT NULL CHECK (outcome IN (...))` found")
        values = {v.strip().strip("'\"") for v in match.group(1).split(",")}
        self.assertEqual(values, set(breaker.OUTCOMES),
                         "the SQL CHECK list must be identical to breaker.OUTCOMES — "
                         "the code and the schema must never drift apart on this")

    def test_has_an_append_only_trigger(self) -> None:
        self.assertRegex(
            self.text, r"CALL\s+make_append_only\(\s*'feed_runs'\s*\)",
            "feed_runs must be made append-only the same way every other "
            "immutable table is (make_append_only), not a one-off trigger")

    def test_grants_sd_pipeline_select_and_insert(self) -> None:
        # sd_pipeline is the role every other collector table grants to
        # (db/schema.sql, collection/sql/002); verified live 2026-09-20
        # that dominoes is a member of it, not a directly grantable role
        # itself — see the file's own GRANT comment.
        self.assertRegex(
            self.text, r"GRANT\s+SELECT\s*,\s*INSERT\s+ON\s+feed_runs\s+TO\s+sd_pipeline\s*;",
            "expected `GRANT SELECT, INSERT ON feed_runs TO sd_pipeline;`")

    def test_no_sequence_grant(self) -> None:
        # run_id is an identity column: no privilege on its underlying
        # sequence is needed, and a schema-wide sequence grant is out of
        # scope for this file.
        self.assertNotIn("ALL SEQUENCES", self.text,
                         "no sequence grant is needed for an identity column")

    def test_no_grant_to_dominoes(self) -> None:
        # dominoes is a member of sd_pipeline (verified live 2026-09-20),
        # not itself a grantable role for this table.
        self.assertNotRegex(
            self.text, r"GRANT[^;]*TO\s+dominoes",
            "grant sd_pipeline, not dominoes directly — dominoes is a "
            "member of sd_pipeline, per Brian's live verification")

    def test_no_test_file_opens_a_database_connection(self) -> None:
        # Defensive guard against a future test "helpfully" applying this
        # (or any) migration against a live database — CLAUDE.md §1 forbids
        # that outright, and the whole test suite promises "no Postgres".
        # (Excludes this file itself, whose own source necessarily contains
        # the marker string it's searching for.)
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
