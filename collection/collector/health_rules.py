"""Pure feed-health rules — no I/O, no config import, `now` always passed
in. Everything here takes plain rows (dicts) and returns plain data, so the
tests need no Postgres.

The 2026-09-19 18h outage raised nothing, and reading feed_health.py plus
the live tables afterward turned up six separate reasons (STATUS.md, "Task
B1", health-monitor findings):
  1. it measured captures, never polls, so "nothing new" and "collector
     dead" looked identical;
  2. gaps opened once and never closed, so two false positives from August
     occupied the only slots a real outage could have used;
  3. it notified every day regardless, because two placeholder verify items
     always failed;
  4. its 48h-vs-30-day capture-rate check was weekend-blind;
  5. retired feeds stayed active = true forever, so they were checked (and
     alerted on) indefinitely;
  6. no gap carried a country.

This module is the fix for (1), (4) and (6) — deciding WHAT is wrong, from
the feed_runs ledger and breaker.py's existing "what counts as failing"
definition. collector/feed_health.py (the DB-facing orchestration) is the
fix for (2), (3) and (5): reconcile() below closes a gap the moment its
condition stops being asserted, feed_health.py notifies only on a change,
and config.sync_feeds retires feeds.yaml dropouts so their gaps become
closeable.

A second, later pass (harden-health follow-up) fixed a bug in the first
version: when evidence was unreadable, evaluate() simply asserted nothing
for the origins that depend on it, and reconcile() then read "not asserted"
as "condition cleared" and closed (or refused to open) gaps it had no actual
evidence about — an unreadable feed_runs table would have closed a real,
still-open collector_down gap, and a single failed raw_captures read would
have been silently treated as "confirmed never captured," opening a false
dead_feed gap on a feed that might be perfectly healthy. evaluate() now
returns which origins are UNDETERMINED this run (evidence unreadable) as a
value distinct from both "asserted" and "not asserted," and reconcile()
never opens or closes an undetermined origin. A failed read must never be
representable by the same value as a confirmed "no" or a confirmed "never."
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Mapping, NamedTuple, Sequence

from . import breaker

DEFAULT_POLL_STALE_H = 6.0
DEFAULT_BREAKER_THRESHOLD = 5
DEFAULT_COLLECTOR_DOWN_RENOTIFY_H = 6.0

# low_volume tuning — see _low_volume_problem for what each one does.
LOW_VOLUME_FRACTION = 0.25
LOW_VOLUME_MIN_MEDIAN = 5
LOW_VOLUME_FIRST_FILL_H = 48.0
LOW_VOLUME_PRIOR_WEEKS = 4
LOW_VOLUME_MIN_VALID_WEEKS = 3

MANAGED_PREFIXES = ("poll_stale:", "dead_feed:", "low_volume:")


class Problem(NamedTuple):
    origin: str
    feed_id: str | None
    description: str
    priority: str


class Evaluation(NamedTuple):
    problems: list[Problem]
    undetermined: frozenset[str]


def is_managed_origin(origin: str) -> bool:
    """A gap is 'managed' — eligible to be auto-opened/closed by
    feed_health.py — iff its origin is exactly 'collector_down' or starts
    with one of poll_stale: / dead_feed: / low_volume:. Anything else (e.g.
    an analyst-opened gap) is never touched."""
    return origin == "collector_down" or origin.startswith(MANAGED_PREFIXES)


def feed_id_from_origin(origin: str) -> str | None:
    """The feed_id encoded in a managed origin, or None for collector_down
    (which isn't about one feed) or an unrecognized origin. research_gaps
    has no feed_id column — origin is the only place this lives."""
    for prefix in MANAGED_PREFIXES:
        if origin.startswith(prefix):
            return origin[len(prefix):]
    return None


# ------------------------------------------------------------- evaluation --
def evaluate(*, rss_feeds: Sequence[Mapping], histories: Mapping[str, Sequence[Mapping]] | None,
            verify_items: Sequence[Mapping], latest_captures: Mapping[str, datetime | None],
            now: datetime,
            unreadable_captures: frozenset[str] = frozenset(),
            poll_stale_h: float = DEFAULT_POLL_STALE_H,
            breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD) -> Evaluation:
    """Everything currently wrong, as Evaluation(problems, undetermined).

    `problems` are CONFIRMED — every origin in it has evidence behind it.
    `undetermined` are origins this run has NO evidence for, one way or the
    other, because a read failed — never treat an origin's absence from
    `problems` as "confirmed fine" without also checking it isn't in
    `undetermined`: reconcile() already does this for you, but a caller
    reading `problems` directly must not.

    rss_feeds: [{"feed_id": ..., "created_at": datetime | omitted}, ...] —
      active feed_class='rss' rows only. `created_at` (feeds.created_at —
      see db/schema.sql; unaffected by sync_feeds' ON CONFLICT upsert, so it
      reflects when the feed_id first entered the table) is optional: a
      feed_id with no `created_at` and no history is treated as stale
      immediately (the pre-grace-period behavior). Supplied, it grants a
      no-history feed_id a grace window of poll_stale_h from creation before
      it can be called poll_stale at all — see NEW-FEED GRACE below.
    histories: feed_id -> that feed's feed_runs rows (most-recent-first,
      each {"outcome", "finished_at", "new_captures"}), or None if the
      ledger itself couldn't be read this run. When None, EVERY
      ledger-derived origin for every rss_feeds entry — collector_down,
      each poll_stale:<id>, each dead_feed:<id>, each low_volume:<id> — is
      undetermined, not silently absent from `problems`: an unreadable
      ledger must never look identical to "every feed is fine."
    verify_items: [{"feed_id": ..., "max_gap_days": int}, ...].
    latest_captures: feed_id -> most recent raw_captures.retrieved_at, or
      None if it has never captured anything — used by both the RSS
      "no captures ever" check and the verify-item staleness rule. A feed_id
      whose read genuinely failed still gets a None entry here (callers
      need SOME value to pass), but MUST also be listed in
      `unreadable_captures` — that's what tells evaluate() the None means
      "unknown," not "confirmed never."
    unreadable_captures: feed_ids whose raw_captures read failed this run.
      dead_feed:<id> depends on evidence from BOTH histories (the breaker's
      consecutive-failure count) and latest_captures (the "no captures
      ever" check for RSS, or the whole staleness check for a verify item)
      — if EITHER source is unreadable for a given feed_id, that feed_id's
      dead_feed:<id> origin is undetermined, full stop, regardless of what
      the other source says.

    FROZEN PER-FEED ORIGINS: whenever a feed can't currently be judged for
    poll-freshness — it's covered by collector_down, it's individually
    poll_stale, or it's a brand-new feed still in its creation grace window
    (see NEW-FEED GRACE) — that feed's OWN dead_feed:<id> and low_volume:<id>
    are added to `undetermined`, not silently left out of `problems`. Not
    being able to see a feed is not evidence its problem went away: without
    this, evaluate() simply stopped asserting those origins while the feed
    was unreachable, and reconcile() (correctly, given what it was told) read
    "not asserted" as "condition cleared" and closed a real, still-open gap.
    Verify items are unaffected by any of this — they don't depend on
    feed_runs, so collector_down and RSS poll-staleness never touch them.

    NEW-FEED GRACE: a feed_id with no feed_runs rows and a `created_at`
    newer than poll_stale_h ago is "grace," not poll_stale — everything
    about it (poll_stale:<id> included) is undetermined rather than treated
    as a confirmed staleness problem, because it hasn't existed long enough
    for silence to mean anything yet. A grace feed_id never counts toward
    collector_down in either direction: collector_down only compares feeds
    this run CAN judge (fresh or stale) against each other, so one new feed
    can't mask a real outage among the others, and a lone new feed can't
    manufacture a false one.
    """
    problems: list[Problem] = []
    undetermined: set[str] = set()

    if histories is None:
        undetermined.add("collector_down")
        for feed in rss_feeds:
            feed_id = feed["feed_id"]
            undetermined.add(f"poll_stale:{feed_id}")
            undetermined.add(f"dead_feed:{feed_id}")
            undetermined.add(f"low_volume:{feed_id}")
    else:
        freshness = {
            feed["feed_id"]: _poll_freshness(
                histories.get(feed["feed_id"], []), now, poll_stale_h, feed.get("created_at"))
            for feed in rss_feeds
        }
        stale_feed_ids = {fid for fid, verdict in freshness.items() if verdict == "stale"}
        grace_feed_ids = {fid for fid, verdict in freshness.items() if verdict == "grace"}
        judgeable_feed_ids = set(freshness) - grace_feed_ids
        collector_down = bool(judgeable_feed_ids) and stale_feed_ids == judgeable_feed_ids

        for feed_id in grace_feed_ids:
            # Too new to say anything about yet — not stale, not fresh.
            undetermined.add(f"poll_stale:{feed_id}")
            undetermined.add(f"dead_feed:{feed_id}")
            undetermined.add(f"low_volume:{feed_id}")

        if collector_down:
            problems.append(Problem(
                "collector_down", None,
                f"every active RSS feed's newest feed_runs row (of any outcome) is "
                f"older than {poll_stale_h:g}h — this is the collector itself, not "
                f"individually quiet feeds", "high"))
            for feed_id in judgeable_feed_ids:
                undetermined.add(f"dead_feed:{feed_id}")
                undetermined.add(f"low_volume:{feed_id}")
        else:
            for feed_id in stale_feed_ids:
                problems.append(Problem(
                    f"poll_stale:{feed_id}", feed_id,
                    f"Feed health: {feed_id} — no feed_runs row (of any outcome, "
                    f"skips included) in over {poll_stale_h:g}h", "high"))
                # Haven't heard from this feed recently — can't confirm OR
                # clear its own dead_feed/low_volume state either.
                undetermined.add(f"dead_feed:{feed_id}")
                undetermined.add(f"low_volume:{feed_id}")

            for feed in rss_feeds:
                feed_id = feed["feed_id"]
                if feed_id in stale_feed_ids or feed_id in grace_feed_ids:
                    continue
                history = histories.get(feed_id, [])

                # dead_feed has two independent evidence sources. The
                # breaker's consecutive-failure count comes from `history`
                # alone and proves dead_feed by itself, regardless of
                # whether raw_captures is readable — check it FIRST, not
                # gated on unreadable_captures. Only the "no captures ever"
                # half actually needs latest_captures.
                dead_reason = _breaker_dead_reason(history, breaker_threshold)
                if dead_reason is None and feed_id in unreadable_captures:
                    undetermined.add(f"dead_feed:{feed_id}")
                elif dead_reason is None:
                    dead_reason = _no_captures_ever_reason(
                        history, latest_captures.get(feed_id))

                if dead_reason is not None:
                    problems.append(Problem(f"dead_feed:{feed_id}", feed_id,
                                            f"Feed health: {feed_id} — {dead_reason}",
                                            "high"))
                    continue
                # dead_feed wasn't asserted (confirmed healthy, or
                # undetermined because captures were unreadable) —
                # low_volume only ever needed `history`, which IS readable
                # here, so it's evaluated either way, never frozen just
                # because raw_captures happened to fail this run.
                low = _low_volume_problem(feed_id, history, now)
                if low is not None:
                    problems.append(low)

    for item in verify_items:
        feed_id = item["feed_id"]
        if feed_id in unreadable_captures:
            undetermined.add(f"dead_feed:{feed_id}")
            continue
        max_gap_days = int(item.get("max_gap_days", 8))
        reason = _verify_item_dead_reason(latest_captures.get(feed_id), now, max_gap_days)
        if reason is not None:
            problems.append(Problem(f"dead_feed:{feed_id}", feed_id,
                                    f"Feed health: {feed_id} — {reason}", "high"))

    return Evaluation(problems, frozenset(undetermined))


def _poll_freshness(history: Sequence[Mapping], now: datetime, poll_stale_h: float,
                    created_at: datetime | None) -> str:
    """One of 'fresh', 'stale', 'grace' — grace only possible with no
    history and a created_at within poll_stale_h of now (see NEW-FEED GRACE
    in evaluate()'s docstring). No created_at supplied -> no grace, matching
    the pre-grace behavior (no history = stale)."""
    if history:
        newest = history[0]["finished_at"]
        return "fresh" if (now - newest) <= timedelta(hours=poll_stale_h) else "stale"
    if created_at is not None and (now - created_at) < timedelta(hours=poll_stale_h):
        return "grace"
    return "stale"


def _breaker_dead_reason(history: Sequence[Mapping], breaker_threshold: int) -> str | None:
    """dead_feed's first evidence source: the ledger alone. Independent of
    raw_captures — proves dead_feed even when latest_captures is unreadable."""
    if breaker.consecutive_failures(history) >= breaker_threshold:
        return f"{breaker_threshold} or more consecutive failed/timeout polls"
    return None


def _no_captures_ever_reason(history: Sequence[Mapping],
                             last_capture: datetime | None) -> str | None:
    """dead_feed's second evidence source: raw_captures. Only meaningful
    when latest_captures was actually readable — callers must not call this
    for a feed_id in unreadable_captures (that's the "unknown, not never"
    distinction the whole undetermined mechanism exists for)."""
    if history and last_capture is None:
        return "no captures ever recorded despite being polled"
    return None


def _verify_item_dead_reason(last_capture: datetime | None, now: datetime,
                             max_gap_days: int) -> str | None:
    if last_capture is None:
        return "never successfully verified"
    if (now - last_capture) > timedelta(days=max_gap_days):
        return f"no successful verification in over {max_gap_days} days"
    return None


def _week_bounds(now: datetime, weeks_back: int) -> tuple[datetime, datetime]:
    """[start, end) for the week `weeks_back` full weeks before the current
    one — weeks_back=0 is "the last 7 days", ending at `now`."""
    end = now - timedelta(days=7 * weeks_back)
    start = end - timedelta(days=7)
    return start, end


def _sum_new_captures(history: Sequence[Mapping], start: datetime, end: datetime) -> int:
    return sum((row.get("new_captures") or 0) for row in history
              if start <= row["finished_at"] < end)


def _low_volume_problem(feed_id: str, history: Sequence[Mapping],
                        now: datetime) -> Problem | None:
    """captures in the last 7 days < 25% of the median of the previous 4
    full weeks. Needs >= 3 of those weeks to have happened entirely after
    the feed's first 48h (first-fill bursts never enter the baseline, so
    they can't make a later steady state look like a collapse by
    comparison); skip if the baseline itself is too thin (median < 5) to
    say anything meaningful about a drop."""
    if not history:
        return None
    first_seen = min(row["finished_at"] for row in history)
    ignore_before = first_seen + timedelta(hours=LOW_VOLUME_FIRST_FILL_H)

    weekly = []
    for weeks_back in range(1, LOW_VOLUME_PRIOR_WEEKS + 1):
        start, end = _week_bounds(now, weeks_back)
        if start < ignore_before:
            continue                             # week overlaps the first-fill window
        weekly.append(_sum_new_captures(history, start, end))

    if len(weekly) < LOW_VOLUME_MIN_VALID_WEEKS:
        return None                              # not enough clean history to judge

    median = statistics.median(weekly)
    if median < LOW_VOLUME_MIN_MEDIAN:
        return None

    last7_start, last7_end = _week_bounds(now, 0)
    last7 = _sum_new_captures(history, last7_start, last7_end)

    if last7 < LOW_VOLUME_FRACTION * median:
        return Problem(
            f"low_volume:{feed_id}", feed_id,
            f"Feed health: {feed_id} — {last7} capture(s) in the last 7 days vs a "
            f"weekly median of {median:.1f} over the prior {len(weekly)} week(s)",
            "default")
    return None


# --------------------------------------------------------------- reconcile --
def reconcile(open_managed_gaps: Sequence[Mapping], asserted: Sequence[Problem],
             undetermined: frozenset[str] = frozenset()
             ) -> tuple[list[Problem], list[Mapping]]:
    """(to_open, to_close). `open_managed_gaps` is the CURRENT open,
    managed gaps (already filtered by is_managed_origin — see
    DbHealthStore.open_managed_gaps); `asserted` and `undetermined` are
    this run's evaluate() output (its `.problems` and `.undetermined`).

    A Problem whose origin has no matching open gap -> open a new one,
    UNLESS that origin is undetermined this run. An open gap whose origin
    is no longer asserted -> close it, UNLESS that origin is undetermined
    this run. "Undetermined" always wins over both directions: no evidence
    means no action, not "assume it cleared." (evaluate() already keeps
    undetermined origins out of `asserted`, so in practice the `to_open`
    guard is redundant with correctly-behaving callers — it's here so
    reconcile() is correct on its own terms, not just in combination with
    one particular evaluate() implementation.)

    Nothing else: running this twice on unchanged input returns ([], [])
    both times, and a condition that clears and later comes back gets a
    brand-new gap (the old one stays closed) rather than reusing the closed
    row — that's the point, not an oversight."""
    open_origins = {gap["origin"] for gap in open_managed_gaps}
    asserted_origins = {problem.origin for problem in asserted}

    to_open = [p for p in asserted
              if p.origin not in open_origins and p.origin not in undetermined]
    to_close = [g for g in open_managed_gaps
               if g["origin"] not in asserted_origins and g["origin"] not in undetermined]
    return to_open, to_close


def should_renotify_collector_down(
        opened_at: datetime, now: datetime,
        renotify_h: float = DEFAULT_COLLECTOR_DOWN_RENOTIFY_H) -> bool:
    """Whether a still-open collector_down gap is due another notification.
    No state table records the last renotify time (spec: "no new state
    table") — this derives entirely from opened_at and now: False until at
    least renotify_h has elapsed since opened_at, then True for roughly the
    first hour after each further multiple of renotify_h, which lines up
    with one hourly health run per window at the default hourly cadence
    (see collection/systemd/sd-health.timer).

    The `elapsed_h < renotify_h` guard matters on its own, not just as a
    special case of the modulo: without it, elapsed_h % renotify_h is just
    elapsed_h for any elapsed_h below renotify_h, so EVERY run within the
    first renotify_h-hour window (including one 30-50 minutes after opening,
    well inside typical timer jitter) would satisfy "< 1.0" and renotify
    immediately — the opposite of "at most once per renotify_h"."""
    elapsed_h = (now - opened_at).total_seconds() / 3600.0
    if elapsed_h < renotify_h:
        return False
    return (elapsed_h % renotify_h) < 1.0
