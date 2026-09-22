"""Poll GDELT DOC 2.0 — one query per invocation, never more (gdelt-slow).

GDELT DOC 2.0 throttles hard and the block is sticky: 2026-07 third-party
evidence found ~60 requests over 90 minutes triggered a block no retry
interval cleared, and backing off made it worse. A throttled response
carries NO JSON error object — it reads exactly like "zero results" unless
the body is actually parsed (see collector.fetcher.classify_gdelt_body).
That is almost certainly what killed the original gdelt-sea-stack feed: a
burst of queries per poll, blocked early, and a health monitor reading only
capture counts that could not tell a block from a quiet day.

The fix here is structural, not a bigger backoff: this module issues AT
MOST ONE DOC 2.0 request per invocation, full stop. Pacing lives entirely
in the systemd timer (collection/systemd/sd-gdelt.timer — every 15 minutes,
02:00-06:00 UTC, ~16 fires/night), not in this code — a bug in this module
cannot produce a burst, because there is no loop over queries to have a bug
in. GDELT's DOC 2.0 results are retroactive (timespan=7d returns the same
set whether queried hourly or weekly), so polling slowly costs only
latency, never coverage.

Each `kind: gdelt` entry in feeds.yaml is one query (feed_id, query,
timespan, optional country_iso3/maxrecords — see feeds.yaml for the
worked examples). This deliberately reuses the same feed_runs ledger,
breaker machinery, sync_feeds, and feed_health as poll_rss — there is no
parallel manifest or ledger table for GDELT. What's different is the
*policy* layered on top:

  - Query SELECTION (select_next_query, pure, below) round-robins active
    queries by ledger history alone, no stored cursor: a query never
    attempted at all goes first; among the rest, the query least recently
    ATTEMPTED (any real outcome, not just 'ok' — a chronically failing
    query must not block the rotation forever) goes next; a query with an
    'ok' run inside config.GDELT_QUERY_MIN_GAP_DAYS is not eligible at all
    this invocation (retroactive results mean polling it sooner buys
    nothing).
  - Cooldown (collector.breaker.gdelt_cooldown_active) is a flat, IP-wide
    gate: ANY kind: gdelt query throttled in the last config.GDELT_COOLDOWN_H
    blocks EVERY kind: gdelt query, because they share an IP and the block
    is not per-query. This is checked in addition to selection, not instead
    of it — see run()'s docstring for the exact order.

Unlike poll_rss, a down feed_runs ledger is NOT treated as "every query is
healthy" here: selection and cooldown are both computed FROM the ledger, so
if it can't be read there is no sound decision to make about which query
(if any) is safe to run, and this module fails loudly (non-zero exit)
rather than silently guessing. poll_rss's ledger-optional fallback exists
to keep many independent feeds polling through a down ledger; GDELT's
one-request-per-fire design has no such independent fallback to fall back
to.

Run:  python -m collector.poll_gdelt
      python -m collector.poll_gdelt --dry-run   # report only — no requests,
                                                  # no ledger/capture writes
                                                  # (main() still runs config.
                                                  # sync_feeds beforehand, as
                                                  # every run does)
"""
from __future__ import annotations

import argparse
import json as json_module
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, NamedTuple, Sequence

from . import breaker, common, config, fetcher, poll_rss

# GDELT DOC 2.0's public article-list endpoint. PROVISIONAL alongside
# fetcher.GDELT_THROTTLE_SIGNATURES — see that module for why.
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Used only for query SELECTION (select_next_query) — each active kind:
# gdelt feed's store.recent_runs(feed_id, HISTORY_LOOKBACK), most-recent-
# first. NOT used for the cooldown check (see DbStore.recent_throttles'
# docstring for why a capped, per-feed lookup isn't safe there). 100 is
# generous margin for selection's purposes: at the timer's 15-minute
# cadence and the config.GDELT_QUERY_MIN_GAP_DAYS (default 7d) gap between
# eligible re-polls of the same query, even a chronically-skipped or
# chronically-failing query accumulates nowhere near 100 rows before its
# next turn comes up in the least-recently-attempted ordering.
HISTORY_LOOKBACK = 100

# The two feed_runs.outcome values GDELT needs beyond breaker.OUTCOMES (see
# collection/sql/005_gdelt.sql, which ALTERs the CHECK constraint to add
# exactly these) — the canonical source test_gdelt_sql.py checks the SQL
# file against, same pattern as test_feed_runs_sql.py checking 004 against
# breaker.OUTCOMES.
GDELT_NEW_OUTCOMES = frozenset({"throttled", "skipped_throttled"})

Run = Mapping[str, object]


class QuerySelection(NamedTuple):
    feed: Mapping
    reason: str   # 'never_attempted' | 'least_recently_attempted'


class Outcome(NamedTuple):
    """What one invocation of run() did. `wrote_row` distinguishes the two
    paths that touch the ledger (True) from the two that don't: 'nothing to
    do' (no feed_id to attach a row to — see run()'s docstring) and
    dry-run (deliberately issues no request and writes no ledger/capture
    row — but run() itself is only ever called after main() has already
    run config.sync_feeds, dry-run or not, so "touches nothing" overstates
    it). `previous_outcome` is the
    outcome of the most recent real attempt across ALL kind: gdelt feeds
    BEFORE this invocation's own row was written (None if there has never
    been one) — main() feeds it to gdelt_notify_decision to decide whether
    to alert; it's carried on every status for uniformity, but only
    meaningful for the five real-attempt statuses."""
    status: str            # 'ok' | 'budget_exhausted' | 'throttled' |
                            # 'skipped_throttled' | 'nothing_to_do' | 'failed' |
                            # 'timeout' | 'dry_run'
    feed_id: str | None
    reason: str | None     # selection reason, when a feed was selected
    requests_issued: int
    entries_seen: int
    new_captures: int
    wrote_row: bool
    previous_outcome: str | None = None


def _last_ok_at(history: Sequence[Run]) -> datetime | None:
    for run in history:
        if run["outcome"] == "ok":
            return run["finished_at"]
    return None


# Outcomes that do NOT count as a real attempt at a query, for ORDERING
# purposes (see select_next_query below) and for the notify decision (see
# gdelt_notify_decision below): both are this module's own skip decisions
# (a cooldown gate, or — not currently emitted by poll_gdelt.py, but
# excluded defensively for the same reason — a breaker-style quarantine
# skip), and say nothing about whether the query itself works. Everything
# else ('ok', 'failed', 'timeout', 'budget_exhausted', 'throttled') is a
# real attempt: GDELT was actually asked, however the request turned out.
NOT_AN_ATTEMPT = frozenset({"skipped_throttled", "skipped_quarantined"})


def _last_real_attempt(history: Sequence[Run]) -> Run | None:
    for run in history:
        if run["outcome"] not in NOT_AN_ATTEMPT:
            return run
    return None


def _last_attempted_at(history: Sequence[Run]) -> datetime | None:
    run = _last_real_attempt(history)
    return run["finished_at"] if run is not None else None


def _most_recent_real_attempt_outcome(histories: Mapping[str, Sequence[Run]]) -> str | None:
    """The outcome of the single most recent real attempt across EVERY
    kind: gdelt feed's history — used only for gdelt_notify_decision below,
    which cares about the health of the shared GDELT pipeline as a whole
    (one endpoint, one shared cooldown), not any individual query. None if
    there has never been a real attempt for any of them."""
    best: Run | None = None
    for history in histories.values():
        run = _last_real_attempt(history)
        if run is None:
            continue
        if best is None or run["finished_at"] > best["finished_at"]:
            best = run
    return best["outcome"] if best is not None else None


# The only current_outcome values gdelt_notify_decision ever needs to act
# on — the five real-attempt outcomes (see NOT_AN_ATTEMPT above). Anything
# else ('nothing_to_do', 'skipped_throttled', 'dry_run') is not a real
# attempt and always resolves to "don't notify".
NOTIFY_ELIGIBLE_OUTCOMES = frozenset({"ok", "budget_exhausted", "failed", "timeout", "throttled"})


def gdelt_notify_decision(previous_outcome: str | None,
                          current_outcome: str) -> tuple[str, str] | None:
    """Pure function deciding whether main() should notify about THIS
    invocation's outcome. `previous_outcome` is the outcome of the most
    recent PRIOR real attempt across every kind: gdelt feed (None if there
    has never been one — see _most_recent_real_attempt_outcome); `current_
    outcome` is this invocation's own outcome. Returns (title, priority) to
    notify with, or None to stay quiet.

    Before this function existed, main() notified on every failed/timeout
    outcome unconditionally — up to ~16 high-priority alerts a night at the
    timer's 15-minute cadence, one per fire, for a condition already
    alerted about minutes earlier. feed_health.py does not evaluate
    structured_news (kind: gdelt) feeds at all, so GDELT still needs its
    own alerting — this just gates it on STATE CHANGE instead of on every
    real attempt:

      - 'throttled' always notifies. This is naturally at most once per
        cooldown window BY CONSTRUCTION, not by a check here: once
        recorded, collector.breaker.gdelt_cooldown_active blocks every
        further real attempt (and therefore every further call into this
        function) until the cooldown expires — there is no consecutive-
        throttle case that could otherwise repeat the alert.
      - 'failed'/'timeout' notifies only if the previous real attempt was
        NOT ITSELF 'failed'/'timeout' (either one suppresses, regardless of
        which — they're the same "a real attempt didn't work" bucket for
        this purpose): the first failure in a streak alerts, a second (or
        later) consecutive one does not repeat it.
      - 'ok'/'budget_exhausted' notifies "GDELT recovered" (default
        priority, not high) only if the previous real attempt was
        'failed'/'timeout'/'throttled' — i.e. only on an actual recovery,
        never on an ordinary successful poll following another one, and
        never on the very first attempt ever (previous_outcome is None).

    Only ever called by main() for a real attempt in the first place (see
    NOTIFY_ELIGIBLE_OUTCOMES), but stays total for any current_outcome
    string, returning None for anything outside that set — both for its
    own sake and for testability.
    """
    if current_outcome not in NOTIFY_ELIGIBLE_OUTCOMES:
        return None
    if current_outcome == "throttled":
        return ("Collector: GDELT throttled", "high")
    if current_outcome in ("failed", "timeout"):
        if previous_outcome in ("failed", "timeout"):
            return None
        return ("Collector: GDELT poll failed", "high")
    # current_outcome in ("ok", "budget_exhausted")
    if previous_outcome in ("failed", "timeout", "throttled"):
        return ("Collector: GDELT recovered", "default")
    return None


def select_next_query(feeds: Sequence[Mapping], histories: Mapping[str, Sequence[Run]],
                      now: datetime, *, min_gap: timedelta) -> QuerySelection | None:
    """Pick the single kind: gdelt query to run this invocation, or None if
    every query has an 'ok' run inside `min_gap`. Pure: no I/O, `histories`
    is the caller's job to assemble (each feed_id's recent_runs, most-
    recent-first — same shape poll_rss.DbStore.recent_runs returns).

    Eligibility is unchanged: a query with an 'ok' run inside `min_gap` is
    excluded entirely (GDELT results are retroactive, so polling it sooner
    buys no coverage — see the module docstring).

    ORDER is least-recently-ATTEMPTED, not "no ok yet". An earlier version
    of this function sorted "never had an ok" first, unconditionally — but
    a query that always fails or is always throttled (e.g. a bad query
    string GDELT answers with a text error) then NEVER accumulates an ok,
    so it would be re-selected forever ahead of every other query, even one
    that has genuinely never been attempted even once: permanent head-of-
    line blocking, starving the rest of the queries indefinitely. A real
    attempt is any outcome except 'skipped_throttled'/'skipped_quarantined'
    (NOT_AN_ATTEMPT above) — this module's own skip decisions, which carry
    no information about the query itself, same distinction breaker.py
    already draws between FAILURE/RESET outcomes and IGNORED_OUTCOMES.

    A query with no real attempt at all always sorts first — there is no
    way to be "more overdue" than never having been tried even once. Among
    the rest, the one least recently attempted (oldest finished_at, over
    ANY real-attempt outcome, not just 'ok') goes next — so a chronically
    failing query still gets re-tried eventually, but only after every
    other query has had its turn, not instead of them. Ties (e.g. two
    queries both never attempted) keep feeds.yaml's own list order, since
    Python's sort is stable.
    """
    never = datetime.min.replace(tzinfo=timezone.utc)
    candidates: list[tuple[datetime, Mapping, str]] = []
    for feed in feeds:
        history = histories.get(feed["feed_id"], [])
        last_ok = _last_ok_at(history)
        if last_ok is not None and (now - last_ok) < min_gap:
            continue
        last_attempt = _last_attempted_at(history)
        if last_attempt is None:
            candidates.append((never, feed, "never_attempted"))
        else:
            candidates.append((last_attempt, feed, "least_recently_attempted"))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    _, feed, reason = candidates[0]
    return QuerySelection(feed, reason)


def _gdelt_params(feed: Mapping) -> dict:
    return {
        "query": feed["query"],
        "mode": "artlist",
        "format": "json",
        "timespan": feed.get("timespan", "7d"),
        "maxrecords": int(feed.get("maxrecords", 100)),
        "sort": "datedesc",
    }


class DbStore:
    """Default store: the real database, via the psycopg connection main()
    already opened. Deliberately its own small class rather than importing
    poll_rss.DbStore — same precedent as feed_health.DbHealthStore not
    reusing poll_rss.DbStore even though both wrap the same tables."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def recent_runs(self, feed_id: str, limit: int) -> list[dict]:
        # Same rollback-before-reraise discipline as poll_rss.DbStore, for
        # the same reason: a failed statement (e.g. UndefinedTable before
        # 004_feed_runs.sql is applied) leaves the connection aborted, and
        # every later statement on it fails too until rollback() runs.
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT outcome, started_at, finished_at, entries_seen,
                           new_captures, error
                    FROM feed_runs
                    WHERE feed_id = %s
                    ORDER BY finished_at DESC
                    LIMIT %s
                    """,
                    (feed_id, limit),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            self._conn.rollback()
            raise

    def record_run(self, *, feed_id: str, started_at: datetime, finished_at: datetime,
                   outcome: str, entries_seen: int, new_captures: int,
                   error: str | None) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feed_runs
                      (feed_id, started_at, finished_at, outcome, entries_seen,
                       new_captures, error)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (feed_id, started_at, finished_at, outcome, entries_seen,
                     new_captures, error),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def recent_throttles(self, since: datetime) -> list[dict]:
        """Every feed_runs row with outcome='throttled' at or after
        `since`, across ALL feed_ids — not scoped to today's active kind:
        gdelt feeds, and not capped with a LIMIT. Only GDELT ever writes
        'throttled', so no feed_id filter is needed to keep this GDELT-
        only; omitting one is deliberate, since a query REMOVED from
        feeds.yaml since its throttle can still be the reason the shared,
        IP-wide cooldown (collector.breaker.gdelt_cooldown_active) must
        stay active — a per-feed_id lookup would blind this to exactly
        that case. No LIMIT: concatenating each feed's own capped recent_
        runs(feed_id, 100) (the earlier approach) was only correct by
        arithmetic — 100 comfortably exceeds the ~17 skipped_throttled
        rows one cooldown window produces at the 15-minute timer cadence,
        but that's a coincidence of the current cadence/config, not a
        guarantee, and a busier query mix could silently push the one
        'throttled' row that matters out of a capped window."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT outcome, finished_at
                    FROM feed_runs
                    WHERE outcome = 'throttled' AND finished_at >= %s
                    """,
                    (since,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            self._conn.rollback()
            raise

    def already_captured(self, feed_id: str, url: str) -> bool:
        return common.url_already_captured(self._conn, feed_id, url)

    def record_capture(self, feed_id: str, url: str, payload: bytes, ext: str,
                       status: str, snapshot_id: str | None, snapshot_url: str | None,
                       metadata: dict | None = None) -> bool:
        return common.insert_capture(
            self._conn, feed_id=feed_id, url=url, payload=payload, ext=ext,
            parse_status=status, snapshot_id=snapshot_id, snapshot_url=snapshot_url,
            metadata=metadata)

    def rollback(self) -> None:
        self._conn.rollback()


def _safe_record_run(store, **kwargs) -> None:
    """Aborted-transaction discipline (CLAUDE.md §5 / this module's
    docstring): roll back before re-raising, so a failure writing THIS row
    can't poison the connection for anything after it. Unlike poll_rss's
    _make_ledger_calls, this does not swallow the exception — see the
    module docstring for why a down ledger is a hard failure here."""
    try:
        store.record_run(**kwargs)
    except Exception:
        rollback = getattr(store, "rollback", None)
        if rollback is not None:
            rollback()
        raise


def _capture_articles(feed_id: str, articles: list[dict], store) -> tuple[int, bool]:
    """Fetch and archive each article's body, exactly mirroring poll_rss.
    poll_feed's loop (same fallback-to-entry_only behavior, same wayback
    submission, same dedupe-by-already-captured-URL) under a per-invocation
    wall-clock budget (config.GDELT_BUDGET) — budget exhaustion stops the
    loop early and is reported as 'budget_exhausted', not a failure; the
    remaining articles are picked up on a later poll via the same
    (feed_id, url) dedup RSS relies on.

    Returns (new_captures, budget_hit): new_captures is the count of
    articles actually newly archived (store.record_capture returned True);
    budget_hit is whether the loop stopped early on the wall-clock budget.
    Already-captured URLs are counted and printed as they're skipped, but
    that count is not part of the return value — the caller derives
    entries_seen from len(articles), which already includes skipped ones.
    """
    new = 0
    skipped = 0
    start = time.monotonic()
    budget_hit = False
    for i, article in enumerate(articles):
        if time.monotonic() - start > config.GDELT_BUDGET:
            print(f"[{feed_id}] outcome=budget_exhausted "
                 f"articles_remaining={len(articles) - i}")
            budget_hit = True
            break
        link = article.get("url")
        if not link:
            continue
        if store.already_captured(feed_id, link):
            skipped += 1
            continue
        payload = poll_rss.fetch_article(link)
        if payload is not None:
            status, ext = "captured", "html"
        else:
            payload = repr({k: article.get(k) for k in
                            ("title", "url", "seendate", "domain")}).encode()
            status, ext = "entry_only", "txt"
        snap_id, snap_url = common.wayback_submit(link) if payload else (None, None)
        metadata = {
            "query_id": feed_id,
            "seendate": article.get("seendate"),
            "sourcecountry": article.get("sourcecountry"),
            "language": article.get("language"),
            "domain": article.get("domain"),
        }
        if store.record_capture(feed_id, link, payload, ext, status, snap_id, snap_url,
                               metadata):
            new += 1
    if skipped:
        print(f"[{feed_id}] skipped {skipped} already-captured URL(s)")
    return new, budget_hit


def run(cfg: dict, store, *, now: datetime | None = None, dry_run: bool = False,
        fetch_query: Callable[[Mapping], "fetcher.FetchResult"] | None = None) -> Outcome:
    """One GDELT invocation. Order of decisions:

      1. Select the next-due query from the ledger (select_next_query,
         pure) — computed FIRST, unconditionally, so a cooldown skip has a
         feed_id to attach its ledger row to, and so --dry-run can report
         what selection would have chosen even while blocked by cooldown.
      2. If nothing is eligible (every query 'ok' inside the gap): 'nothing
         to do', print one line, no ledger row (there is no feed_id to
         attach one to), exit clean.
      3. If the shared cooldown is active: record 'skipped_throttled'
         against the selected feed_id, zero requests, exit clean — never a
         sleep-retry.
      4. Otherwise issue exactly one DOC 2.0 request for the selected
         query, classify the body, and either record 'ok'/'budget_exhausted'
         (articles archived) or 'throttled' (body was throttled OR
         unparseable — see fetcher.classify_gdelt_body; the distinction is
         kept in the free-text error column, not the constrained outcome
         column, since both are handled identically here).

    --dry-run stops after step 1/3's reporting and never calls store.
    record_run / store.record_capture / fetch_query.
    """
    now = now or datetime.now(timezone.utc)
    fetch_query = fetch_query or (lambda feed: fetcher.get(
        GDELT_ENDPOINT, params=_gdelt_params(feed),
        headers={"User-Agent": config.USER_AGENT}))

    gdelt_feeds = [f for f in cfg.get("feeds", []) if config.feed_kind(f) == "gdelt"]
    histories = {f["feed_id"]: store.recent_runs(f["feed_id"], HISTORY_LOOKBACK)
                for f in gdelt_feeds}
    # Computed once, from state as of BEFORE this invocation writes its own
    # row — this is the "previous" half of gdelt_notify_decision's input.
    previous_outcome = _most_recent_real_attempt_outcome(histories)
    min_gap = timedelta(days=config.GDELT_QUERY_MIN_GAP_DAYS)
    selection = select_next_query(gdelt_feeds, histories, now, min_gap=min_gap)

    if selection is None:
        print("[gdelt] nothing to do — every query has an 'ok' run inside "
             f"{config.GDELT_QUERY_MIN_GAP_DAYS:g} day(s)")
        return Outcome("nothing_to_do", None, None, 0, 0, 0, wrote_row=False,
                       previous_outcome=previous_outcome)

    feed_id, reason = selection.feed["feed_id"], selection.reason
    cooldown_window = timedelta(hours=config.GDELT_COOLDOWN_H)
    # A dedicated time-windowed lookup (store.recent_throttles), not the
    # per-feed capped `histories` gathered above for selection — see that
    # method's docstring for why a LIMIT-based approach isn't safe here.
    throttled_runs = store.recent_throttles(now - cooldown_window)
    cooldown = breaker.gdelt_cooldown_active(throttled_runs, now, cooldown=cooldown_window)

    if dry_run:
        cooldown_note = " (BLOCKED by cooldown)" if cooldown else ""
        print(f"[dry-run] would poll {feed_id} ({reason}){cooldown_note}")
        print(f"[dry-run] 0 requests issued, 0 new captures — no ledger/capture writes "
             f"(config.sync_feeds still ran before this, in main())")
        return Outcome("dry_run", feed_id, reason, 0, 0, 0, wrote_row=False,
                       previous_outcome=previous_outcome)

    started_at = datetime.now(timezone.utc)

    if cooldown:
        print(f"[gdelt] cooldown active — skipping {feed_id} "
             f"(would have run: {reason})")
        finished_at = datetime.now(timezone.utc)
        _safe_record_run(store, feed_id=feed_id, started_at=started_at,
                         finished_at=finished_at, outcome="skipped_throttled",
                         entries_seen=0, new_captures=0, error=None)
        return Outcome("skipped_throttled", feed_id, reason, 0, 0, 0, wrote_row=True,
                       previous_outcome=previous_outcome)

    print(f"[{feed_id}] query: {selection.feed['query']!r} ({reason})")
    try:
        resp = fetch_query(selection.feed)
    except fetcher.FetchTimeout as exc:
        finished_at = datetime.now(timezone.utc)
        _safe_record_run(store, feed_id=feed_id, started_at=started_at,
                         finished_at=finished_at, outcome="timeout",
                         entries_seen=0, new_captures=0, error=str(exc))
        print(f"[{feed_id}] outcome=timeout {exc}")
        return Outcome("timeout", feed_id, reason, 1, 0, 0, wrote_row=True,
                       previous_outcome=previous_outcome)
    except fetcher.FetchError as exc:
        finished_at = datetime.now(timezone.utc)
        _safe_record_run(store, feed_id=feed_id, started_at=started_at,
                         finished_at=finished_at, outcome="failed",
                         entries_seen=0, new_captures=0, error=str(exc))
        print(f"[{feed_id}] outcome=failed {exc}")
        return Outcome("failed", feed_id, reason, 1, 0, 0, wrote_row=True,
                       previous_outcome=previous_outcome)

    verdict = fetcher.classify_gdelt_body(resp.status_code, resp.content)
    if verdict != "ok":
        preview = resp.content[:200]
        finished_at = datetime.now(timezone.utc)
        _safe_record_run(store, feed_id=feed_id, started_at=started_at,
                         finished_at=finished_at, outcome="throttled",
                         entries_seen=0, new_captures=0,
                         error=f"gdelt classify={verdict} body[:200]={preview!r}")
        print(f"[{feed_id}] outcome=throttled classify={verdict}")
        return Outcome("throttled", feed_id, reason, 1, 0, 0, wrote_row=True,
                       previous_outcome=previous_outcome)

    try:
        parsed = json_module.loads(resp.content)
    except json_module.JSONDecodeError:
        parsed = {}
    articles = parsed.get("articles", []) if isinstance(parsed, dict) else []

    try:
        new_captures, budget_hit = _capture_articles(feed_id, articles, store)
    except Exception as exc:      # one bad capture must not crash the invocation
        # Whatever just raised (e.g. a real DB error inside record_capture)
        # may have left the connection in an aborted-transaction state —
        # roll back BEFORE record_run tries to write anything, exactly the
        # discipline test_transaction_recovery.py exists to enforce.
        rollback = getattr(store, "rollback", None)
        if rollback is not None:
            rollback()
        finished_at = datetime.now(timezone.utc)
        _safe_record_run(store, feed_id=feed_id, started_at=started_at,
                         finished_at=finished_at, outcome="failed",
                         entries_seen=len(articles), new_captures=0, error=str(exc))
        print(f"[{feed_id}] outcome=failed (during article capture) {exc}")
        return Outcome("failed", feed_id, reason, 1, len(articles), 0, wrote_row=True,
                       previous_outcome=previous_outcome)

    outcome = "budget_exhausted" if budget_hit else "ok"
    finished_at = datetime.now(timezone.utc)
    _safe_record_run(store, feed_id=feed_id, started_at=started_at,
                     finished_at=finished_at, outcome=outcome,
                     entries_seen=len(articles), new_captures=new_captures, error=None)
    print(f"[{feed_id}] entries={len(articles)} new={new_captures} outcome={outcome}")
    return Outcome(outcome, feed_id, reason, 1, len(articles), new_captures, wrote_row=True,
                   previous_outcome=previous_outcome)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report which query would run and why; no requests, no writes")
    args = ap.parse_args()

    common.warn_if_ntfy_unconfigured()
    cfg = config.load_feeds_config()
    conn = common.connect()
    config.sync_feeds(conn, cfg)
    store = DbStore(conn)

    outcome = run(cfg, store, dry_run=args.dry_run)

    # Gated on STATE CHANGE (gdelt_notify_decision), not on every real
    # attempt — see that function's docstring for why (up to ~16 high-
    # priority alerts/night otherwise, one per timer fire).
    decision = gdelt_notify_decision(outcome.previous_outcome, outcome.status)
    if decision is not None:
        title, priority = decision
        if title == "Collector: GDELT throttled":
            message = (f"{outcome.feed_id}: response classified as throttled/unrecognised — "
                      f"cooldown will gate further GDELT queries for {config.GDELT_COOLDOWN_H:g}h.")
            tags = "warning"
        elif title == "Collector: GDELT recovered":
            message = f"{outcome.feed_id}: outcome={outcome.status}, after a prior {outcome.previous_outcome}."
            tags = "white_check_mark"
        else:
            message = f"{outcome.feed_id}: outcome={outcome.status}"
            tags = "warning"
        notified = common.notify(title, message, priority=priority, tags=tags)
        print(f"notified={notified}")

    if not args.dry_run:
        print(f"done: {outcome.new_captures} new capture(s), "
             f"{outcome.requests_issued} request(s) issued, outcome={outcome.status}")
    conn.close()
    return 0 if outcome.status in ("ok", "budget_exhausted", "nothing_to_do",
                                   "skipped_throttled", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
