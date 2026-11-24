"""Configuration: environment first, config file second, sensible defaults always.

Everything is overridable with an environment variable so Docker deployments need
nothing but a compose file. Both ``POCKETSMS_FOO`` and the bare ``FOO`` spellings
work, because nobody remembers the prefix on the first try.
"""

from __future__ import annotations

import os
import secrets
import sys
import urllib.request

from . import NAME

#: Order matters: the first address that answers the HiLink API wins.
DEFAULT_MODEM_CANDIDATES = (
    "http://192.168.1.1",    # Orange Airbox, most French ISP routers
    "http://192.168.8.1",    # Huawei HiLink default
    "http://192.168.0.1",
    "http://192.168.9.1",
    "http://192.168.100.1",
)

#: Settings an administrator may change from the web UI. They live in the
#: database and are layered over the environment, so a change survives a restart
#: without editing a file - which matters in a container that mounts only /data.
EDITABLE = {
    "line_alert_phone": {
        "label": "Owner phone number",
        "help": "Where the line-down alert is texted. Empty disables alerts.",
        "kind": "phone",
        "default": "",
    },
    "line_alert_cooldown": {
        "label": "Repeat at most every (seconds)",
        "help": "Minimum gap between two alerts for the same outage.",
        "kind": "int",
        "default": "1800",
        "min": 60,
        "max": 86400,
    },
    "line_alert_on_recovery": {
        "label": "Text me when the line comes back",
        "help": "Sends a second message once the primary connection is up again.",
        "kind": "bool",
        "default": "true",
    },
    "line_down_message": {
        "label": "Line-down message",
        "help": "Placeholders: {time} and {detail}.",
        "kind": "text",
        "default": "pocketsms: la ligne principale est tombee ({time}). Bascule 4G en cours.",
    },
    "line_up_message": {
        "label": "Recovery message",
        "help": "Placeholders: {time} and {detail}.",
        "kind": "text",
        "default": "pocketsms: la ligne principale est revenue ({time}).",
    },
}


DEFAULTS = {
    # --- modem -------------------------------------------------------------
    "modem_url": "",              # empty = autodetect from the candidates above
    "modem_user": "admin",
    "modem_password": "admin",    # default on ISP-branded Huawei firmware
    "modem_timeout": "10",
    # --- service -----------------------------------------------------------
    "bind": "0.0.0.0",
    "port": "8090",
    "data_dir": "",               # empty = pick the best available location
    "base_url": "",               # only used in messages/links
    # --- first run ---------------------------------------------------------
    "admin_user": "admin",
    "admin_password": "",         # empty = generated and printed once at startup
    # --- sms ---------------------------------------------------------------
    "poll_interval": "15",        # seconds between inbound polls
    "sms_max_length": "918",      # 6 concatenated SMS
    # --- two-step verification --------------------------------------------
    "otp_length": "6",
    "otp_ttl": "300",             # seconds a code stays valid
    "otp_max_attempts": "5",
    "otp_max_per_hour": "5",      # per phone number
    "otp_message": "{code} : votre code de verification ({service}). Valable {minutes} min.",
    # --- relays ------------------------------------------------------------
    "relay_enabled": "false",
    "ntfy_url": "",
    "telegram_token": "",
    "telegram_chat_id": "",
    "webhook_url": "",            # POSTed for every inbound SMS
    # --- misc --------------------------------------------------------------
    "log_level": "info",
    # --- line monitoring --------------------------------------------------- #
    # Defaults come from EDITABLE: one source of truth for what the web UI shows
    # and what the environment may override, so the two cannot drift apart.
    # Config.load only scans DEFAULTS for environment variables, so a key that
    # lives only in EDITABLE could not be set from the environment at all.
    **{key: spec["default"] for key, spec in EDITABLE.items()},
}


def _default_data_dir() -> str:
    """Pick somewhere writable without asking: /data in containers, /var/lib on hosts."""
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data"
    for path in ("/var/lib/" + NAME, os.path.expanduser("~/.local/share/" + NAME)):
        if os.path.isdir(path) and os.access(path, os.W_OK):
            return path
        parent = os.path.dirname(path)
        # Never create anything directly under /: that is how you end up with a
        # stray /data on a machine that was only asked to print a diagnostic.
        if os.path.isdir(parent) and os.access(parent, os.W_OK) and parent != "/":
            try:
                os.makedirs(path, mode=0o700, exist_ok=True)
                return path
            except OSError:
                continue
    fallback = os.path.join(os.getcwd(), "data")
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    return fallback


class Config(dict):
    """A dict with defaults, an optional config file, and environment overrides."""

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls(DEFAULTS)
        path = path or os.environ.get(f"{NAME.upper()}_CONF") or "/etc/" + NAME + ".conf"
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        # The file is written in the same UPPERCASE style as the
                        # environment, but the defaults are lowercase: normalise,
                        # or every key in the file is silently ignored.
                        cfg[key.strip().lower()] = value.strip().strip('"').strip("'")
            except OSError as exc:
                # A config file the service cannot read must not be a crash with
                # a traceback: say what is wrong and carry on with defaults.
                print(f"[{NAME}] cannot read {path}: {exc} "
                      f"(check its permissions - continuing with defaults and the "
                      f"environment)", file=sys.stderr, flush=True)

        prefix = NAME.upper() + "_"
        for key in DEFAULTS:
            for spelling in (prefix + key.upper(), key.upper()):
                if spelling in os.environ:
                    cfg[key] = os.environ[spelling]
                    break

        cfg["data_dir"] = cfg["data_dir"] or _default_data_dir()
        os.makedirs(cfg["data_dir"], mode=0o700, exist_ok=True)
        cfg["db_path"] = os.path.join(cfg["data_dir"], NAME + ".db")
        cfg["secret_path"] = os.path.join(cfg["data_dir"], "session.secret")
        return cfg

    # -- typed accessors ---------------------------------------------------- #

    def int(self, key: str) -> int:
        try:
            return int(self[key])
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def flag(self, key: str) -> bool:
        return str(self[key]).lower() in ("1", "true", "yes", "on")

    @property
    def relay(self) -> bool:
        return self.flag("relay_enabled")

    @property
    def directory(self) -> str:
        return self["data_dir"]

    # -- secrets ------------------------------------------------------------ #

    def session_secret(self) -> bytes:
        """Stable across restarts, never printed: sessions must survive a reboot."""
        path = self["secret_path"]
        if os.path.exists(path):
            with open(path, "rb") as handle:
                blob = handle.read().strip()
            if blob:
                return blob
        blob = secrets.token_bytes(32)
        with open(path, "wb") as handle:
            handle.write(blob)
        os.chmod(path, 0o600)
        return blob

    def detected_modem_url(self) -> str:
        """First candidate that answers the HiLink API, else the first candidate."""
        if self["modem_url"]:
            return self["modem_url"].rstrip("/")
        timeout = min(3.0, float(self["modem_timeout"]))
        for candidate in DEFAULT_MODEM_CANDIDATES:
            try:
                with urllib.request.urlopen(
                        candidate + "/api/webserver/SesTokInfo", timeout=timeout):
                    print(f"[{NAME}] modem detected at {candidate}", file=sys.stderr, flush=True)
                    return candidate
            except Exception:  # noqa: BLE001 - probing, any failure just means "not here"
                continue
        return DEFAULT_MODEM_CANDIDATES[0]
