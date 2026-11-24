"""Inbound SMS: polling, deduplication, and fan-out to relays.

HiLink has no push notification for a new message, so the only option is to ask
the router for its inbox periodically. The router hands back the whole box every
time, so every message is fingerprinted and stored with ``INSERT OR IGNORE``:
the same message can be polled a hundred times and will only ever fire the
relays once.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request

log = logging.getLogger("hilinkd.poller")

#: BoxType on the HiLink API.
BOX_INBOX = 1


class Poller(threading.Thread):
    """Polls the modem inbox and relays anything new. Survives every failure."""

    daemon = True

    def __init__(self, modem, store, cfg, otp=None) -> None:
        super().__init__(name=f"{__name__}.poller")
        self.modem = modem
        self.store = store
        self.cfg = cfg
        self.otp = otp
        self.interval = max(5, cfg.int("poll_interval"))
        self.stopped = threading.Event()
        self.last_poll: str | None = None
        self.last_error: str | None = None
        self.seen = 0

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        log.info("polling the inbox every %ds", self.interval)
        while not self.stopped.wait(self.interval):
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - a poll must never kill the thread
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("poll failed: %s", self.last_error)

    def poll_once(self) -> int:
        from .store import now

        new_messages = 0
        for item in self.modem.list_sms(box=BOX_INBOX):
            text = item.get("text", "")
            if not text:
                continue
            new_id = self.store.add_message(
                "in", item.get("phone", ""), text,
                device_index=item.get("device_index", ""),
                device_date=item.get("device_date", ""))
            if new_id:
                new_messages += 1
                self.seen += 1
                log.info("inbound #%s from %s", new_id, item.get("phone", "?"))
                self.fan_out(item.get("phone", ""), text)
        self.last_poll = now()
        self.last_error = None
        return new_messages

    # -- relays ------------------------------------------------------------- #

    def fan_out(self, phone: str, text: str) -> None:
        """Tell whoever wants to know. Failures are logged, never raised."""
        if self.cfg.relay:
            self._ntfy(phone, text)
            self._telegram(phone, text)
        if self.cfg["webhook_url"]:
            self._webhook(phone, text)

    def _ntfy(self, phone: str, text: str) -> None:
        url = self.cfg["ntfy_url"]
        if not url:
            return
        try:
            request = urllib.request.Request(
                url.rstrip("/"), data=f"{phone}: {text}".encode(),
                headers={"Title": "SMS from " + phone, "Tags": "sms"}, method="POST")
            urllib.request.urlopen(request, timeout=10).read()
        except Exception as exc:  # noqa: BLE001
            log.warning("ntfy relay failed: %s", exc)

    def _telegram(self, phone: str, text: str) -> None:
        token, chat = self.cfg["telegram_token"], self.cfg["telegram_chat_id"]
        if not token or not chat:
            return
        try:
            payload = json.dumps({"chat_id": chat, "text": f"SMS from {phone}:\n{text}"})
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=payload.encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request, timeout=10).read()
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram relay failed: %s", exc)

    def _webhook(self, phone: str, text: str) -> None:
        try:
            payload = json.dumps({"from": phone, "text": text}).encode()
            request = urllib.request.Request(
                self.cfg["webhook_url"], data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request, timeout=10).read()
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook failed: %s", exc)
