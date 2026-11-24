"""The HiLink client: everything that talks to the router.

Why HTTP and not AT commands: the routers this project targets (Orange Airbox,
Huawei E5573/E5576 and friends) expose no serial interface at all - only
CDC-Ethernet plus this XML API. Gateways built on ``AT+CMGS`` have nothing to
open on these devices.

Protocol facts, all measured on firmware 21.315.01.02.1047:

* login is mandatory (``<login>1</login>``); without it every call answers ``100003``
* ``password_type 4`` = ``base64(sha256hex(user + base64(sha256hex(pw)) + token))``
* the CSRF token is **single use**; login hands back a ``#``-separated list of ~32
* the authenticated session is the login response's ``Set-Cookie``. The SessionID
  from ``SesTokInfo`` is *not* authenticated - reusing it gives ``100003``
* there is no push for inbound SMS: polling is the only option

Error codes worth knowing: ``100002`` unsupported, ``100003`` not logged in,
``125002`` wrong session, ``125003`` wrong/used token, ``108002`` bad password,
``108003`` already logged in, ``111001`` invalid number.
"""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import re
import threading
import urllib.error
import urllib.request

log = logging.getLogger("pocketsms.modem")

AUTH_ERRORS = ("100003", "125002", "125003")


class ModemError(RuntimeError):
    """Raised for anything the router refuses, with the code kept in ``args``."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def xml_escape(value: str) -> str:
    return html.escape(value, quote=False)


class HiLink:
    """A small, self-healing client. Safe to share between threads."""

    def __init__(self, base_url: str, username: str = "admin", password: str = "admin",
                 timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.cookie: str | None = None
        self.tokens: list[str] = []
        self.lock = threading.RLock()
        self.last_error: str | None = None

    # -- plumbing ---------------------------------------------------------- #

    def _http(self, method: str, path: str, body: bytes | None = None,
              headers: dict | None = None) -> tuple[str, dict]:
        request = urllib.request.Request(self.base + path, data=body, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace"), dict(response.headers)
        except urllib.error.HTTPError as exc:
            # The router answers most errors with 200 and an <error> body, but not all.
            return exc.read().decode("utf-8", "replace"), dict(exc.headers or {})
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim for the operator
            raise ModemError(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def tag(xml: str, name: str) -> str:
        """Extract a field. The firmware escapes characters inside values as
        numeric entities (a message containing "(API)" arrives as
        ``&#40;API&#41;``), so decode them here, once, for every caller."""
        match = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
        return html.unescape(match.group(1).strip()) if match else ""

    @staticmethod
    def header(headers: dict, name: str) -> str:
        for key, value in headers.items():
            if key.lower() == name.lower():
                return value
        return ""

    # -- session ----------------------------------------------------------- #

    def _open_session(self) -> str:
        data, _ = self._http("GET", "/api/webserver/SesTokInfo")
        session = self.tag(data, "SesInfo")     # literally "SessionID=..."
        token = self.tag(data, "TokInfo")
        if not session or not token:
            raise ModemError(f"SesTokInfo unusable: {data[:120]}")
        self.cookie = session
        self.tokens = [token]
        return token

    def _password_hash(self, token: str) -> str:
        first = hashlib.sha256(self.password.encode()).hexdigest()
        first_b64 = base64.b64encode(first.encode()).decode()
        mixed = f"{self.username}{first_b64}{token}".encode()
        return base64.b64encode(hashlib.sha256(mixed).hexdigest().encode()).decode()

    def login(self) -> None:
        with self.lock:
            token = self._open_session()
            body = (
                '<?xml version="1.0" encoding="UTF-8"?><request>'
                f"<Username>{xml_escape(self.username)}</Username>"
                f"<Password>{self._password_hash(token)}</Password>"
                "<password_type>4</password_type></request>"
            ).encode()
            data, headers = self._http(
                "POST", "/api/user/login", body,
                {"Cookie": self.cookie or "",
                 "__RequestVerificationToken": token,
                 "Content-Type": "text/xml"},
            )
            if "<response>OK</response>" not in data.replace("\n", "").replace("\r", ""):
                code = self.tag(data, "code")
                raise ModemError(f"login refused: {data[:160]}", code or None)
            # The authenticated session only ever arrives in this header.
            cookie = self.header(headers, "Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";")[0]
            fresh = (self.header(headers, "__RequestVerificationToken")
                     or self.header(headers, "__RequestVerificationTokenone"))
            self.tokens = [part for part in fresh.split("#") if part]
            self.last_error = None

    def _next_token(self) -> str:
        if not self.tokens:
            # Re-login rather than re-reading SesTokInfo: refreshing with the
            # current cookie builds a fresh *unauthenticated* session and every
            # later call would fail with 100003.
            self.login()
        return self.tokens.pop(0)

    # -- requests ---------------------------------------------------------- #

    def request(self, method: str, path: str, body: str | None = None,
                retry: bool = True) -> str:
        with self.lock:
            if self.cookie is None:
                self.login()
            token = self._next_token()
            data, headers = self._http(
                method, path, body.encode() if body else None,
                {"Cookie": self.cookie or "",
                 "__RequestVerificationToken": token,
                 "Content-Type": "text/xml"},
            )
            code = self.tag(data, "code")
            if code in AUTH_ERRORS and retry:
                log.warning("session lost (%s), re-authenticating", code)
                self.login()
                return self.request(method, path, body, retry=False)
            fresh = self.header(headers, "__RequestVerificationToken")
            if fresh:
                self.tokens.extend(part for part in fresh.split("#") if part)
            if code:
                self.last_error = f"error {code}"
                raise ModemError(f"{path} -> error {code}", code)
            return data

    # -- operations -------------------------------------------------------- #

    def send_sms(self, phone: str, text: str) -> None:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?><request><Index>-1</Index>'
            f"<Phones><Phone>{xml_escape(phone)}</Phone></Phones><Sca></Sca>"
            f"<Content>{xml_escape(text)}</Content><Length>-1</Length>"
            "<Reserved>1</Reserved><Date>-1</Date></request>"
        )
        data = self.request("POST", "/api/sms/send-sms", body)
        if "<response>OK</response>" not in data.replace("\n", "").replace("\r", ""):
            raise ModemError(f"send refused: {data[:160]}", self.tag(data, "code") or None)

    def list_sms(self, box: int = 1) -> list[dict]:
        """box: 1 = inbox, 2 = sent, 3 = drafts."""
        body = (
            '<?xml version="1.0" encoding="UTF-8"?><request><PageIndex>1</PageIndex>'
            "<ReadCount>50</ReadCount>"
            f"<BoxType>{box}</BoxType><SortType>0</SortType><Ascending>0</Ascending>"
            "<UnreadPreferred>0</UnreadPreferred></request>"
        )
        data = self.request("POST", "/api/sms/sms-list", body)
        result = []
        for block in re.findall(r"<Message>(.*?)</Message>", data, re.S):
            result.append({
                "device_index": self.tag(block, "Index"),
                "phone": self.tag(block, "Phone"),
                "text": self.tag(block, "Content"),
                "device_date": self.tag(block, "Date"),
                "state": self.tag(block, "Smstat"),
            })
        return result

    def sms_count(self) -> dict:
        data = self.request("GET", "/api/sms/sms-count")
        return {name: self.tag(data, name) for name in
                ("LocalUnread", "LocalInbox", "LocalOutbox", "SimUsed")}

    def status(self) -> dict:
        data = self.request("GET", "/api/monitoring/status")
        return {name: self.tag(data, name) for name in
                ("ConnectionStatus", "SignalStrength", "SignalIcon", "SimStatus",
                 "ServiceStatus", "CurrentNetworkType", "RoamingStatus",
                 "BatteryStatus", "BatteryPercent", "WanIPAddress")}

    def signal(self) -> dict:
        data = self.request("GET", "/api/device/signal")
        return {name: self.tag(data, name) for name in
                ("rssi", "rsrp", "rsrq", "sinr", "mode", "psatt")}

    def information(self) -> dict:
        data = self.request("GET", "/api/device/information")
        return {name: self.tag(data, name) for name in
                ("DeviceName", "SoftwareVersion", "HardwareVersion", "Imei",
                 "Imsi", "Iccid")}

    def sim_status(self) -> dict:
        data = self.request("GET", "/api/monitoring/converged-status")
        return {"SimState": self.tag(data, "SimState"),
                "SimLockEnable": self.tag(data, "SimLockEnable")}

    def operator(self) -> str:
        data = self.request("GET", "/api/net/current-plmn")
        return self.tag(data, "FullName")

    # -- friendliness ------------------------------------------------------- #

    def snapshot(self) -> dict:
        """One call the UI and the API can both use, with everything that matters."""
        return {
            "information": self.information(),
            "status": self.status(),
            "signal": self.signal(),
            "sim": self.sim_status(),
            "operator": self.operator(),
            "counts": self.sms_count(),
        }

    def healthy(self) -> tuple[bool, str]:
        try:
            self.information()
            return True, "ok"
        except ModemError as exc:
            return False, str(exc)
