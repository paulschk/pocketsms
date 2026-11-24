"""The HTTP layer: routing, authentication, scopes, JSON in and out.

Two ways in:

* **session cookie** - the web interface. A signed-in browser carries an opaque
  cookie; state-changing calls from the browser must also send
  ``X-Requested-With: hilinkd``, which a cross-site form cannot do, so the cookie
  is not exploitable by CSRF.
* **API key** - everything else. ``X-API-Key: hkd_...`` with one of three scopes:
  ``sms`` (send/read messages, read the modem), ``otp`` (two-step verification),
  ``full`` (both, plus key and user management).

Every error is JSON: ``{"error": "reason", "detail": "human readable"}``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import NAME, __version__
from .hilink import ModemError
from .otp import OtpError
from .security import constant_eq, normalise_phone
from .webui import page

log = logging.getLogger("hilinkd.http")

SESSION_COOKIE = NAME + "_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = NAME
SCOPE_SMS, SCOPE_OTP, SCOPE_FULL = "sms", "otp", "full"

LOGIN_WINDOW = 300.0     # seconds
LOGIN_MAX_FAILURES = 10  # per source address, per window


class ApiError(RuntimeError):
    def __init__(self, status: int, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.status = status
        self.reason = reason
        self.detail = detail


class Identity:
    """Who is calling: a signed-in user, an API key, or nobody."""

    def __init__(self, kind: str, name: str = "", scope: str = "", user_id: int | None = None,
                 is_admin: bool = False) -> None:
        self.kind = kind          # "user" | "key" | "anonymous"
        self.name = name
        self.scope = scope
        self.user_id = user_id
        self.is_admin = is_admin

    def allows(self, scope: str) -> bool:
        if self.kind == "anonymous":
            return False
        if scope == "admin":
            # A session is not a "full" API key: for a signed-in user only the
            # admin flag counts, or every account could manage users and keys.
            if self.kind == "user":
                return bool(self.is_admin)
            return self.scope == SCOPE_FULL
        if self.kind == "user":
            return True
        # A full key allows everything; any other key allows exactly its scope.
        return self.scope == SCOPE_FULL or scope == self.scope


class App:
    """All the behaviour, kept away from the socket plumbing."""

    def __init__(self, cfg, store, modem, otp, poller=None) -> None:
        self.cfg = cfg
        self.store = store
        self.modem = modem
        self.otp = otp
        self.poller = poller
        self.started = time.time()
        self.failures: dict[str, list[float]] = {}
        self.failures_lock = threading.Lock()

    # -- helpers ------------------------------------------------------------ #

    def modem_status(self) -> dict:
        snapshot = self.modem.snapshot()
        snapshot["messages"] = self.store.message_stats()
        snapshot["poll"] = {
            "last": getattr(self.poller, "last_poll", None),
            "error": getattr(self.poller, "last_error", None),
            "received": getattr(self.poller, "seen", 0),
        }
        return snapshot

    def list_messages(self, box: str, limit: int, offset: int) -> dict:
        return {"box": box, "messages": self.store.messages(box, limit, offset)}

    def send(self, to: str, text: str) -> dict:
        number = normalise_phone(to)
        text = (text or "").strip()
        if not number or not text:
            raise ApiError(400, "missing_fields", "'to' and 'text' are both required")
        if len(text) > self.cfg.int("sms_max_length"):
            raise ApiError(413, "too_long",
                           f"{len(text)} characters exceeds the configured maximum")
        try:
            self.modem.send_sms(number, text)
        except ModemError as exc:
            self.store.add_message("out", number, text, status="failed", detail=str(exc))
            raise ApiError(502, "modem_error", str(exc)) from exc
        message_id = self.store.add_message("out", number, text, status="accepted")
        return {"ok": True, "id": message_id, "to": number, "accepted": True}

    # -- authentication ------------------------------------------------------ #

    def login(self, username: str, password: str, source: str) -> tuple[dict, str]:
        if self._throttled(source):
            raise ApiError(429, "too_many_attempts", "try again later")
        user = self.store.check_login(username, password)
        if not user:
            self._record_failure(source)
            log.warning("failed login for %r from %s", username, source)
            raise ApiError(401, "invalid_credentials")
        self._clear_failures(source)
        token = self.store.create_session(user["id"])
        return {"id": user["id"], "username": user["username"],
                "is_admin": bool(user["is_admin"])}, token

    def _throttled(self, source: str) -> bool:
        with self.failures_lock:
            recent = [t for t in self.failures.get(source, []) if time.time() - t < LOGIN_WINDOW]
            self.failures[source] = recent
            return len(recent) >= LOGIN_MAX_FAILURES

    def _record_failure(self, source: str) -> None:
        with self.failures_lock:
            self.failures.setdefault(source, []).append(time.time())

    def _clear_failures(self, source: str) -> None:
        with self.failures_lock:
            self.failures.pop(source, None)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

_ROUTE_MESSAGE = re.compile(r"^/api/v1/messages/(\d+)$")
_ROUTE_KEY = re.compile(r"^/api/v1/keys/(\d+)$")
_ROUTE_USER = re.compile(r"^/api/v1/users/(\d+)$")


class Handler(BaseHTTPRequestHandler):
    server_version = f"{NAME}/{__version__}"
    protocol_version = "HTTP/1.1"
    sys_version = ""

    # -- output helpers ------------------------------------------------------ #

    def _send(self, status: int, payload: bytes, content_type: str,
              extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload, extra_headers: dict | None = None) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8", extra_headers)

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    # -- input helpers ------------------------------------------------------- #

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not length:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "invalid_json") from None

    def _cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _identity(self) -> Identity:
        key = self.headers.get("X-API-Key", "").strip()
        if key:
            record = self.server.store.api_key_by_token(key)  # type: ignore[attr-defined]
            if not record:
                raise ApiError(401, "invalid_api_key")
            return Identity("key", record["label"], record["scope"])
        token = self._cookie(SESSION_COOKIE)
        if token:
            user = self.server.store.session_user(token)  # type: ignore[attr-defined]
            if user:
                return Identity("user", user["username"], SCOPE_FULL, user["id"],
                                bool(user["is_admin"]))
        return Identity("anonymous")

    def _require(self, identity: Identity, scope: str, method: str) -> None:
        if identity.kind == "anonymous":
            raise ApiError(401, "authentication_required")
        if not identity.allows(scope):
            raise ApiError(403, "insufficient_scope", f"needs scope {scope!r}")
        # CSRF: a browser cookie must come with a header a cross-site form cannot set.
        if identity.kind == "user" and method != "GET":
            if self.headers.get(CSRF_HEADER, "") != CSRF_VALUE:
                raise ApiError(403, "csrf", f"send {CSRF_HEADER}: {CSRF_VALUE}")

    # -- routing ------------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        app: App = self.server.app          # type: ignore[attr-defined]
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        try:
            identity = self._identity() if path.startswith("/api/") else Identity("anonymous")

            # --- public ---------------------------------------------------- #
            if path == "/" or path == "/index.html":
                self._send(200, page().encode(), "text/html; charset=utf-8",
                           {"Cache-Control": "no-store"})
                return
            if path == "/favicon.svg":
                self._send(200, FAVICON, "image/svg+xml", {"Cache-Control": "max-age=86400"})
                return
            if path == "/api/v1/health":
                self._json(200, {"ok": True, "version": __version__,
                                 "uptime": int(time.time() - app.started),
                                 "messages": app.store.message_stats()})
                return
            if path == "/api/v1/auth/login" and method == "POST":
                payload = self._body()
                user, token = app.login(str(payload.get("username", "")),
                                        str(payload.get("password", "")),
                                        self.address_string())
                self._json(200, {"user": user}, {
                    "Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
                                  f"SameSite=Lax; Max-Age={30 * 86400}"})
                return

            # --- authenticated --------------------------------------------- #
            if path == "/api/v1/auth/me":
                self._require(identity, SCOPE_SMS, method)
                self._json(200, {"user": {"username": identity.name,
                                          "is_admin": identity.is_admin,
                                          "kind": identity.kind}})
                return
            if path == "/api/v1/auth/logout" and method == "POST":
                self._require(identity, SCOPE_SMS, method)
                app.store.delete_session(self._cookie(SESSION_COOKIE))
                self._json(200, {"ok": True},
                           {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0"})
                return
            if path == "/api/v1/auth/password" and method == "POST":
                if identity.kind != "user":
                    raise ApiError(403, "session_required",
                                   "change passwords from a signed-in browser session")
                self._require(identity, SCOPE_SMS, method)
                payload = self._body()
                user = app.store.user_by_id(identity.user_id or 0)
                if not user or not app.store.check_login(user["username"],
                                                         str(payload.get("current", ""))):
                    raise ApiError(403, "invalid_credentials")
                new_password = str(payload.get("new", ""))
                if len(new_password) < 8:
                    raise ApiError(400, "weak_password", "at least 8 characters")
                app.store.set_password(user["id"], new_password)
                self._json(200, {"ok": True})
                return

            if path == "/api/v1/status":
                self._require(identity, SCOPE_SMS, method)
                self._json(200, app.modem_status())
                return

            if path in ("/api/v1/messages", "/api/v1/sms/inbox", "/api/v1/sms/outbox"):
                self._require(identity, SCOPE_SMS, method)
                if method == "POST":
                    payload = self._body()
                    self._json(200, app.send(str(payload.get("to", "")),
                                             str(payload.get("text", ""))))
                    return
                box = ("inbox" if path.endswith("inbox") else
                       "outbox" if path.endswith("outbox") else
                       query.get("box", ["inbox"])[0])
                self._json(200, app.list_messages(box, _int(query, "limit", 50, 1, 500),
                                                  _int(query, "offset", 0, 0, 100000)))
                return
            if path == "/api/v1/sms/send" and method == "POST":
                self._require(identity, SCOPE_SMS, method)
                payload = self._body()
                self._json(200, app.send(str(payload.get("to", "")),
                                         str(payload.get("text", ""))))
                return

            match = _ROUTE_MESSAGE.match(path)
            if match:
                self._require(identity, SCOPE_SMS, method)
                message = app.store.message(int(match.group(1)))
                self._json(200, message) if message else self._json(404, {"error": "not_found"})
                return

            # --- two-step verification ------------------------------------- #
            if path == "/api/v1/otp" and method == "POST":
                self._require(identity, SCOPE_OTP, method)
                payload = self._body()
                self._json(201, app.otp.start(
                    str(payload.get("phone", "")),
                    service=str(payload.get("service", "")),
                    purpose=str(payload.get("purpose", "")),
                    message=payload.get("message") or None))
                return
            if path == "/api/v1/otp/verify" and method == "POST":
                self._require(identity, SCOPE_OTP, method)
                payload = self._body()
                self._json(200, app.otp.verify(
                    challenge=str(payload.get("challenge", "")),
                    phone=str(payload.get("phone", "")),
                    code=str(payload.get("code", ""))))
                return
            if path == "/api/v1/otp/status":
                self._require(identity, SCOPE_OTP, method)
                self._json(200, app.otp.status(query.get("challenge", [""])[0]))
                return

            # --- administration -------------------------------------------- #
            if path == "/api/v1/keys":
                self._require(identity, "admin", method)
                if method == "POST":
                    payload = self._body()
                    scope = str(payload.get("scope", SCOPE_SMS))
                    if scope not in (SCOPE_SMS, SCOPE_OTP, SCOPE_FULL):
                        raise ApiError(400, "invalid_scope", "sms, otp or full")
                    plaintext, row = app.store.create_api_key(
                        str(payload.get("label", "")), scope)
                    row.pop("key_hash", None)
                    self._json(201, {"key": plaintext, "details": row})
                    return
                self._json(200, {"keys": app.store.list_api_keys()})
                return
            match = _ROUTE_KEY.match(path)
            if match and method == "DELETE":
                self._require(identity, "admin", method)
                app.store.revoke_api_key(int(match.group(1)))
                self._json(200, {"ok": True})
                return

            if path == "/api/v1/users":
                self._require(identity, "admin", method)
                if method == "POST":
                    payload = self._body()
                    username = str(payload.get("username", "")).strip()
                    password = str(payload.get("password", ""))
                    if not username or len(password) < 8:
                        raise ApiError(400, "invalid_user",
                                       "username required, password at least 8 characters")
                    if app.store.user_by_name(username):
                        raise ApiError(409, "user_exists")
                    user_id = app.store.create_user(username, password,
                                                    bool(payload.get("admin")))
                    self._json(201, {"id": user_id, "username": username})
                    return
                self._json(200, {"users": app.store.list_users()})
                return
            match = _ROUTE_USER.match(path)
            if match and method == "DELETE":
                self._require(identity, "admin", method)
                user_id = int(match.group(1))
                if user_id == identity.user_id:
                    raise ApiError(400, "self_delete", "you cannot delete yourself")
                if app.store.count_users() <= 1:
                    raise ApiError(400, "last_user", "at least one account must remain")
                app.store.delete_user(user_id)
                self._json(200, {"ok": True})
                return

            self._json(404, {"error": "no_such_endpoint", "detail": path})

        except ApiError as exc:
            self._json(exc.status, {"error": exc.reason, "detail": exc.detail})
        except OtpError as exc:
            self._json(exc.status, {"error": exc.reason, "detail": exc.detail})
        except ModemError as exc:
            self._json(502, {"error": "modem_error", "detail": str(exc)})
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - never leak a traceback to a client
            log.exception("unhandled error on %s %s", method, path)
            self._json(500, {"error": "internal_error", "detail": type(exc).__name__})


def _int(query: dict, name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(query.get(name, [default])[0])))
    except (TypeError, ValueError):
        return default


FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#2b6cb0"/>'
    '<path d="M9 21V11h3.6c2.2 0 3.4 1 3.4 2.6 0 1.2-.7 2-1.8 2.3 1.3.2 2.2 1.1 2.2 2.5 '
    '0 1.7-1.3 2.6-3.6 2.6H9zm2.4-6h1c.9 0 1.4-.4 1.4-1.1S13.3 13 12.4 13h-1v2zm0 4.2h1.2c.9 '
    '0 1.5-.4 1.5-1.1 0-.7-.5-1.1-1.5-1.1h-1.2v2.2z" fill="#fff"/></svg>'
).encode()


def serve(cfg, store, modem, otp, poller) -> None:
    app = App(cfg, store, modem, otp, poller)
    address = (cfg["bind"], cfg.int("port"))
    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    server.app = app        # type: ignore[attr-defined]
    server.store = store    # type: ignore[attr-defined]
    log.info("listening on http://%s:%s", address[0], address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        server.server_close()
