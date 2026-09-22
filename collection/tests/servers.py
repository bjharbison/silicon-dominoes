"""Stub HTTP servers for the bounded-fetch tests. No Postgres, no external
network — every server binds 127.0.0.1 on an ephemeral port and runs in a
daemon thread so a leaked one can never block the test process from exiting.

Not a test module itself (no test*.py name), so unittest discover skips it.
"""
from __future__ import annotations

import gzip
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class RawSocketServer:
    """A TCP server that hands each connection to `handler(conn)` and does
    nothing else — used for the black-hole and trickle cases, where we need
    control below the level BaseHTTPRequestHandler gives us (specifically:
    the ability to accept a connection and never write anything back)."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._conns: list[socket.socket] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._conns.append(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            self._handler(conn)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        # Closes the listening socket AND every connection handed to a
        # handler so far, so a black-hole/trickle handler still sleeping in
        # a daemon thread doesn't leave an open socket for the GC to warn
        # about after the test has moved on.
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
        with self._lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass


def _black_hole_handler(conn: socket.socket) -> None:
    # Accept and never read or write anything — the 2026-09-19 incident:
    # a feed host that accepted the TLS/TCP connection and then just sat
    # there. Sleep long enough to outlast any test's deadline.
    time.sleep(30)


def black_hole_server() -> RawSocketServer:
    return RawSocketServer(_black_hole_handler)


def _trickle_handler(conn: socket.socket) -> None:
    # Drain whatever the client sent (best effort; not required for the
    # test), then send valid headers immediately followed by one byte per
    # second forever. This is the case a read timeout alone cannot catch:
    # each individual read/write of the connection succeeds quickly, so a
    # per-read timeout never trips. Only a wall-clock deadline checked
    # across the whole streaming read does.
    try:
        conn.settimeout(1)
        conn.recv(65536)
    except OSError:
        pass
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
    for _ in range(60):
        conn.sendall(b"x")
        time.sleep(1)


def trickle_server() -> RawSocketServer:
    return RawSocketServer(_trickle_handler)


def _header_trickle_handler(conn: socket.socket) -> None:
    # Drain the request (best effort), then send the status line one byte
    # every 0.5s and never reach the blank line that terminates the header
    # block — the response never finishes arriving, even though every
    # individual byte shows up promptly. This is the "wait for headers"
    # half of the whole-call deadline: a per-op read timeout does not catch
    # it for the same reason it does not catch a trickled body.
    try:
        conn.settimeout(1)
        conn.recv(65536)
    except OSError:
        pass
    try:
        for byte in b"HTTP/1.1 200 OK\r\n":
            conn.sendall(bytes([byte]))
            time.sleep(0.5)
        while True:
            conn.sendall(b"X")
            time.sleep(0.5)
    except OSError:
        pass


def header_trickle_server() -> RawSocketServer:
    return RawSocketServer(_header_trickle_handler)


class _QuietHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        # The oversized-response test deliberately aborts mid-stream once
        # max_bytes is exceeded, which can reset a still-open keep-alive
        # connection this server is about to read the next request from —
        # expected given the test, not a real error.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def _start(handler_cls) -> _QuietHTTPServer:
    server = _QuietHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}/"
    return server


def gdelt_stub_server(articles: list | None = None) -> _QuietHTTPServer:
    """Records the full request path (including query string) of the most
    recent GET in `server.last_path`, and responds with a minimal valid
    GDELT DOC 2.0 JSON body — {"articles": articles or []} — so collector.
    fetcher.classify_gdelt_body reads it as 'ok'. Used to prove the real
    (non-injected) fetch path — collector.fetcher.get, exercised through
    poll_gdelt.py's default fetch_query — actually sends the query
    parameters poll_gdelt._gdelt_params builds, since every other gdelt
    test injects a fake fetch_query and never touches a socket at all."""
    body = json.dumps({"articles": articles or []}).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.server.last_path = self.path
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    server = _start(Handler)
    server.last_path = None
    return server


def notify_stub_server(status: int = 200) -> _QuietHTTPServer:
    """Records the headers/body of every POST received and responds with
    `status`; used to test common.notify(). `server.hit_count` counts
    requests, `server.last_headers` / `server.last_body` hold the most
    recent one."""
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            self.server.hit_count += 1
            self.server.last_headers = dict(self.headers)
            self.server.last_body = body
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    server = _start(Handler)
    server.hit_count = 0
    server.last_headers = None
    server.last_body = None
    return server


def oversized_server(size_bytes: int) -> _QuietHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = b"a" * size_bytes
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    return _start(Handler)


def gzip_server(payload: bytes) -> _QuietHTTPServer:
    """Serves `payload` gzip-compressed with Content-Encoding: gzip, to
    confirm the fetcher hands callers the decoded bytes, not the raw
    compressed stream."""
    compressed = gzip.compress(payload)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)

        def log_message(self, *_args) -> None:
            pass

    return _start(Handler)


def delayed_response_server(delay: float, body: bytes,
                            content_type: str = "text/plain") -> _QuietHTTPServer:
    """Responds normally (status + headers + body) but only after sleeping
    `delay` seconds first — used to prove a large deadline/read_timeout
    override lets a legitimately slow call still succeed."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._respond()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            self._respond()

        def _respond(self) -> None:
            if delay:
                time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    return _start(Handler)


def llm_stub_server(delay: float, content: str) -> _QuietHTTPServer:
    """Mimics the OpenAI-compatible chat/completions endpoint extract.py's
    call_llm() posts to: waits `delay` seconds, then returns a completion
    whose message content is `content` (already a JSON string, per the
    protocol — call_llm() parses it as the actual candidate payload)."""
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
    return delayed_response_server(delay, body, content_type="application/json")


RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>stub feed</title>
{items}
</channel></rss>"""

ITEM_TEMPLATE = """<item><title>{title}</title><link>{link}</link>
<pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate>
<description>stub</description></item>"""


def healthy_feed_server(n_articles: int = 2, article_delay: float = 0.0) -> _QuietHTTPServer:
    """Serves a feed at /feed whose entries link to /article/<i> on the same
    server; each article responds with small text after `article_delay`
    seconds (0 by default — used with a delay by the per-feed-budget test)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.path == "/feed":
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                items = "\n".join(
                    ITEM_TEMPLATE.format(title=f"article {i}", link=f"{base}/article/{i}")
                    for i in range(n_articles)
                )
                body = RSS_TEMPLATE.format(items=items).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/article/"):
                if article_delay:
                    time.sleep(article_delay)
                body = f"article body {self.path}".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    return _start(Handler)
