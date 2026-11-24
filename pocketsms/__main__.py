"""Command line entry point: serve, doctor, user, key.

``python -m pocketsms serve`` is the service. ``doctor`` exists because the most
common failure with these routers is not the software at all - it is the router
being unreachable, still in mass-storage mode, or sitting on a subnet that
collides with the local LAN. doctor says which, in one screen.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import secrets
import sys
import threading
import time

from . import DESCRIPTION, NAME, TAGLINE, __version__
from .config import DEFAULT_MODEM_CANDIDATES, Config
from .hilink import HiLink, ModemError
from .otp import OtpService
from .poller import Poller
from .server import serve as serve_http
from .store import Store

log = logging.getLogger(NAME)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=f"%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build(cfg: Config):
    store = Store(cfg["db_path"])
    modem = HiLink(cfg.detected_modem_url(), cfg["modem_user"], cfg["modem_password"],
                   float(cfg["modem_timeout"]))
    otp = OtpService(modem, store, cfg)
    poller = Poller(modem, store, cfg, otp)
    return store, modem, otp, poller


def ensure_admin(store: Store, cfg: Config) -> None:
    """First run: create the admin account and say the password out loud, once."""
    if store.count_users():
        return
    password = cfg["admin_password"] or secrets.token_urlsafe(12)
    store.create_user(cfg["admin_user"], password, is_admin=True)
    banner = [
        "",
        "  " + "=" * 62,
        f"  {NAME}: first run, an account has been created",
        "",
        f"    username: {cfg['admin_user']}",
        f"    password: {password}",
        "",
        "    Sign in and change it in Settings. This password is never",
        "    printed again, and is not stored anywhere in plaintext.",
        "  " + "=" * 62,
        "",
    ]
    print("\n".join(banner), flush=True)
    log.info("created the initial admin account %r", cfg["admin_user"])


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_serve(args) -> int:
    cfg = Config.load(args.config)
    setup_logging(args.log_level or cfg["log_level"])
    log.info("%s %s - %s", NAME, __version__, TAGLINE)
    log.info("data directory %s", cfg.directory)

    store, modem, otp, poller = build(cfg)
    ensure_admin(store, cfg)
    if not cfg["modem_url"]:
        log.info("modem address: %s (autodetected)", modem.base)

    try:
        modem.login()
        information = modem.information()
        log.info("modem %s, firmware %s, operator %s",
                 information.get("DeviceName"), information.get("SoftwareVersion"),
                 modem.operator())
    except Exception as exc:  # noqa: BLE001 - the API stays up and retries on demand
        log.warning("could not reach the modem yet (%s) - the API will retry", exc)

    poller.start()
    threading.Thread(target=_housekeeping, args=(store, poller), daemon=True,
                     name="housekeeping").start()
    serve_http(cfg, store, modem, otp, poller)
    poller.stop()
    return 0


def _housekeeping(store: Store, poller: Poller) -> None:
    """Hourly tidying: dead sessions, stale challenges, old messages stay put."""
    while True:
        try:
            sessions = store.purge_sessions()
            challenges = store.purge_otp()
            if sessions or challenges:
                log.info("housekeeping: %d session(s), %d challenge(s) removed",
                         sessions, challenges)
        except Exception as exc:  # noqa: BLE001
            log.warning("housekeeping failed: %s", exc)
        time.sleep(3600)


def cmd_doctor(args) -> int:
    cfg = Config.load(args.config)
    setup_logging("warning")
    store = Store(cfg["db_path"])
    print(f"{NAME} {__version__} - {DESCRIPTION}\n")

    print("configuration")
    print(f"  config file   : {args.config or '/etc/' + NAME + '.conf'}")
    print(f"  data dir      : {cfg.directory}")
    print(f"  database      : {cfg['db_path']}")
    print(f"  listen        : {cfg['bind']}:{cfg['port']}")
    print(f"  poll interval : {cfg.int('poll_interval')}s")
    print(f"  otp           : {cfg.int('otp_length')} digits, "
          f"{cfg.int('otp_ttl')}s, {cfg.int('otp_max_per_hour')}/hour/number")
    print(f"  relays        : ntfy={'yes' if cfg['ntfy_url'] else 'no'} "
          f"telegram={'yes' if cfg['telegram_token'] else 'no'} "
          f"webhook={'yes' if cfg['webhook_url'] else 'no'}")

    print("\nmodem")
    candidates = [cfg["modem_url"]] if cfg["modem_url"] else list(DEFAULT_MODEM_CANDIDATES)
    modem = None
    for candidate in candidates:
        probe = HiLink(candidate, cfg["modem_user"], cfg["modem_password"],
                       min(4.0, float(cfg["modem_timeout"])))
        try:
            probe.login()
            print(f"  reachable     : yes, at {candidate}")
            modem = probe
            break
        except ModemError as exc:
            print(f"  {candidate:<24} {exc}")
    if modem is None:
        print("\n  The router did not answer. Most common causes, in order:")
        print("   1. it is still in mass-storage (ZeroCD) mode - run scripts/bringup.sh")
        print("   2. its LAN collides with this host's LAN - see the README, /32 route")
        print("   3. the admin password differs from the configured one")
        return 2

    try:
        information, status, sim = modem.information(), modem.status(), modem.sim_status()
        print(f"  device        : {information.get('DeviceName')}")
        print(f"  firmware      : {information.get('SoftwareVersion')} "
              f"({information.get('HardwareVersion')})")
        print(f"  imsi / iccid  : {information.get('Imsi')} / {information.get('Iccid')}")
        print(f"  operator      : {modem.operator()}")
        print(f"  sim state     : {sim.get('SimState')} "
              f"(257 = ready), operator lock: {sim.get('SimLockEnable')} (0 = none)")
        print(f"  connection    : {status.get('ConnectionStatus')}, "
              f"signal {status.get('SignalStrength') or '?'}, "
              f"battery {status.get('BatteryPercent')}%")
        print(f"  sms counters  : {modem.sms_count()}")
    except ModemError as exc:
        print(f"  authenticated but a query failed: {exc}")

    print("\ndatabase")
    print(f"  accounts      : {store.count_users()}")
    print(f"  api keys      : {len(store.list_api_keys())}")
    print(f"  messages      : {store.message_stats()}")
    store.close()
    print("\nEverything the service needs is reachable.")
    return 0


def cmd_user(args) -> int:
    cfg = Config.load(args.config)
    setup_logging("warning")
    store = Store(cfg["db_path"])
    if args.action == "add":
        if store.user_by_name(args.username):
            print(f"user {args.username!r} already exists", file=sys.stderr)
            return 1
        password = args.password or getpass.getpass("password: ")
        if len(password) < 8:
            print("password must be at least 8 characters", file=sys.stderr)
            return 1
        store.create_user(args.username, password, is_admin=not args.no_admin)
        print(f"created {args.username!r} "
              f"({'admin' if not args.no_admin else 'user'})")
    elif args.action == "list":
        for user in store.list_users():
            print(f"{user['id']:>3}  {user['username']:<20} "
                  f"{'admin' if user['is_admin'] else 'user':<6} {user['created_at']}")
    elif args.action == "delete":
        user = store.user_by_name(args.username)
        if not user:
            print(f"no such user: {args.username}", file=sys.stderr)
            return 1
        store.delete_user(user["id"])
        print(f"deleted {args.username!r}")
    elif args.action == "password":
        user = store.user_by_name(args.username)
        if not user:
            print(f"no such user: {args.username}", file=sys.stderr)
            return 1
        password = args.password or getpass.getpass("new password: ")
        store.set_password(user["id"], password)
        print(f"password updated for {args.username!r}")
    store.close()
    return 0


def cmd_key(args) -> int:
    cfg = Config.load(args.config)
    setup_logging("warning")
    store = Store(cfg["db_path"])
    if args.action == "create":
        plaintext, row = store.create_api_key(args.label, args.scope)
        print(f"label  : {row['label']}   scope: {row['scope']}")
        print(f"key    : {plaintext}")
        print("\nThis key is shown once: only a fingerprint is stored.")
    elif args.action == "list":
        keys = store.list_api_keys()
        if not keys:
            print("no API keys")
        for key in keys:
            print(f"{key['id']:>3}  {key['label']:<20} {key['scope']:<6} "
                  f"{key['display']:<24} last used: {key['last_used_at'] or 'never'}")
    elif args.action == "revoke":
        store.revoke_api_key(args.id)
        print(f"revoked key {args.id}")
    store.close()
    return 0


# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=NAME, description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    parser.add_argument("--config", help="path to the config file")
    # The same flag on every subcommand, so `pocketsms serve --config X` works as
    # well as `pocketsms --config X serve`. SUPPRESS keeps the global value when the
    # subcommand does not repeat it: a plain default would overwrite it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="path to the config file")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the gateway", parents=[common])
    serve.add_argument("--log-level", choices=["debug", "info", "warning", "error"])
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", parents=[common],
                            help="check the modem, the database and the config")
    doctor.set_defaults(func=cmd_doctor)

    user = sub.add_parser("user", parents=[common], help="manage accounts")
    user.add_argument("action", choices=["add", "list", "delete", "password"])
    user.add_argument("username", nargs="?")
    user.add_argument("--password", help="avoid the interactive prompt (careful: shell history)")
    user.add_argument("--no-admin", action="store_true", help="create a non-admin account")
    user.set_defaults(func=cmd_user)

    key = sub.add_parser("key", parents=[common], help="manage API keys")
    key.add_argument("action", choices=["create", "list", "revoke"])
    key.add_argument("label", nargs="?")
    key.add_argument("--scope", choices=["sms", "otp", "full"], default="sms")
    key.add_argument("--id", type=int)
    key.set_defaults(func=cmd_key)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
