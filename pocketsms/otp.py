"""Two-step verification over SMS, for other services.

The point of this module: any service you run (a reverse proxy, a dashboard, a
runbook script) can ask pocketsms to (1) send a code to a phone number and (2)
verify the code the user typed. The code is never stored in plaintext, is
single-use, expires, and is rate-limited per phone number.

Integration in words: your service requests a code for a phone number, shows a
"type the code" form, then posts what the user typed to ``/api/v1/otp/verify``.
A 200 means verified. The README has a Caddy example.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from .security import mask_phone, new_code, new_token, normalise_phone
from .store import now

log = logging.getLogger("pocketsms.otp")


class OtpError(RuntimeError):
    """Carries an HTTP status so the API layer stays a thin wrapper."""

    def __init__(self, reason: str, status: int = 400, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.status = status
        self.detail = detail


def code_digest(challenge_id: str, code: str) -> str:
    """Bind the code to its challenge: a leaked digest is useless elsewhere."""
    return hashlib.sha256(f"{challenge_id}:{code}".encode()).hexdigest()


class OtpService:
    def __init__(self, modem, store, cfg) -> None:
        self.modem = modem
        self.store = store
        self.cfg = cfg

    # -- send a code -------------------------------------------------------- #

    def start(self, phone: str, *, service: str = "", purpose: str = "",
              message: str | None = None) -> dict:
        number = normalise_phone(phone)
        if not number.startswith("+") or len(number) < 8:
            raise OtpError("invalid_phone", 400, f"{phone!r} is not a usable number")

        allowed = self.cfg.int("otp_max_per_hour")
        recent = self.store.requests_last_hour(number)
        if recent >= allowed:
            raise OtpError("rate_limited", 429,
                           f"{recent} codes already requested for this number this hour")

        digits = self.cfg.int("otp_length")
        ttl = self.cfg.int("otp_ttl")
        code = new_code(digits)
        challenge_id = new_token(12)
        self.store.create_challenge(
            challenge_id, number, service[:64], purpose[:128],
            code_digest(challenge_id, code), ttl, self.cfg.int("otp_max_attempts"))

        body = (message or self.cfg["otp_message"]).format(
            code=code, service=service or "acces", purpose=purpose or "",
            minutes=max(1, ttl // 60), digits=digits)
        try:
            self.modem.send_sms(number, body)
        except Exception as exc:  # noqa: BLE001 - never leave a challenge nobody can use
            self.store.drop_challenge(challenge_id)
            log.warning("OTP send to %s failed: %s", mask_phone(number), exc)
            raise OtpError("send_failed", 502, str(exc)) from exc

        log.info("challenge %s sent to %s (%s)", challenge_id[:6], mask_phone(number),
                 service or "no service")
        return {
            "challenge": challenge_id,
            "phone": mask_phone(number),
            "service": service,
            "digits": digits,
            "expires_in": ttl,
        }

    # -- check a code ------------------------------------------------------- #

    def verify(self, *, challenge: str = "", phone: str = "", code: str = "") -> dict:
        code = (code or "").strip()
        if not code:
            raise OtpError("missing_code", 400)

        record = (self.store.challenge(challenge) if challenge
                  else self.store.latest_open_challenge(normalise_phone(phone)))
        if not record:
            raise OtpError("unknown_challenge", 404)
        if record["consumed_at"]:
            raise OtpError("already_used", 409)
        if record["expires_at"] < now():
            raise OtpError("expired", 410)
        if record["attempts"] >= record["max_attempts"]:
            raise OtpError("too_many_attempts", 429)

        if not secrets.compare_digest(code_digest(record["id"], code), record["code_hash"]):
            attempts = self.store.bump_attempts(record["id"])
            remaining = max(0, record["max_attempts"] - attempts)
            log.info("challenge %s rejected, %d attempt(s) left", record["id"][:6], remaining)
            raise OtpError("invalid_code", 401, f"{remaining} attempt(s) left")

        self.store.consume_challenge(record["id"])
        log.info("challenge %s verified for %s", record["id"][:6], mask_phone(record["phone"]))
        return {
            "verified": True,
            "challenge": record["id"],
            "phone": mask_phone(record["phone"]),
            "service": record["service"],
            "purpose": record["purpose"],
        }

    def status(self, challenge_id: str) -> dict:
        record = self.store.challenge(challenge_id)
        if not record:
            raise OtpError("unknown_challenge", 404)
        return {
            "challenge": record["id"],
            "phone": mask_phone(record["phone"]),
            "service": record["service"],
            "attempts": record["attempts"],
            "max_attempts": record["max_attempts"],
            "expires_at": record["expires_at"],
            "consumed": bool(record["consumed_at"]),
            "expired": record["expires_at"] < now(),
        }
