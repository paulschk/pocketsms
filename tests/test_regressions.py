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
    """An unrendered {placeholder} would show up verbatim in the browser."""
    from pocketsms.webui import page
    html = page()
    for placeholder in ("{name}", "{tagline}", "{version}", "{admin_phone}"):
        assert placeholder not in html
    assert "pocketsms" in html and "<style>" in html
