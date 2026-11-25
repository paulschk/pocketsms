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


# --------------------------------------------------------------------------- #
# the dashboard said "received" for messages that were sent
# --------------------------------------------------------------------------- #

def test_a_listed_message_says_which_way_it_went(tmp_path):
    """The page tells sent from received by `direction`; the query never selected
    it, so every row fell through to "received" and sent messages read as
    received."""
    from pocketsms.store import Store

    store = Store(str(tmp_path / "d.db"))
    try:
        store.add_message("out", "+33612345678", "going out")
        store.add_message("in", "+33612345678", "coming in")

        sent = store.messages("sent")[0]
        received = store.messages("inbox")[0]
        assert sent["direction"] == "out", "the API omitted the direction field"
        assert received["direction"] == "in"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# line monitoring
# --------------------------------------------------------------------------- #

def _app(tmp_path, **overrides):
    from pocketsms.config import DEFAULTS, Config
    from pocketsms.hilink import HiLink
    from pocketsms.otp import OtpService
    from pocketsms.server import App
    from pocketsms.store import Store

    config = Config(DEFAULTS)
    config.update({"data_dir": str(tmp_path), "bind": "127.0.0.1", "port": "0"}, **overrides)
    store = Store(str(tmp_path / "app.db"))
    modem = _RecordingModem()
    return App(config, store, modem, OtpService(modem, store, config)), store, modem


class _RecordingModem:
    """Stands in for the HiLink client: records sends, reports a device."""

    base = "http://127.0.0.1:1"

    def __init__(self):
        self.sent = []

    def send_sms(self, phone, text):
        self.sent.append((phone, text))

    def snapshot(self):
        return {"information": {"DeviceName": "E5573Bs-320"}, "status": {}, "operator": "Orange F"}

    def information(self):
        return {"DeviceName": "E5573Bs-320"}

    def operator(self):
        return "Orange F"

    def login(self):
        return None


def test_the_owner_phone_is_where_the_ui_puts_it(tmp_path):
    app, store, _ = _app(tmp_path)
    try:
        assert app.setting("line_alert_phone") == ""          # nothing configured
        assert app.setting("line_alert_cooldown") == "1800"   # falls back to the default

        app.save_settings({"line_alert_phone": "0665423989", "line_alert_cooldown": "600"})
        assert app.setting("line_alert_phone") == "+33665423989"   # normalised
        assert app.setting("line_alert_cooldown") == "600"

        # It survives a restart, because it is not held in memory.
        assert store.setting("line_alert_phone") == "+33665423989"
    finally:
        store.close()


def test_settings_reject_typos_and_shouting(tmp_path):
    from pocketsms.server import ApiError

    app, store, _ = _app(tmp_path)
    try:
        for payload in ({"line_alert_phnone": "0665423989"},      # a typo, not a no-op
                        {"line_alert_cooldown": "5"},              # below the floor
                        {"line_alert_cooldown": "soon"}):
            try:
                app.save_settings(payload)
            except ApiError as exc:
                assert exc.status == 400
            else:
                raise AssertionError(f"accepted {payload!r}")
    finally:
        store.close()


def test_a_dropped_line_texts_the_owner_once(tmp_path):
    app, store, modem = _app(tmp_path)
    try:
        app.save_settings({"line_alert_phone": "0665423989"})

        first = app.report_line("down", "no route to the internet")
        assert first["notified"] is True
        assert len(modem.sent) == 1
        number, text = modem.sent[0]
        assert number == "+33665423989"
        assert "tombee" in text                    # the configured template was used

        # A repeat report inside the cooldown must not send a second SMS.
        again = app.report_line("down", "still down")
        assert again["notified"] is False
        assert len(modem.sent) == 1

        # Recovery is announced, and only because we had been down.
        back = app.report_line("up", "route restored")
        assert back["notified"] is True and len(modem.sent) == 2
        assert "revenue" in modem.sent[1][1]

        quiet = app.report_line("up", "still up")
        assert quiet["notified"] is False and len(modem.sent) == 2

        # And the state is visible to the dashboard.
        assert app.line_state()["state"] == "up"
    finally:
        store.close()


def test_a_line_alert_with_no_number_configured_is_silent(tmp_path):
    app, store, modem = _app(tmp_path)
    try:
        result = app.report_line("down", "cable unplugged")
        assert result["notified"] is False
        assert "no owner phone" in result["reason"]
        assert modem.sent == []
    finally:
        store.close()


def test_the_test_button_sends_what_a_real_alert_looks_like(tmp_path):
    from pocketsms.server import ApiError

    app, store, modem = _app(tmp_path)
    try:
        try:
            app.test_line_alert()
        except ApiError as exc:
            assert exc.reason == "no_owner_phone"
        else:
            raise AssertionError("sent a test with no number configured")

        app.save_settings({"line_alert_phone": "0665423989"})
        result = app.test_line_alert()
        assert result["to"] == "+33665423989"
        assert modem.sent[0][1].startswith("[test] "), "a test must not look like an outage"
        # A test must not pretend the line is down.
        assert app.line_state()["state"] == "unknown"
    finally:
        store.close()


def test_a_quiet_report_syncs_the_state_without_texting(tmp_path):
    """The watcher announces its starting state on every boot; that must not
    cost an SMS, but the dashboard must stop showing a stale outage."""
    app, store, modem = _app(tmp_path)
    try:
        app.save_settings({"line_alert_phone": "0665423989"})
        app.report_line("down")                      # a real outage, announced
        assert len(modem.sent) == 1

        result = app.report_line("up", "watcher started", quiet=True)
        assert result["notified"] is False
        assert len(modem.sent) == 1                  # nothing sent
        assert app.line_state()["state"] == "up"     # but the state moved
    finally:
        store.close()


def test_the_mode_says_whether_anything_is_watching_the_line(tmp_path):
    """SMS-only or failover is derived from the watcher reporting, not from a flag
    somebody set once and forgot."""
    app, store, modem = _app(tmp_path)
    try:
        # Nothing has ever reported: this is an SMS gateway and nothing more.
        assert app.line_state()["mode"] == "sms-only"
        assert app.line_state()["watcher"]["armed"] is False

        # A heartbeat from the watcher arms the failover, quietly.
        app.report_line("up", "watcher alive", quiet=True, watching="eth0",
                        fallback="eth1", every=300)
        state = app.line_state()
        assert state["mode"] == "failover"
        assert state["watcher"]["armed"] is True
        assert state["watcher"]["watching"] == "eth0"
        assert state["watcher"]["fallback"] == "eth1"
        assert modem.sent == [], "a heartbeat must never text anybody"

        # Three missed heartbeats and it stops claiming to be armed.
        store.save_settings({"_line_seen_at": "2020-01-01 00:00:00"})
        assert app.line_state()["mode"] == "sms-only"
    finally:
        store.close()


def test_a_stale_heartbeat_interval_is_ignored(tmp_path):
    """A nonsense `every` from a hand-written payload must not arm anything."""
    app, store, _ = _app(tmp_path)
    try:
        app.report_line("up", "hello", quiet=True, every="not-a-number")
        assert app.line_state()["watcher"]["every"] == 300
        app.report_line("up", "hello", quiet=True, every=5)
        assert app.line_state()["watcher"]["every"] == 30      # floored, not trusted
    finally:
        store.close()


def test_the_declared_version_matches_the_package():
    """Two files carry the version; a release tagged from one and built from the
    other is how you ship an image whose /api/v1/health lies."""
    import re
    import pathlib

    import pocketsms

    root = pathlib.Path(__file__).resolve().parent.parent
    declared = re.search(r'^version\s*=\s*"([^"]+)"',
                         (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert declared, "no version declared in pyproject.toml"
    assert declared.group(1) == pocketsms.__version__
