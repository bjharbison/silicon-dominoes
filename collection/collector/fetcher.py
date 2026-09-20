"""Silicon Dominoes collector — the one bounded HTTP fetcher.

Every outbound network call anywhere in this package goes through fetch()
(or its get()/post() shorthands): feed fetch, article fetch, Wayback
submission, ntfy notify, verify_watch, snapshot_retry, poll_gdelt, and the
LLM call in extract.py. feedparser must never fetch a URL itself — fetch
bytes through here, then feedparser.parse(bytes).

This exists because of 2026-09-19: sd-rss hung 18 hours on one feed host
that accepted a TLS connection and never sent a byte. No call anywhere had
a timeout. A read timeout alone would not have caught it either — a server
that trickles one byte every few seconds keeps resetting a per-read timeout
forever without ever tripping it (this applies just as much to the wait for
a response's status line and headers as it does to the body: readline()
loops the same way read(amt) does). FETCH_DEADLINE is the wall-clock
backstop that covers the *entire* call — connect, the wait for headers, and
the body — regardless of what any individual socket operation is doing.

Mechanism: the actual synchronous call (connect + headers + full body read)
runs inside a daemon thread; fetch() itself does nothing but start that
thread and wait on it with a hard timeout of `deadline`. If the thread
hasn't finished by then, fetch() raises FetchTimeout and returns — the
caller is never blocked past `deadline` no matter what the socket is doing.
The abandoned thread is not force-killed (Python has no safe way to do
that); it either finishes on its own once its own connect/read timeout
trips, or in the fully pathological case (a peer trickling bytes forever,
each one arriving just under the per-op timeout) it never does and leaks
for the remainder of the process's life. That's an acceptable trade here:
every collector entry point is a `Type=oneshot` systemd unit that exits
right after its `main()` returns, the thread is daemonized so it can never
block that exit, and each per-op timeout still bounds any individual
socket read regardless.

An alternative considered was a threading.Timer that shuts down the
response's underlying socket at the deadline. It was not used because
reaching the raw socket means poking at private attributes several layers
deep in urllib3/http.client (resp.raw._fp.fp...), which differ between
urllib3 1.x and 2.x and would make this module fragile to a routine
dependency bump. The watchdog-thread approach only uses the public
requests API and gives the same guarantee to the caller.

Callers pass explicit connect_timeout / read_timeout / deadline / max_bytes
to override the collection.collector.config defaults for a specific call
(e.g. extract.py gives the LLM call a much longer read_timeout and deadline,
since a single blocking generation is not the trickle scenario the defaults
guard against). Config values are read fresh on every call, never cached at
import time, so tests can override them by patching the config module or by
passing kwargs directly.
"""
from __future__ import annotations

import json as json_module
import threading
from typing import Any

import requests

from . import config

CHUNK_SIZE = 65536  # 64 KiB — ordinary streaming chunk size, for throughput.


class FetchError(Exception):
    """Base class for bounded-fetch failures. Never let requests' own
    exception types leak past this module — callers only need to catch
    this (or the two subclasses below)."""


class FetchTimeout(FetchError):
    """Connect timeout, read timeout, or total wall-clock deadline exceeded."""


class FetchTooLarge(FetchError):
    """Response body exceeded the configured byte ceiling."""


class FetchResult:
    def __init__(self, *, status_code: int, content: bytes, url: str,
                 headers: dict, ok: bool) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = headers
        self.ok = ok

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json_module.loads(self.content)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise FetchError(f"HTTP {self.status_code} for {self.url}")


def fetch(url: str, *, method: str = "GET", headers: dict | None = None,
          params: dict | None = None, data: bytes | None = None,
          json_body: Any = None, allow_redirects: bool = True,
          connect_timeout: float | None = None,
          read_timeout: float | None = None,
          deadline: float | None = None,
          max_bytes: int | None = None,
          session: requests.Session | None = None) -> FetchResult:
    """The one bounded HTTP call. Raises FetchTimeout or FetchTooLarge on
    the conditions those names describe, or FetchError for anything else
    requests couldn't complete. `deadline` bounds the WHOLE call — connect,
    waiting for the response headers, and reading the body — never just
    part of it; see the module docstring for the mechanism and why."""
    connect_timeout = config.HTTP_CONNECT_TIMEOUT if connect_timeout is None else connect_timeout
    read_timeout = config.HTTP_READ_TIMEOUT if read_timeout is None else read_timeout
    deadline = config.FETCH_DEADLINE if deadline is None else deadline
    max_bytes = config.FETCH_MAX_BYTES if max_bytes is None else max_bytes

    box: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            box["result"] = _do_fetch(
                url, method, headers, params, data, json_body, allow_redirects,
                connect_timeout, read_timeout, max_bytes, session)
        except Exception as exc:  # noqa: BLE001 — handed back to the caller's thread
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True, name="sd-fetch").start()
    if not done.wait(deadline):
        raise FetchTimeout(
            f"{method} {url}: total deadline of {deadline}s exceeded "
            "(connect, header wait, and body read all count against it)")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _do_fetch(url: str, method: str, headers: dict | None, params: dict | None,
              data: bytes | None, json_body: Any, allow_redirects: bool,
              connect_timeout: float, read_timeout: float, max_bytes: int,
              session: requests.Session | None) -> FetchResult:
    """The actual synchronous request, run inside fetch()'s watchdog thread.
    Whatever this blocks on — DNS, TCP connect, TLS handshake, the wait for
    a status line, or streaming the body — fetch() bounds from the outside
    with `deadline`; this function is free to use ordinary chunk sizes."""
    sess = session or requests
    try:
        resp = sess.request(
            method, url, headers=headers, params=params, data=data,
            json=json_body, allow_redirects=allow_redirects,
            timeout=(connect_timeout, read_timeout), stream=True,
        )
    except requests.exceptions.Timeout as exc:
        raise FetchTimeout(f"{method} {url}: connect/read timeout: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"{method} {url}: {exc}") from exc

    try:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise FetchTooLarge(f"{method} {url}: exceeded {max_bytes} byte cap")
            chunks.append(chunk)
    except requests.exceptions.Timeout as exc:
        raise FetchTimeout(f"{method} {url}: read timeout while streaming: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"{method} {url}: {exc}") from exc
    finally:
        resp.close()

    return FetchResult(status_code=resp.status_code, content=b"".join(chunks),
                        url=resp.url, headers=dict(resp.headers), ok=resp.ok)


def get(url: str, **kwargs: Any) -> FetchResult:
    return fetch(url, method="GET", **kwargs)


def post(url: str, **kwargs: Any) -> FetchResult:
    return fetch(url, method="POST", **kwargs)
