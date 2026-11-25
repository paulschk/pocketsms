<div align="center">

<img src="docs/logo.svg" alt="pocketsms" width="88">

# pocketsms

**A self-hosted SMS gateway for the 4G router your ISP gave you.**

Orange Airbox · SFR · Bouygues · Free — send and receive SMS from your own box, with a
REST API, a web interface, and an SMS-OTP endpoint your other services can call for
two-step verification.

![license](https://img.shields.io/badge/license-MIT-blue.svg)
![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![docker](https://img.shields.io/badge/docker-ready-blue.svg)

</div>

---

## What it does

- **Sends and receives SMS** through the SIM in a router you already have.
- **REST API and web interface**, with scoped API keys.
- **SMS one-time passwords** for two-step verification on your other services.
- **Texts you when the cable drops**, and moves the default route to the 4G router while
  it is down — see [When the cable drops](#when-the-cable-drops).
- **No dependencies**: the Python standard library, and SQLite for storage.
- **Your messages stay yours**: a file on your disk, not a provider's dashboard.

The router's own hotspot keeps working — pocketsms talks to it over USB/HTTP, so it needs
no serial port, no AT commands and no device node.

## What you need

A Huawei **HiLink** router (most French ISP 4G boxes are) and a SIM with an SMS plan.
French operators stopped SIM-locking equipment years ago, so any SIM works.

| Router | Status |
|---|---|
| **Orange Airbox 4G (Huawei E5573Bs-320)** | Orange — developed and tested on this unit |
| Orange Airbox 4G+ (E5576), SFR Pocket (E5577, E5372), Bouygues and Free Huawei keys | expected — same HiLink API |
| Huawei E3372h, E8372h (unbranded, HiLink mode) | expected — same HiLink API |

> If your modem appears as `/dev/ttyUSB0` and answers `AT` commands, it is a plain serial
> modem and pocketsms is the wrong tool — see [prior art](#prior-art).

## Install

### 1. Make the router reachable

```bash
sudo ./scripts/bringup.sh
```

Two things trip everybody up, and this script handles both:

1. **ZeroCD mode.** These routers boot as a CD-ROM holding the vendor's installer — no
   network, no SMS. One `usb_modeswitch` message turns them into a network device.
2. **The subnet collision.** The router runs its own LAN on `192.168.1.0/24`, the same
   subnet most home networks use. The script binds a **`/32` route** to the router's
   interface, so the router answers at `192.168.1.1` while your LAN keeps its own routing
   untouched.

It is idempotent — run it on every boot. To survive replugs automatically:

```bash
sudo mkdir -p /opt/pocketsms && sudo cp -r scripts /opt/pocketsms/
sudo cp deploy/pocketsms-bringup.service /etc/systemd/system/
sudo cp deploy/99-pocketsms.rules /etc/udev/rules.d/
sudo systemctl daemon-reload && sudo systemctl enable --now pocketsms-bringup
```

Different model? Check `lsusb` while it is cold and adjust the USB IDs and the interface
MAC in `deploy/99-pocketsms.rules`.

### 2. Run it

```bash
cp .env.example .env      # optional - the defaults work
docker compose up -d
docker compose logs -f    # the first-run admin password is printed here
```

`docker-compose.yml` is short; this is all of it:

```yaml
services:
  pocketsms:
    image: ghcr.io/paulschk/pocketsms:latest   # `build: .` tags a local build the same way
    build: .
    restart: unless-stopped
    network_mode: host            # the router's LAN collides with yours - see below
    env_file: [.env]              # optional
    volumes: [pocketsms-data:/data]
    security_opt: [no-new-privileges:true]     # no capabilities, no devices, no root

  line-watch:                     # only with: docker compose --profile failover up -d
    image: ghcr.io/paulschk/pocketsms:latest
    profiles: ["failover"]
    restart: unless-stopped
    network_mode: host
    entrypoint: ["/app/scripts/line-watch.sh"]
    user: "0:0"                   # it writes root-owned files; capabilities need root
    cap_drop: [ALL]
    cap_add: [NET_ADMIN, NET_RAW]
    volumes:
      - /etc/resolv.conf:/etc/resolv.conf
      - /etc/pocketsms-line-watch.conf:/etc/pocketsms-line-watch.conf:ro
      - pocketsms-watch:/run/pocketsms-line-watch

volumes:
  pocketsms-data:
  pocketsms-watch:
```

`network_mode: host` is not a shortcut: the router advertises its own `192.168.1.0/24`, so
the container has to reach `192.168.1.1` there while the host keeps routing
`192.168.1.0/24` out its own interface. Sharing the host's network stack holds both.

Bare metal instead:

```bash
sudo useradd --system --home /var/lib/pocketsms pocketsms
sudo mkdir -p /var/lib/pocketsms && sudo chown pocketsms:pocketsms /var/lib/pocketsms
sudo pip install .
sudo -u pocketsms python3 -m pocketsms serve   # prints the admin password on first run
```

### 3. Sign in

Open `http://<host>:8090`, sign in as `admin` with the password printed at first start,
and change it in Settings.

## Two modes

pocketsms runs in one of two modes, and the web interface always shows which: a pill in
the header, and *Operating mode* on the dashboard. The mode is **derived, not
configured** — a watcher that keeps reporting is what makes an installation a failover
one, and a watcher that stops is why it falls back to SMS-only.

| | **SMS only**<br>`docker compose up -d` | **SMS + 4G failover**<br>`docker compose --profile failover up -d` |
|---|---|---|
| Send and receive SMS, REST API, web interface | ✅ | ✅ |
| SMS one-time passwords for your other services | ✅ | ✅ |
| A text when your connection drops | — | ✅ |
| Default route moved to the 4G router while it is down | — | ✅ |
| Containers | one, unprivileged | two: the service, plus a privileged watcher |
| What it changes on the host | nothing | the routing table and `/etc/resolv.conf` |

Both modes use the same API and the same database. Turning the failover on later changes
nothing about the SMS side; it adds something that watches the line and tells you about
it. See [When the cable drops](#when-the-cable-drops) for what that watcher needs.

## The web interface

| Tab | For |
|---|---|
| **Dashboard** | which router, operator, signal, battery, line state, recent messages |
| **Compose** | sending a message |
| **Inbox** / **Sent** | what arrived, what went out |
| **Verification** | sending yourself a code and checking it, to test the flow by hand |
| **Settings** | line monitoring, API keys, accounts, password |

## Settings

Most configuration is an environment variable (see [Configuration](#configuration)).
These are stored in the database and edited under **Settings → Line monitoring**, so a
change survives a restart without touching a file:

| Setting | Default | What it does |
|---|---|---|
| Owner phone number | *empty* | where the line alert is texted; empty disables alerts |
| Repeat at most every | `1800` | minimum seconds between two alerts for one outage |
| Text me when the line comes back | `true` | a second message on recovery |
| Line-down / recovery message | French | templates with `{time}` and `{detail}` |

**Send a test SMS** sends exactly what a real alert looks like, prefixed `[test]` so it
cannot be mistaken for one.

## When the cable drops

pocketsms sends over the cellular network, a path your fibre does not share. That is only
worth something if something notices the fibre going away.

```bash
# a key for the watcher, and its config (mode 600: it holds that key)
docker exec pocketsms pocketsms key create line-watch --scope sms
sudo tee /etc/pocketsms-line-watch.conf >/dev/null <<'EOF'
CABLE_IFACE=eth0             # the connection worth watching
MOBILE_IFACE=eth1            # the router's interface
MOBILE_GATEWAY=192.168.1.1
MOBILE_SOURCE=192.168.1.240  # the /32 address the bring-up script assigned
POCKETSMS_URL=http://127.0.0.1:8090
POCKETSMS_API_KEY=pks_...
EOF
sudo chmod 600 /etc/pocketsms-line-watch.conf
```

Then set the owner number under **Settings → Line monitoring**, and start the watcher:

```bash
docker compose --profile failover up -d
```

**Being straight about the privileges**, because this is the one part that cannot be
unprivileged:

- The watcher edits the **host's** routing table and resolvers. That needs `NET_ADMIN` to
  write routes, `NET_RAW` to bind the probe to the cable's device, host networking to act
  on the host at all, and a writable mount of `/etc/resolv.conf` — a container otherwise
  has its own copy, and rewriting that changes nothing. So it runs as a **separate,
  opt-in container** — running as root, with every capability dropped except those two —
  never as part of the main service, which stays capability-free and unprivileged.
- If you would rather run no privileged container at all, run the same script from the
  host instead:

  ```bash
  sudo mkdir -p /opt/pocketsms/scripts
  sudo install -m 755 scripts/line-watch.sh /opt/pocketsms/scripts/
  sudo install -m 644 deploy/pocketsms-line-watch.service /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now pocketsms-line-watch
  ```

Why it behaves the way it does:

- **It probes the cable, not the internet.** The probe is bound to `CABLE_IFACE` with
  `SO_BINDTODEVICE`, so a 4G link already carrying traffic cannot hide a dead cable.
- **It demotes, it does not delete.** The cable's default route is kept at metric 1000 and
  the fallback is added at metric 0. Deleting the cable's last route blinds the probe to
  the cable coming back — the fallback becomes a one-way trap that only a reboot escapes.
- **It swaps the resolvers too**, because the ISP resolver rides the connection that just
  died.
- **It reports both transitions**, and a restart resyncs the recorded state quietly
  instead of leaving a stale "down" on the dashboard.

Two caveats: anything else that rewrites `/etc/resolv.conf` (a DHCP client, Tailscale)
will fight it, and anything transferred while failed over — `docker compose pull`
included — goes over the SIM and spends its allowance.

## Two-step verification

1. Your service posts a phone number to `/api/v1/otp`. pocketsms stores only the code's
   hash, sends it by SMS, and returns a `challenge` id.
2. You show a "type the code" form.
3. Your service posts the challenge and the typed code to `/api/v1/otp/verify`.
   **HTTP 200 means verified.**

Codes are single-use, expire (`OTP_TTL`, 5 minutes), are bound to their challenge, and are
capped per number per hour and per number of wrong attempts.

```bash
curl -s http://localhost:8090/api/v1/otp -H 'X-API-Key: pks_...' \
  -H 'Content-Type: application/json' -d '{"phone":"0612345678","service":"grafana"}'
# => {"challenge":"kJ2f...","phone":"+336****989","digits":6,"expires_in":300}

curl -s http://localhost:8090/api/v1/otp/verify -H 'X-API-Key: pks_...' \
  -H 'Content-Type: application/json' -d '{"challenge":"kJ2f...","code":"123456"}'
# => {"verified":true,...}   or   401 {"error":"invalid_code","detail":"2 attempt(s) left"}
```

Behind Caddy, `forward_auth` turns this into a gate in front of anything you proxy:

```caddyfile
grafana.example.com {
    forward_auth 127.0.0.1:8091 {
        uri /check
        copy_headers X-Verified
    }
    reverse_proxy 127.0.0.1:3000
}
```

## API

`X-API-Key: pks_...` with a scope of `sms`, `otp` or `full` — or a session cookie from the
web interface.

| Method | Path | Scope | What it does |
|---|---|---|---|
| `GET` | `/api/v1/health` | — | liveness and version |
| `GET` | `/api/v1/status` | `sms` | device, operator, signal, SIM, counters, line state |
| `GET` | `/api/v1/messages?box=inbox\|sent` | `sms` | stored messages |
| `POST` | `/api/v1/messages` | `sms` | send a message |
| `GET` | `/api/v1/messages/{id}` | `sms` | one message |
| `POST` | `/api/v1/otp` · `/api/v1/otp/verify` | `otp` | send, or check, a code |
| `GET` | `/api/v1/otp/status?challenge=` | `otp` | state of a challenge |
| `POST` | `/api/v1/line` | `sms` | the watcher reports the line up or down |
| `GET`/`POST` | `/api/v1/settings` | admin | read or change the runtime settings |
| `GET`/`POST`/`DELETE` | `/api/v1/keys[/{id}]` · `/api/v1/users[/{id}]` | admin | keys and accounts |

```bash
curl -s http://localhost:8090/api/v1/messages -H 'X-API-Key: pks_...' \
  -H 'Content-Type: application/json' -d '{"to":"0612345678","text":"hello from pocketsms"}'
```

## Configuration

`POCKETSMS_FOO` and `FOO` both work — in the environment, in `.env` (Docker) or in
`/etc/pocketsms.conf` (bare metal). The environment wins over the file.

| Variable | Default | What it does |
|---|---|---|
| `MODEM_URL` | *autodetected* | router address |
| `MODEM_USER` / `MODEM_PASSWORD` | `admin` / `admin` | the router's web credentials |
| `BIND` / `PORT` | `0.0.0.0` / `8090` | where the API and the web interface listen |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / *generated* | the first-run account |
| `DATA_DIR` | `/data`, else `/var/lib/pocketsms` | where the database lives |
| `POLL_INTERVAL` | `15` | seconds between inbound polls |
| `OTP_LENGTH` / `OTP_TTL` | `6` / `300` | code length and lifetime |
| `OTP_MAX_ATTEMPTS` / `OTP_MAX_PER_HOUR` | `5` / `5` | limits, per phone number |
| `OTP_MESSAGE` | French | template: `{code}`, `{service}`, `{minutes}` |
| `RELAY_ENABLED` | `false` | forward inbound messages |
| `NTFY_URL` / `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` / `WEBHOOK_URL` | — | where to forward them |
| `LINE_ALERT_PHONE` | *empty* | owner number; normally set in the interface |
| `LINE_ALERT_COOLDOWN` · `LINE_ALERT_ON_RECOVERY` · `LINE_DOWN_MESSAGE` · `LINE_UP_MESSAGE` | see Settings | the alert's own settings |
| `LOG_LEVEL` | `info` | `debug` explains what the router said |

## Security

- Passwords are PBKDF2-HMAC-SHA256, 260 000 rounds, per-account salt. API keys are
  SHA-256 fingerprints, with the plaintext shown once.
- Sessions are opaque random tokens in an `HttpOnly; SameSite=Lax` cookie, and every
  state-changing browser call must also carry `X-Requested-With: pocketsms` — a
  cross-site form cannot set that header.
- Failed sign-ins are throttled per source address. OTP codes are never logged, never
  returned, and only ever echoed masked (`+336****989`).
- **Keep it on your LAN.** It is built to reach a router on your own network, not to face
  the internet: bind it to a private address, or put TLS and authentication in front.
- Change the generated password on first sign-in. It is printed to the service log, so
  anything that can read that log — `docker logs`, the journal — has already seen it.

## Troubleshooting

**Start with `pocketsms doctor`**: it checks the config, the router, the login, the SIM,
the signal and the database, and tells you which step failed.

| Symptom | Cause | Fix |
|---|---|---|
| the router never answers | still in ZeroCD mass-storage mode | `sudo ./scripts/bringup.sh` |
| it answered before, silent now | subnet collision, or the route was lost on replug | re-run `bringup.sh`; check `ip route get 192.168.1.1` |
| `error 108002` | wrong router password | set `MODEM_PASSWORD` (Orange Airbox default: `admin`) |
| `ConnectionStatus 902` | the router itself has no data session | **harmless for SMS** — the radio is registered. Unrelated to the failover, which watches your cable, not the modem |
| the signal indicator shows `1/5` and no dBm | normal on this firmware: it leaves `SignalStrength` and every dBm field empty and reports only a bar level | the indicator uses the module's own level (`SignalIcon` against `maxsignal`); a low reading means the router is badly placed for radio |
| nothing ever arrives in the inbox | some firmwares keep SMS on the SIM only | check the router's own web page |
| a message arrived twice | should not happen | deduplication is by fingerprint — open an issue |

## How it works

These routers run Huawei's **HiLink** firmware, which exposes a full XML API over HTTP —
that is what pocketsms speaks, through `pocketsms/hilink.py`, a single self-contained
client you can reuse. Inbound SMS has no push notification, so the poller fetches the
inbox every `POLL_INTERVAL` seconds and deduplicates by message fingerprint, because the
router re-sends its whole inbox every time.

## Development

```bash
pip install -e . && pytest -q      # the suite runs offline: no router needed
```

The image is published to `ghcr.io/paulschk/pocketsms` for amd64 and arm64 by
`.github/workflows/image.yml` on every push to `main`.

## Prior art

Inspired by [mattboston/sms-gateway](https://github.com/mattboston/sms-gateway), which
does the same job for serial modems over AT commands. If your modem is a `/dev/ttyUSB0`
one, that project is the better fit — pocketsms exists because HiLink devices have no
serial port at all.

## License

MIT — see [LICENSE](LICENSE).
