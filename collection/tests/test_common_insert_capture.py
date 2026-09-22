"""Regression test for a review finding on gdelt-slow: common.insert_capture
used to name the `metadata` column in its INSERT unconditionally, even when
metadata=None — meaning deploying this branch before collection/sql/005_
gdelt.sql is applied would break EVERY RSS capture (raw_captures.metadata
doesn't exist on any earlier schema), not just GDELT ones, despite the
function's own docstring claiming RSS callers were unaffected. Fixed by
naming the column only when metadata is not None. No Postgres: a minimal
fake cursor just records the exact SQL text and params passed to execute().
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # .../collection/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # .../collection

from collector import common  # noqa: E402


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self.rowcount = 0

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._conn.executed.append((sql, params))
        self.rowcount = 1


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.commit_calls = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commit_calls += 1


def _insert_sql(conn: _FakeConn) -> str:
    for sql, _params in conn.executed:
        if "INSERT INTO raw_captures" in sql:
            return sql
    raise AssertionError("no INSERT INTO raw_captures was executed")


class InsertCaptureMetadataColumnTests(unittest.TestCase):
    def setUp(self) -> None:
        # write_archive() touches the real filesystem (config.ARCHIVE_DIR) —
        # this test is only about the SQL text/params insert_capture builds,
        # so stub it out entirely rather than exercising a real archive dir.
        self._original_write_archive = common.write_archive
        common.write_archive = lambda feed_id, payload, ext="bin": "raw/stub/key"
        self.addCleanup(lambda: setattr(common, "write_archive", self._original_write_archive))

    def test_metadata_none_omits_the_column_entirely(self) -> None:
        conn = _FakeConn()
        common.insert_capture(
            conn, feed_id="rss-a", url="https://fixture.invalid/a", payload=b"hello",
            ext="html", parse_status="captured")

        sql = _insert_sql(conn)
        self.assertNotIn("metadata", sql,
                         "an RSS-style call (metadata=None, the default) must never "
                         "name the metadata column — that column doesn't exist until "
                         "005_gdelt.sql is applied")

    def test_metadata_dict_names_the_column_and_carries_its_json(self) -> None:
        conn = _FakeConn()
        common.insert_capture(
            conn, feed_id="gdelt-a", url="https://fixture.invalid/a", payload=b"hello",
            ext="json", parse_status="captured",
            metadata={"query_id": "gdelt-a", "sourcecountry": "VM"})

        sql, params = next((s, p) for s, p in conn.executed if "INSERT INTO raw_captures" in s)
        self.assertIn("metadata", sql)
        self.assertIn('"sourcecountry": "VM"', params[-1],
                     "the last placeholder's value should be the JSON-dumped metadata")

    def test_column_and_placeholder_counts_always_match(self) -> None:
        # Regression guard for the exact bug this file exists to catch: an
        # earlier fix attempt built the column list and the params tuple
        # from two lists that could silently drift out of alignment.
        for metadata in (None, {"a": 1}):
            conn = _FakeConn()
            common.insert_capture(
                conn, feed_id="f", url="https://fixture.invalid/x", payload=b"x",
                ext="html", parse_status="captured", metadata=metadata)
            sql, params = next((s, p) for s, p in conn.executed
                               if "INSERT INTO raw_captures" in s)
            self.assertEqual(sql.count("%s"), len(params),
                             f"metadata={metadata!r}: placeholder count must match "
                             f"param count")


if __name__ == "__main__":
    unittest.main()
