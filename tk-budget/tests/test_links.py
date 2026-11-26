import http.client
import logging
import socket

import pytest

from tk_budget.links import LinkServer

URLS = {
    ("kid-a", "pc1"): "vnc://pc1.lan:5900",
    ("kid-a", "pc2"): "rdp://full%20address=s:pc2.lan",
    ("kid-a", "web"): "http://pc3.lan/",
    ("kid-a", "evil"): "vnc://pc1.lan\r\nSet-Cookie: x=1",
}


@pytest.fixture(scope="module")
def running():
    calls = []

    def lookup(kid, host):
        calls.append((kid, host))
        return URLS.get((kid, host))

    srv = LinkServer(lookup, 0)
    srv.start()
    yield srv, calls
    srv.stop()


@pytest.fixture
def server(running):
    running[1].clear()
    return running


def request(srv, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        resp.read()
        return resp.status, resp.getheader("Location")
    finally:
        conn.close()


def test_redirect_for_known_pair(server, caplog):
    srv, calls = server
    caplog.set_level(logging.INFO, logger="tk_budget.links")
    assert request(srv, "/connect/kid-a/pc1") == (302, "vnc://pc1.lan:5900")
    assert request(srv, "/connect/kid-a/pc2") == (302, "rdp://full%20address=s:pc2.lan")
    assert calls == [("kid-a", "pc1"), ("kid-a", "pc2")]
    assert len([r for r in caplog.records if "redirecting" in r.getMessage()]) == 2


def test_unknown_pair_404(server):
    srv, _ = server
    assert request(srv, "/connect/kid-a/nope")[0] == 404


@pytest.mark.parametrize("path", [
    "/connect/kid-a/../pc1", "/connect/../kid-a/pc1", "/connect/kid-a%2Fpc1", "/connect/kid-a/pc1%2F",
    "/connect/kid-a/pc%31", "/connect/kid-a/pc1?x=1", "/connect/kid-a/pc1/", "/connect/kid-a",
    "/connect/Kid-a/pc1", "/connect/_x/pc1", "/", "/other/kid-a/pc1", "/connect/kid-a/pc1#x",
    "http://evil/connect/kid-a/pc1", "/connect/kid-a/pc1;x",
])
def test_bad_paths_404_without_lookup(server, path):
    srv, calls = server
    assert request(srv, path)[0] == 404
    assert calls == []


def test_only_rdp_and_vnc_redirected(server):
    srv, _ = server
    assert request(srv, "/connect/kid-a/web") == (404, None)
    assert request(srv, "/connect/kid-a/evil") == (404, None)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
def test_other_methods_404(server, method):
    srv, calls = server
    assert request(srv, "/connect/kid-a/pc1", method)[0] == 404
    assert calls == []


def test_survives_malformed_request_line(server):
    srv, _ = server
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as s:
        s.sendall(b"\x00\xffgarbage request line\r\n\r\n")
        s.recv(1024)
    assert request(srv, "/connect/kid-a/pc1")[0] == 302


def test_lookup_error_is_404():
    def boom(kid, host):
        raise RuntimeError("x")
    srv = LinkServer(boom, 0)
    srv.start()
    try:
        assert request(srv, "/connect/kid-a/pc1")[0] == 404
    finally:
        srv.stop()
