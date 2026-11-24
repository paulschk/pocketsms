"""Unit tests. No modem, no network, no Docker: just the parts that must not rot.

    python -m pytest -q
"""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

from pocketsms import security
from pocketsms.config import DEFAULTS, Config
from pocketsms.hilink import HiLink, xml_escape
from pocketsms.otp import OtpError, OtpService
from pocketsms.store import Store
from pocketsms.webui import page


class FakeModem:
    """Stands in for the router: records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_sms(self, phone: str, text: str) -> None:
        self.sent.append((phone, text))

    def last_code(self) -> str:
        match = re.search(r"\b(\d{4,8})\b", self.sent[-1][1])
        assert match, f"no code in {self.sent[-1][1]!r}"
        return match.group(1)


@pytest.fixture()
def store(tmp_path):
    instance = Store(str(tmp_path / "test.db"))
    yield instance
    instance.close()


@pytest.fixture()
def cfg():
    config = Config(DEFAULTS)
    config.update({
        "otp_message": "{code}", "otp_length": "6", "otp_ttl": "300",
        "otp_max_attempts": "3", "otp_max_per_hour": "3", "data_dir": "/tmp",
    })
    return config


@pytest.fixture()
def otp(store, cfg):
    return OtpService(FakeModem(), store, cfg)


# --------------------------------------------------------------------------- #
# security
# --------------------------------------------------------------------------- #


def test_password_round_trip():
    stored = security.hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2_sha256$")
    assert security.verify_password("correct horse battery staple", stored)
    assert not security.verify_password("wrong", stored)


def test_password_hash_is_salted():
    assert security.hash_password("same") != security.hash_password("same")


def test_garbage_hash_never_authenticates():
    for broken in ("", "nonsense", "pbkdf2_sha256$1$2", "md5$x$y$z"):
        assert not security.verify_password("same", broken)


@pytest.mark.parametrize("given,expected", [
    ("06 65 42 39 89", "+33665423989"),
    ("0665423989", "+33665423989"),
    ("+33665423989", "+33665423989"),
    ("0033665423989", "+33665423989"),
    ("+33 6 65 42 39 89", "+33665423989"),
    ("0033 6.65.42.39.89", "+33665423989"),
])
def test_phone_normalisation(given, expected):
    assert security.normalise_phone(given) == expected


def test_mask_leaves_enough_to_recognise():
    assert security.mask_phone("+33665423989") == "+336****989"
    assert "65423989" not in security.mask_phone("+33665423989")


def test_codes_are_numeric_and_the_right_length():
    for _ in range(50):
        code = security.new_code(6)
        assert len(code) == 6 and code.isdigit()


# --------------------------------------------------------------------------- #
# modem protocol
# --------------------------------------------------------------------------- #


def test_password_type4_formula_is_the_documented_one():
    """base64(sha256hex(user + base64(sha256hex(password)) + token))."""
    modem = HiLink("http://127.0.0.1", "admin", "secret")
    token = "TOKEN123"
    first = base64.b64encode(hashlib.sha256(b"secret").hexdigest().encode()).decode()
    expected = base64.b64encode(
        hashlib.sha256(f"admin{first}{token}".encode()).hexdigest().encode()).decode()
    assert modem._password_hash(token) == expected


def test_xml_payloads_are_escaped():
    assert xml_escape("a & b <c>") == "a &amp; b &lt;c&gt;"


def test_tag_parsing_tolerates_missing_fields():
    assert HiLink.tag("<a><b>x</b></a>", "b") == "x"
    assert HiLink.tag("<a></a>", "b") == ""


def test_device_entities_are_decoded():
    """The firmware sends a message containing (API) as &#40;API&#41;."""
    payload = "<Content>smsd - gateway OK &#40;API REST&#41;</Content>"
    assert HiLink.tag(payload, "Content") == "smsd - gateway OK (API REST)"


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #


def test_accounts_and_sessions(store):
    store.create_user("paul", "hunter2hunter2", is_admin=True)
    assert store.count_users() == 1
    assert store.check_login("paul", "hunter2hunter2")
    assert store.check_login("paul", "nope") is None

    token = store.create_session(store.user_by_name("paul")["id"])
    assert store.session_user(token)["username"] == "paul"
    store.delete_session(token)
    assert store.session_user(token) is None


def test_api_keys_store_only_a_fingerprint(store):
    plaintext, row = store.create_api_key("grafana", "otp")
    assert plaintext not in str(row)
    assert store.api_key_by_token(plaintext)["scope"] == "otp"
    assert store.api_key_by_token("hkd_not_a_key") is None
    store.revoke_api_key(row["id"])
    assert store.api_key_by_token(plaintext) is None


def test_inbound_messages_are_deduplicated(store):
    first = store.add_message("in", "+33612345678", "hello", device_index="7",
                              device_date="2026-01-01 10:00:00")
    again = store.add_message("in", "+33612345678", "hello", device_index="7",
                              device_date="2026-01-01 10:00:00")
    assert first is not None
    assert again is None, "the same device message must not be stored twice"
    assert store.message_stats()["in"] == 1


def test_boxes_are_separated(store):
    store.add_message("in", "+33600000000", "received")
    store.add_message("out", "+33600000000", "sent")
    assert [m["text"] for m in store.messages("inbox")] == ["received"]
    assert [m["text"] for m in store.messages("sent")] == ["sent"]


# --------------------------------------------------------------------------- #
# two-step verification
# --------------------------------------------------------------------------- #


def test_otp_happy_path(otp):
    started = otp.start("+33665423989", service="caddy")
    code = otp.modem.last_code()
    assert started["phone"] == "+336****989"
    result = otp.verify(challenge=started["challenge"], code=code)
    assert result["verified"] and result["service"] == "caddy"


def test_otp_can_be_verified_by_phone_number(otp):
    otp.start("0665423989")
    code = otp.modem.last_code()
    assert otp.verify(phone="0665423989", code=code)["verified"]


def test_otp_is_single_use(otp):
    started = otp.start("+33665423989")
    code = otp.modem.last_code()
    otp.verify(challenge=started["challenge"], code=code)
    with pytest.raises(OtpError) as caught:
        otp.verify(challenge=started["challenge"], code=code)
    assert caught.value.reason == "already_used"


def test_otp_attempts_are_counted_and_capped(otp):
    started = otp.start("+33665423989")
    for _ in range(3):
        with pytest.raises(OtpError) as caught:
            otp.verify(challenge=started["challenge"], code="000000")
        assert caught.value.reason == "invalid_code"
    with pytest.raises(OtpError) as caught:
        otp.verify(challenge=started["challenge"], code="000000")
    assert caught.value.reason == "too_many_attempts"


def test_otp_expires(otp, store):
    started = otp.start("+33665423989")
    code = otp.modem.last_code()
    with store.lock:
        store.db.execute("UPDATE otp_challenges SET expires_at = '2000-01-01T00:00:00+00:00'"
                         " WHERE id = ?", (started["challenge"],))
        store.db.commit()
    with pytest.raises(OtpError) as caught:
        otp.verify(challenge=started["challenge"], code=code)
    assert caught.value.reason == "expired"


def test_otp_is_rate_limited_per_number(otp):
    for _ in range(3):
        otp.start("+33665423989")
    with pytest.raises(OtpError) as caught:
        otp.start("+33665423989")
    assert caught.value.reason == "rate_limited"
    assert caught.value.status == 429


def test_otp_rejects_an_unusable_number(otp):
    with pytest.raises(OtpError) as caught:
        otp.start("not-a-number")
    assert caught.value.reason == "invalid_phone"


def test_otp_can_use_a_custom_message(store, cfg):
    cfg["otp_message"] = "Your {service} code is {code} ({minutes} min)"
    service = OtpService(FakeModem(), store, cfg)
    service.start("+33665423989", service="grafana")
    assert service.modem.sent[-1][1].startswith("Your grafana code is ")


# --------------------------------------------------------------------------- #
# web interface
# --------------------------------------------------------------------------- #


def test_the_page_renders():
    """A stray brace in the page's CSS or JS would raise here, not in production."""
    html = page()
    assert html.startswith("<!DOCTYPE html>")
    for fragment in ("<script>", "login-form", "otp-verify", "api/v1/messages"):
        assert fragment in html
