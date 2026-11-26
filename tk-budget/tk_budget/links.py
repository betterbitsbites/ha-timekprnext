"""Tiny HTTP redirect server for remote-connect links on the dashboard.

HA's markdown card drops `vnc://`/`rdp://` hrefs, so the dashboard links to
`http://<ha-host>:<link_port>/connect/<kid>/<host>` and this server answers with a 302 to the
connect URL the agent published for that kid on that host. Unauthenticated and LAN only: it serves
nothing but redirects to `rdp`/`vnc` URLs; everything else is a 404.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

Lookup = Callable[[str, str], "str | None"]

_PATH_RE = re.compile(r"^/connect/([a-z0-9][a-z0-9_-]*)/([a-z0-9][a-z0-9_-]*)$")
SCHEMES = ("rdp", "vnc")


def _redirect_target(lookup: Lookup, raw_path: str) -> str | None:
    """The URL to redirect to, or None for a 404."""
    parts = urllib.parse.urlsplit(raw_path)
    if parts.scheme or parts.netloc or parts.query or parts.fragment or parts.path != raw_path:
        return None
    m = _PATH_RE.match(parts.path)
    if m is None:
        return None
    kid, host = m.groups()
    try:
        url = lookup(kid, host)
    except Exception:
        log.exception("connect lookup failed for %s on %s", kid, host)
        return None
    if not url or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return None
    if not url.isascii() or urllib.parse.urlsplit(url).scheme not in SCHEMES:
        return None
    log.info("redirecting %s on %s to its %s link", kid, host, urllib.parse.urlsplit(url).scheme)
    return url


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"
    timeout = 10  # seconds; drop clients that stall mid-request

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass  # quiet: one INFO line per redirect is logged by _redirect_target

    def _not_found(self) -> None:
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        url = _redirect_target(self.server.lookup, self.path)
        if url is None:
            self._not_found()
            return
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self._not_found()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD  # noqa: N815


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], lookup: Lookup) -> None:
        self.lookup = lookup
        super().__init__(addr, _Handler)


class LinkServer:
    def __init__(self, lookup: Lookup, port: int) -> None:
        self._lookup = lookup
        self._port = port
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """The bound port (useful with port 0)."""
        return self._server.server_address[1] if self._server else self._port

    def start(self) -> None:
        self._server = _Server(("0.0.0.0", self._port), self._lookup)
        self._thread = threading.Thread(target=self._server.serve_forever, name="tk-links", daemon=True)
        self._thread.start()
        log.info("remote-connect links served on port %s", self.port)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
