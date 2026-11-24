"""Regression tests for the bugs that only a live run reveals.

Each test here exists because the corresponding behaviour was actually wrong at
some point: a config file that was silently ignored, an API key that could not
use the scope it was named after, and a page whose CSS braces broke rendering.
"""

from __future__ import annotations

import pytest

from pocketsms.config import Config
from pocketsms.server import SCOPE_FULL, Identity


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #


def test_config_file_keys_are_case_insensitive(tmp_path, monkeypatch):
    """The file uses UPPERCASE (like the environment); the defaults are lowercase."""
    monkeypatch.setenv("POCKETSMS_DATA_DIR", str(tmp_path))
    for name in ("BIND", "POCKETSMS_BIND", "PORT", "POCKETSMS_PORT"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "pocketsms.conf"
    path.write_text("# a comment\nBIND=127.0.0.1\nPORT=9999\notp_ttl=120\n\n")

    cfg = Config.load(str(path))
    assert cfg["bind"] == "127.0.0.1"
    assert cfg["port"] == "9999"
    assert cfg["otp_ttl"] == "120"
    assert cfg.int("otp_ttl") == 120


def test_environment_beats_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("POCKETSMS_DATA_DIR", str(tmp_path))
    path = tmp_path / "pocketsms.conf"
    path.write_text("BIND=127.0.0.1\n")
    monkeypatch.setenv("POCKETSMS_BIND", "10.0.0.1")
    monkeypatch.setenv("PORT", "1234")

    cfg = Config.load(str(path))
    assert cfg["bind"] == "10.0.0.1", "POCKETSMS_BIND must win over the file"
    assert cfg["port"] == "1234", "the bare spelling must work too"


def test_an_unreadable_config_file_is_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("POCKETSMS_DATA_DIR", str(tmp_path))
    path = tmp_path / "pocketsms.conf"
    path.write_text("BIND=127.0.0.1\n")
    path.chmod(0o000)
    try:
        cfg = Config.load(str(path))          # must not raise
        assert cfg["bind"] == "0.0.0.0"       # falls back to the default
        assert "cannot read" in capsys.readouterr().err
    finally:
        path.chmod(0o600)


# --------------------------------------------------------------------------- #
# API key scopes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key_scope,needed,expected", [
    ("sms", "sms", True),
    ("sms", "otp", False),
    ("sms", "admin", False),
    ("otp", "otp", True),
    ("otp", "sms", False),
    ("otp", "admin", False),
    ("full", "sms", True),
    ("full", "otp", True),
    ("full", "admin", True),
])
def test_api_key_scope_matrix(key_scope, needed, expected):
    assert Identity("key", "test", key_scope).allows(needed) is expected


def test_an_anonymous_caller_is_allowed_nothing():
    for scope in ("sms", "otp", "admin"):
        assert Identity("anonymous").allows(scope) is False


def test_a_signed_in_user_is_not_an_admin_unless_flagged():
    user = Identity("user", "paul", SCOPE_FULL, user_id=1, is_admin=False)
    assert user.allows("sms") and user.allows("otp")
    assert not user.allows("admin")
    admin = Identity("user", "root", SCOPE_FULL, user_id=2, is_admin=True)
    assert admin.allows("admin")


# --------------------------------------------------------------------------- #
# the web page
# --------------------------------------------------------------------------- #


def test_the_page_renders_all_its_placeholders():
    """An unrendered token would show up verbatim in the browser."""
    from pocketsms.webui import page
    html = page()
    for token in ("__NAME__", "__TAGLINE__", "__VERSION__"):
        assert token not in html
    assert "pocketsms" in html and "<style>" in html


def test_a_post_body_is_drained_even_when_the_endpoint_ignores_it(tmp_path):
    """The bug: an unread body corrupts the next request on a keep-alive socket.

    Signing out answers 200, the browser then reloads on the same connection,
    and the leftover ``{}`` made the next request line read ``{}GET / HTTP/1.1``
    - a 501 right after a successful call.
    """
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    from pocketsms.config import DEFAULTS, Config
    from pocketsms.hilink import HiLink
    from pocketsms.otp import OtpService
    from pocketsms.server import Handler
    from pocketsms.store import Store

    config = Config(DEFAULTS)
    config.update({"data_dir": str(tmp_path), "bind": "127.0.0.1", "port": "0"})
    store = Store(str(tmp_path / "t.db"))
    modem = HiLink("http://127.0.0.1:1", "admin", "admin", timeout=1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.app = server_app = __import__("pocketsms.server", fromlist=["App"]).App(
        config, store, modem, OtpService(modem, store, config))
    server.store = store
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        # A POST that returns early (401: no credentials) and never reads the body.
        connection.request("POST", "/api/v1/auth/logout", body=b"{}",
                           headers={"Content-Type": "application/json"})
        first = connection.getresponse()
        first.read()
        assert first.status == 401
        # Same connection, next request: this is where the framing used to break.
        connection.request("GET", "/api/v1/health")
        second = connection.getresponse()
        body = second.read()
        assert second.status == 200, f"framing broken: {second.status} {body!r}"
        assert b'"ok"' in body
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_head_and_options_never_answer_501(tmp_path):
    """Monitoring tools send HEAD; a bare 501 is not a useful answer."""
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    from pocketsms.config import DEFAULTS, Config
    from pocketsms.hilink import HiLink
    from pocketsms.otp import OtpService
    from pocketsms.server import App, Handler
    from pocketsms.store import Store

    config = Config(DEFAULTS)
    config.update({"data_dir": str(tmp_path), "bind": "127.0.0.1", "port": "0"})
    store = Store(str(tmp_path / "h.db"))
    modem = HiLink("http://127.0.0.1:1", "admin", "admin", timeout=1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.app = App(config, store, modem, OtpService(modem, store, config))
    server.store = store
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("HEAD", "/")
        head = connection.getresponse()
        head.read()
        assert head.status == 200, head.status
        connection.request("OPTIONS", "/api/v1/status")
        options = connection.getresponse()
        options.read()
        assert options.status == 204, options.status
        assert "HEAD" in options.getheader("Allow", "")
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        store.close()
