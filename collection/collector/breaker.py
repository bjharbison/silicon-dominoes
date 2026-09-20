"""Circuit breaker for poll_rss — pure logic, no I/O, no config import.

Reads a feed's run history (as collector.poll_rss's Store.recent_runs()
returns it — dicts with at least 'outcome' and 'finished_at', most-recent-
first) and decides whether to poll or skip it this run, and whether a
quarantine/recovery notification is due. All state this module needs lives
in the history itself (the feed_runs ledger, see collection/sql/004_feed_
runs.sql) — there is deliberately no separate "am I quarantined" flag
anywhere: a feed is quarantined precisely when its own history says so, and
"just entered" vs. "already in" quarantine is derived by comparing the
verdict on the full history against the verdict on that history with its
single most recent row removed (see decide()). That is what makes this
function pure and exactly reproducible from the ledger alone.

Terminology: an outcome is either a FAILURE ('failed', 'timeout'), a RESET
('ok', 'budget_exhausted' — a real attempt that was not a failure), or
IGNORED ('skipped_quarantined' — the breaker's own skip decision, which
carries no information about the feed itself and must not itself break or
extend a failure streak).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, NamedTuple, Sequence

FAILURE_OUTCOMES = frozenset({"failed", "timeout"})
RESET_OUTCOMES = frozenset({"ok", "budget_exhausted"})
IGNORED_OUTCOMES = frozenset({"skipped_quarantined"})
OUTCOMES = FAILURE_OUTCOMES | RESET_OUTCOMES | IGNORED_OUTCOMES

DEFAULT_THRESHOLD = 5
DEFAULT_PROBE_AFTER = timedelta(hours=24)

Run = Mapping[str, object]


class Decision(NamedTuple):
    action: str                    # 'poll' | 'skip'
    state_change: str | None       # None | 'quarantined' | 'recovered'


def consecutive_failures(history: Sequence[Run]) -> int:
    """Consecutive FAILURE_OUTCOMES rows counted from the front (most
    recent first) of `history`. IGNORED_OUTCOMES rows are skipped over —
    neither counted nor breaking the streak. A RESET_OUTCOMES row stops the
    count at whatever it has reached (0 if it's the first non-ignored row
    seen), since 'ok' and 'budget_exhausted' are both real, non-failing
    attempts."""
    count = 0
    for run in history:
        outcome = run["outcome"]
        if outcome in IGNORED_OUTCOMES:
            continue
        if outcome in FAILURE_OUTCOMES:
            count += 1
            continue
        break
    return count


def last_real_run(history: Sequence[Run]) -> Run | None:
    """The most recent row that isn't the breaker's own skip marker — i.e.
    the last time this feed was actually contacted."""
    for run in history:
        if run["outcome"] not in IGNORED_OUTCOMES:
            return run
    return None


def next_probe_at(history: Sequence[Run], probe_after: timedelta) -> datetime | None:
    """When a quarantined feed is next allowed a probe attempt, or None if
    it has no real run yet to measure from (shouldn't happen while
    quarantined, since quarantine requires prior real failures)."""
    last = last_real_run(history)
    if last is None:
        return None
    return last["finished_at"] + probe_after


def decide(history: Sequence[Run], now: datetime, *,
          threshold: int = DEFAULT_THRESHOLD,
          probe_after: timedelta = DEFAULT_PROBE_AFTER) -> Decision:
    """Decide whether to poll or skip a feed this run, given its history
    (most-recent-first) and the current time.

    - `threshold` consecutive failures (see consecutive_failures) ->
      quarantined: skip every run except one probe per `probe_after`.
    - state_change fires exactly once per transition: 'quarantined' the
      first evaluation whose failure count reaches `threshold` (detected by
      comparing the verdict on `history` to the verdict on `history[1:]` —
      it wasn't there one row ago, it is now), 'recovered' the first
      evaluation after a probe's outcome is 'ok' (specifically 'ok', not
      'budget_exhausted' — the latter resets the failure count so polling
      resumes, but only a clean probe counts as a verified recovery worth
      notifying about).
    """
    now_quarantined = consecutive_failures(history) >= threshold
    was_quarantined = consecutive_failures(history[1:]) >= threshold

    state_change = None
    if now_quarantined and not was_quarantined:
        state_change = "quarantined"
    elif not now_quarantined and was_quarantined and history and history[0]["outcome"] == "ok":
        state_change = "recovered"

    if not now_quarantined:
        return Decision("poll", state_change)

    probe_at = next_probe_at(history, probe_after)
    if probe_at is not None and now >= probe_at:
        return Decision("poll", state_change)
    return Decision("skip", state_change)


def _order_group(outcome: str | None) -> int:
    if outcome in ("ok", "budget_exhausted"):
        return 0
    if outcome is None:
        return 1
    return 2


def order_feeds(feeds: Sequence[Mapping], latest_outcome: Mapping[str, str | None]) -> list:
    """Feeds whose latest non-skipped outcome is ok/budget_exhausted first,
    then feeds with no history, then failing feeds — stable within each
    group (Python's sort is stable, so feeds.yaml order is preserved
    wherever `feeds` ties on group). `latest_outcome` maps feed_id to that
    feed's most recent non-skipped outcome, or None if it has no history."""
    return sorted(feeds, key=lambda f: _order_group(latest_outcome.get(f["feed_id"])))
