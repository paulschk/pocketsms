"""SQLite storage: messages, users, sessions, API keys, OTP challenges.

One connection guarded by a lock. At the message volume a phone line produces
this is both simpler and faster than a connection pool, and it keeps the whole
data model in one readable file.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .security import (hash_password, new_api_key, new_token, token_fingerprint,
                       verify_password)

log = logging.getLogger("hilinkd.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'sms',
    key_hash     TEXT UNIQUE NOT NULL,
    display      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction    TEXT NOT NULL,
    phone        TEXT NOT NULL,
    text         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ok',
    detail       TEXT NOT NULL DEFAULT '',
    device_index TEXT NOT NULL DEFAULT '',
    device_date  TEXT NOT NULL DEFAULT '',
    fingerprint  TEXT UNIQUE,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_box ON messages(direction, id DESC);

CREATE TABLE IF NOT EXISTS otp_challenges (
    id           TEXT PRIMARY KEY,
    phone        TEXT NOT NULL,
    service      TEXT NOT NULL DEFAULT '',
    purpose      TEXT NOT NULL DEFAULT '',
    code_hash    TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_challenges(phone, created_at DESC);

CREATE TABLE IF NOT EXISTS otp_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # -- users ------------------------------------------------------------- #

    def count_users(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str, is_admin: bool = False) -> int:
        with self.lock:
            cursor = self.db.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at)"
                " VALUES (?,?,?,?)",
                (username.strip(), hash_password(password), 1 if is_admin else 0, now()))
            self.db.commit()
            return cursor.lastrowid

    def user_by_name(self, username: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE username = ?",
                                  (username.strip(),)).fetchone()
        return dict(row) if row else None

    def user_by_id(self, user_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, username, is_admin, created_at FROM users ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def set_password(self, user_id: int, password: str) -> None:
        with self.lock:
            self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                            (hash_password(password), user_id))
            self.db.commit()

    def delete_user(self, user_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.db.commit()

    def check_login(self, username: str, password: str) -> dict | None:
        user = self.user_by_name(username)
        if user and verify_password(password, user["password_hash"]):
            return user
        return None

    # -- sessions ---------------------------------------------------------- #

    def create_session(self, user_id: int, ttl_hours: int = 720) -> str:
        token = new_token()
        with self.lock:
            self.db.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
                " VALUES (?,?,?,?)",
                (token_fingerprint(token), user_id, now(), now_plus(ttl_hours * 3600)))
            self.db.commit()
        return token

    def session_user(self, token: str) -> dict | None:
        if not token:
            return None
        with self.lock:
            row = self.db.execute(
                "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = ?", (token_fingerprint(token),)).fetchone()
        if not row:
            return None
        if row["expires_at"] < now():
            self.delete_session(token)
            return None
        return dict(row)

    def delete_session(self, token: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?",
                            (token_fingerprint(token),))
            self.db.commit()

    def purge_sessions(self) -> int:
        with self.lock:
            cursor = self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
            self.db.commit()
            return cursor.rowcount

    # -- API keys ---------------------------------------------------------- #

    def create_api_key(self, label: str, scope: str = "sms") -> tuple[str, dict]:
        plaintext, display = new_api_key()
        with self.lock:
            cursor = self.db.execute(
                "INSERT INTO api_keys (label, scope, key_hash, display, created_at)"
                " VALUES (?,?,?,?,?)",
                (label.strip() or "unnamed", scope, token_fingerprint(plaintext),
                 display, now()))
            self.db.commit()
            row = self.db.execute("SELECT * FROM api_keys WHERE id = ?",
                                  (cursor.lastrowid,)).fetchone()
        return plaintext, dict(row)

    def api_key_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        with self.lock:
            row = self.db.execute("SELECT * FROM api_keys WHERE key_hash = ?",
                                  (token_fingerprint(token),)).fetchone()
            if row:
                self.db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                                (now(), row["id"]))
                self.db.commit()
        return dict(row) if row else None

    def list_api_keys(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, label, scope, display, created_at, last_used_at"
                " FROM api_keys ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    def revoke_api_key(self, key_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            self.db.commit()

    # -- messages ---------------------------------------------------------- #

    @staticmethod
    def _fingerprint(direction: str, phone: str, text: str, index: str, date: str) -> str:
        raw = "\x1f".join((direction, phone, text, index, date))
        return hashlib.sha256(raw.encode()).hexdigest()

    def add_message(self, direction: str, phone: str, text: str, *, status: str = "ok",
                    detail: str = "", device_index: str = "",
                    device_date: str = "") -> int | None:
        """Returns the row id, or None when this message was already stored."""
        fingerprint = self._fingerprint(direction, phone, text, device_index, device_date)
        with self.lock:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO messages (direction, phone, text, status, detail,"
                " device_index, device_date, fingerprint, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (direction, phone, text, status, detail, device_index, device_date,
                 fingerprint, now()))
            self.db.commit()
            return cursor.lastrowid if cursor.rowcount else None

    def messages(self, box: str = "inbox", limit: int = 50, offset: int = 0) -> list[dict]:
        direction = "in" if box in ("inbox", "in") else "out"
        with self.lock:
            rows = self.db.execute(
                "SELECT id, phone, text, status, detail, created_at FROM messages"
                " WHERE direction = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (direction, limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def message(self, message_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM messages WHERE id = ?",
                                  (message_id,)).fetchone()
        return dict(row) if row else None

    def message_stats(self) -> dict:
        with self.lock:
            rows = self.db.execute(
                "SELECT direction, COUNT(*) AS n FROM messages GROUP BY direction").fetchall()
        stats = {"in": 0, "out": 0}
        for row in rows:
            stats[row["direction"]] = row["n"]
        return stats

    # -- OTP --------------------------------------------------------------- #

    def create_challenge(self, challenge_id: str, phone: str, service: str, purpose: str,
                         code_hash: str, ttl_seconds: int, max_attempts: int) -> str:
        with self.lock:
            self.db.execute(
                "INSERT INTO otp_challenges (id, phone, service, purpose, code_hash,"
                " max_attempts, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (challenge_id, phone, service, purpose, code_hash, max_attempts,
                 now(), now_plus(ttl_seconds)))
            self.db.execute("INSERT INTO otp_events (phone, created_at) VALUES (?,?)",
                            (phone, now()))
            self.db.commit()
        return challenge_id

    def drop_challenge(self, challenge_id: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM otp_challenges WHERE id = ?", (challenge_id,))
            self.db.commit()

    def challenge(self, challenge_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM otp_challenges WHERE id = ?",
                                  (challenge_id,)).fetchone()
        return dict(row) if row else None

    def latest_open_challenge(self, phone: str) -> dict | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM otp_challenges WHERE phone = ? AND consumed_at IS NULL"
                " ORDER BY created_at DESC LIMIT 1", (phone,)).fetchone()
        return dict(row) if row else None

    def bump_attempts(self, challenge_id: str) -> int:
        with self.lock:
            self.db.execute("UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = ?",
                            (challenge_id,))
            self.db.commit()
            row = self.db.execute("SELECT attempts FROM otp_challenges WHERE id = ?",
                                  (challenge_id,)).fetchone()
        return row["attempts"] if row else 0

    def consume_challenge(self, challenge_id: str) -> None:
        with self.lock:
            self.db.execute("UPDATE otp_challenges SET consumed_at = ? WHERE id = ?",
                            (now(), challenge_id))
            self.db.commit()

    def requests_last_hour(self, phone: str) -> int:
        since = now_plus(-3600)
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM otp_events WHERE phone = ? AND created_at >= ?",
                (phone, since)).fetchone()[0]

    def purge_otp(self) -> int:
        """Drop challenges older than a day: they can only be noise by then."""
        with self.lock:
            cutoff = now_plus(-86400)
            cursor = self.db.execute(
                "DELETE FROM otp_challenges WHERE created_at < ?", (cutoff,))
            self.db.execute("DELETE FROM otp_events WHERE created_at < ?", (cutoff,))
            self.db.commit()
            return cursor.rowcount
