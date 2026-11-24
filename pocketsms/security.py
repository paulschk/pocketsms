"""Password hashing, opaque tokens and one-time codes.

Standard library only: PBKDF2-HMAC-SHA256 for passwords, ``secrets`` for
anything that must not be predictable. Nothing here is clever, which is the
point - it is short enough to read in one sitting.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

PBKDF2_ROUNDS = 260_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    """Return ``pbkdf2_sha256$rounds$salt$hash`` - self-describing, so rounds can change."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return "$".join((_ALGO, str(rounds),
                     base64.b64encode(salt).decode(),
                     base64.b64encode(digest).decode()))


def verify_password(password: str, stored: str) -> bool:
    if not stored or stored.count("$") != 3:
        return False
    algo, rounds, salt_b64, digest_b64 = stored.split("$")
    if algo != _ALGO:
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        rounds_int = int(rounds)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds_int)
    return hmac.compare_digest(candidate, expected)


def new_token(length: int = 32) -> str:
    """URL-safe opaque token (sessions, API keys)."""
    return secrets.token_urlsafe(length)


def token_fingerprint(token: str) -> str:
    """SHA-256 of a token. Only fingerprints are stored, never the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_key(prefix: str = "pks") -> tuple[str, str]:
    """Return (plaintext key, display prefix). The plaintext is shown exactly once."""
    body = secrets.token_urlsafe(24)
    return f"{prefix}_{body}", f"{prefix}_{body[:6]}...{body[-4:]}"


def new_code(digits: int = 6) -> str:
    """A numeric one-time code, generated with a CSPRNG and never with ``random``."""
    return "".join(str(secrets.randbelow(10)) for _ in range(digits))


def constant_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left or "", right or "")


_PHONE_RE = re.compile(r"[^0-9+]")


def normalise_phone(raw: str, default_cc: str = "+33") -> str:
    """``06 65 42 39 89`` -> ``+33665423989``; keeps an explicit country code."""
    cleaned = _PHONE_RE.sub("", raw or "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = default_cc + cleaned[1:]
    return cleaned


def mask_phone(number: str) -> str:
    """``+33665423989`` -> ``+3366****989``: enough to recognise, not enough to leak."""
    if len(number) <= 7:
        return number
    return f"{number[:4]}****{number[-3:]}"
