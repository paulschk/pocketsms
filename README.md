<div align="center">

# pocketsms

**A self-hosted SMS gateway for the 4G router your ISP gave you.**

Orange Airbox · SFR · Bouygues · Free — send and receive SMS from your own box,
with a REST API, a web interface, and an SMS-OTP endpoint your other services can
call for two-step verification.

![license](https://img.shields.io/badge/license-MIT-blue.svg)
![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)
![docker](https://img.shields.io/badge/docker-ready-blue.svg)

</div>

---

## Why

Cloud SMS providers want paperwork before they will let you send a text: brand
registration, campaign vetting, template approval, per-segment billing, and a
standing risk of silent filtering. For a home lab or a small alerting setup that
is a lot of ceremony to deliver one message.

Meanwhile there is probably a 4G router in a drawer, given away by an ISP with
a mobile plan. It already has a SIM, a radio, and a SIM toolkit. What it lacks is
a sane way to send and receive messages from your own software.

**pocketsms gives you that.**

- **An alert path that survives your ISP going down.** Monitoring that depends on
  a cloud SMS provider needs working internet to tell you the internet is broken.
  A cellular modem is an independent path: the message still gets through, and you
  can text back.
- **Your messages stay yours.** They live in your SQLite file, not in somebody
  else's dashboard with a retention policy you did not choose.
- **Flat cost.** Your existing SIM plan instead of per-message billing.
- **Two-step verification for everything else you run.** One API call sends a code,
  another verifies it. See [Two-step verification](#two-step-verification).

## Compatible hardware

These devices are Huawei hardware running the **HiLink** firmware, which exposes a
full XML API over HTTP. That is what pocketsms talks to. It does **not** need a serial
port, AT commands, or any device node.

| Router | Operator | Status |
|---|---|---|
| **Orange Airbox 4G (Huawei E5573Bs-320)** | Orange | ✅ developed and tested against this unit |
| Orange Airbox 4G+ (Huawei E5576) | Orange | expected — same HiLink API |
| SFR Pocket / clés 4G (Huawei E5577, E5372) | SFR | expected — same HiLink API |
| Bouygues 4G box / clés Huawei | Bouygues | expected — same HiLink API |
| Free / autres clés Huawei HiLink | Free | expected — same HiLink API |
| Huawei E3372h, E8372h (unbranded, HiLink mode) | — | expected — same HiLink API |

Two useful things about these French ISP routers:

- **They are not SIM-locked.** French operators stopped locking equipment years
  ago, and if you have an older locked unit, the operator gives you the unlock code
  for free on request. Any SIM with a voice/SMS plan works.
- **They are also Wi-Fi access points.** pocketsms uses the USB/HTTP path so the
  router's own hotspot stays available for whatever else you want it for.

> If your modem shows up as `/dev/ttyUSB0` and answers `AT` commands, it is a plain
> serial modem, not a HiLink device — pocketsms is the wrong tool. See
> [prior art](#prior-art).

## Features

- **Send and receive SMS** through the router's own SIM
- **REST API** with scoped API keys (`sms`, `otp`, `full`)
- **Web interface** with sign-in, inbox, outbox, compose and settings — one
  self-contained page, no CDN, no build step
- **SMS one-time passwords** for two-step verification in your other services:
  single-use codes, expiry, attempt limits, per-number rate limiting
- **Inbound relays**: ntfy, Telegram, or a JSON webhook, so a text can wake up
  your monitoring or your phone
- **SQLite** storage, WAL mode, message deduplication (the router re-sends its
  whole inbox on every poll)
- **No dependencies.** Standard library only — nothing to keep patched, nothing to
  build, runs on a Raspberry Pi or a NAS
- **Docker ready** (or plain systemd, your choice)
- **`pocketsms doctor`**: one command that explains why the router is not answering

## Quick start

### 1. Make the router reachable (do not skip this)

Two things trip everybody up, and pocketsms ships a script for both:

```bash
sudo ./scripts/bringup.sh
```

It handles:

1. **ZeroCD mode.** These routers boot presenting themselves as a CD-ROM with the
   vendor's installer on it — no network, no SMS, nothing. One `usb_modeswitch`
   message turns them into a CDC-Ethernet device.
2. **The subnet collision.** Most of these routers run their own LAN on
   `192.168.1.0/24`, which is exactly what a typical home network uses. The script
   adds a **`/32` route** bound to the router's interface, so the router answers at
   `192.168.1.1` while your LAN keeps routing `192.168.1.0/24` out its own
   interface, untouched. No network namespace, no re-plumbing.

It is idempotent, so run it on every boot. For automatic recovery after a replug,
install the units and the udev rule:

```bash
sudo mkdir -p /opt/pocketsms && sudo cp -r scripts /opt/pocketsms/
sudo cp deploy/pocketsms-bringup.service /etc/systemd/system/
sudo cp deploy/99-pocketsms.rules /etc/udev/rules.d/
sudo systemctl daemon-reload && sudo systemctl enable --now pocketsms-bringup
```

> Your router is a different model? Check `lsusb` while it is cold and adjust the
> USB IDs and the interface MAC in `deploy/99-pocketsms.rules`. If port 80 on
> `192.168.1.1` is free on your LAN, pick a different `POCKETSMS_LOCAL_IP`.

### 2. Run it

**Docker** (recommended). The repository ships this `docker-compose.yml`; clone and
use it, or paste it next to your `.env`:

```yaml
services:
  pocketsms:
    build: .                              # or: image: ghcr.io/paulschk/pocketsms:latest
    container_name: pocketsms
    restart: unless-stopped
    network_mode: host                    # the router advertises its own 192.168.1.0/24
    env_file:
      - path: .env
        required: false        # so `docker compose up -d` works with no .env at all
    volumes:
      - pocketsms-data:/data
    security_opt:
      - no-new-privileges:true

volumes:
  pocketsms-data:
```

The image is published to `ghcr.io/paulschk/pocketsms` on every push to `main` by
`.github/workflows/image.yml`, for `amd64` and `arm64`, so you can drop `build:` and
use `image:` alone if you would rather not build it yourself.

`network_mode: host` is not a convenience shortcut. Most of these routers run their
own LAN on `192.168.1.0/24` — the very subnet a typical home network uses — so the
container has to reach `192.168.1.1` there while the host keeps routing
`192.168.1.0/24` out its own interface. Sharing the host's network stack is the
simplest way to hold both. No devices, no capabilities and no privileged mode are
needed: the modem is spoken to over HTTP like any other host on the network.

```bash
cp .env.example .env      # optional — the defaults work
docker compose up -d
docker compose logs -f    # the first-run admin password is printed here
```

**Bare metal:**

```bash
sudo useradd --system --home /var/lib/pocketsms pocketsms
sudo mkdir -p /var/lib/pocketsms && sudo chown pocketsms:pocketsms /var/lib/pocketsms
sudo pip install .                       # or: pipx install .
sudo -u pocketsms python3 -m pocketsms serve # prints the admin password on first run
```

If you keep a config file at `/etc/pocketsms.conf`, it must be readable by the
service account — it holds the router password, so do not make it world-readable:

```bash
sudo chown root:pocketsms /etc/pocketsms.conf && sudo chmod 640 /etc/pocketsms.conf
```

**systemd**, once installed:

```bash
sudo cp deploy/pocketsms.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pocketsms
```

### 3. Sign in

Open `http://<host>:8090`. On first start pocketsms creates an `admin` account and
prints the generated password **once**, in the log:

```
==============================================================
  pocketsms: first run, an account has been created

    username: admin
    password: 3Ykr9cQmQzVp

    Sign in and change it in Settings.
==============================================================
```

Set `ADMIN_PASSWORD` in the environment if you would rather choose it yourself.

## Configuration

Every setting is an environment variable. `POCKETSMS_FOO` and `FOO` both work.

| Variable | Default | What it does |
|---|---|---|
| `MODEM_URL` | *autodetected* | Router address. Tries `192.168.1.1`, `192.168.8.1`, `192.168.0.1`, … in order |
| `MODEM_USER` / `MODEM_PASSWORD` | `admin` / `admin` | Router's web interface credentials |
| `BIND` / `PORT` | `0.0.0.0` / `8090` | Where the API and the web UI listen |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / *generated* | First-run account |
| `DATA_DIR` | `/data`, else `/var/lib/pocketsms` | Where the database lives |
| `POLL_INTERVAL` | `15` | Seconds between inbound polls (HiLink has no push) |
| `OTP_LENGTH` / `OTP_TTL` | `6` / `300` | Code length and lifetime |
| `OTP_MAX_ATTEMPTS` / `OTP_MAX_PER_HOUR` | `5` / `5` | Brute-force and abuse limits, per phone number |
| `OTP_MESSAGE` | French default | Template: `{code}`, `{service}`, `{minutes}` |
| `RELAY_ENABLED` | `false` | Turn the inbound relays on |
| `NTFY_URL` / `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | — | Where inbound messages are forwarded |
| `WEBHOOK_URL` | — | Gets `{"from": "...", "text": "..."}` for every inbound SMS |
| `LOG_LEVEL` | `info` | `debug` is verbose but explains what the router said |
| `LINE_ALERT_PHONE` | *empty* | Owner number for the line-down alert; empty disables it |
| `LINE_ALERT_COOLDOWN` | `1800` | Minimum seconds between two alerts for the same outage |
| `LINE_ALERT_ON_RECOVERY` | `true` | Also text when the primary line comes back |
| `LINE_DOWN_MESSAGE` / `LINE_UP_MESSAGE` | French defaults | Templates: `{time}`, `{detail}` |

The `LINE_ALERT_*` settings are also in the web UI under **Settings → Line
monitoring**, where they are stored in the database and outrank the environment -
an administrator who changes a setting in the UI expects it to stay changed.

There is also `/etc/pocketsms.conf` (bare metal) or `.env` (Docker) if you prefer a
file. Environment wins over the file.

## API

Authenticate with `X-API-Key: pks_...` (create keys in the web UI or with
`pocketsms key create grafana --scope otp`). The web UI uses a session cookie
instead.

| Method | Path | Scope | What it does |
|---|---|---|---|
| `GET` | `/api/v1/health` | — | Liveness and version |
| `GET` | `/api/v1/status` | `sms` | Device, operator, signal, SIM, counters |
| `GET` | `/api/v1/messages?box=inbox\|sent` | `sms` | Stored messages |
| `POST` | `/api/v1/messages` | `sms` | Send a message |
| `GET` | `/api/v1/messages/{id}` | `sms` | One message |
| `POST` | `/api/v1/otp` | `otp` | Send a verification code |
| `POST` | `/api/v1/otp/verify` | `otp` | Check a code |
| `GET` | `/api/v1/otp/status?challenge=` | `otp` | State of a challenge |
| `GET`/`POST`/`DELETE` | `/api/v1/keys[/{id}]` | admin | Manage API keys |
| `GET`/`POST`/`DELETE` | `/api/v1/users[/{id}]` | admin | Manage accounts |
| `POST` | `/api/v1/line` | `sms` | The host watcher reports the primary line up or down |
| `GET`/`POST` | `/api/v1/settings` | admin | Read or change the runtime settings |
| `POST` | `/api/v1/auth/login\|logout` | — | Web session |

```bash
# send a message
curl -s http://localhost:8090/api/v1/messages \
  -H 'X-API-Key: pks_...' -H 'Content-Type: application/json' \
  -d '{"to":"0612345678","text":"hello from pocketsms"}'

# read what came in
curl -s 'http://localhost:8090/api/v1/messages?box=inbox&limit=20' -H 'X-API-Key: pks_...'

# ask the router how it is doing
curl -s http://localhost:8090/api/v1/status -H 'X-API-Key: pks_...'
```

Numbers are normalised for you: `06 65 42 39 89`, `0665423989` and `+33665423989`
all become `+33665423989`.

## Two-step verification

The point of this feature: a text message your users already trust, sent by
hardware you own, with no provider in the middle.

Flow:

1. Your service posts the user's phone number to `/api/v1/otp`. pocketsms generates
   a code, stores only its hash, sends it by SMS, and returns a `challenge` id.
2. Your service shows a "type the code" form.
3. Your service posts the challenge id and the typed code to `/api/v1/otp/verify`.
   **HTTP 200 means verified.** Anything else means no.

Guarantees: the code is single-use, expires (`OTP_TTL`, 5 minutes by default),
is bound to its challenge (a leaked hash is useless elsewhere), survives a fixed
number of wrong guesses (`OTP_MAX_ATTEMPTS`), and the number of codes per phone
is capped per hour (`OTP_MAX_PER_HOUR`).

```bash
# 1. send
curl -s http://localhost:8090/api/v1/otp -H 'X-API-Key: pks_...' \
  -H 'Content-Type: application/json' \
  -d '{"phone":"0612345678","service":"grafana"}'
# => {"challenge":"kJ2f...","phone":"+336****989","digits":6,"expires_in":300}

# 2. verify
curl -s http://localhost:8090/api/v1/otp/verify -H 'X-API-Key: pks_...' \
  -H 'Content-Type: application/json' \
  -d '{"challenge":"kJ2f...","code":"123456"}'
# => {"verified":true,...}   or  401 {"error":"invalid_code","detail":"2 attempt(s) left"}
```

### Putting it in front of a service (Caddy example)

`forward_auth` makes this a gate for anything Caddy proxies. Caddy asks a small
snippet whether the request may pass; if the user has not verified in this
session, the snippet sends a code and shows the form.

```caddyfile
grafana.example.com {
    forward_auth 127.0.0.1:8091 {
        uri /check
        copy_headers X-Verified
    }
    reverse_proxy 127.0.0.1:3000
}
```

The snippet behind `127.0.0.1:8091` keeps a signed cookie of its own and calls
pocketsms's two endpoints. Whatever language you write it in, it is the same three
steps as above — that is the whole contract.

## When the cable drops

pocketsms sends over the cellular network, a path your fibre does not share. That is
only worth anything if something notices the fibre going away: `scripts/line-watch.sh`
does the noticing and moves the default route, and pocketsms sends the text.

```bash
sudo mkdir -p /opt/pocketsms/scripts
sudo install -m 755 scripts/line-watch.sh /opt/pocketsms/scripts/
sudo install -m 644 deploy/pocketsms-line-watch.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pocketsms-line-watch
```

Configure it in `/etc/pocketsms-line-watch.conf` — mode `600`, it holds an API key:

```bash
CABLE_IFACE=eth0             # the connection worth watching
MOBILE_IFACE=eth1            # the router's interface
MOBILE_GATEWAY=192.168.1.1
MOBILE_SOURCE=192.168.1.240  # the /32 address the bring-up script assigned
POCKETSMS_URL=http://192.168.1.55:8090
POCKETSMS_API_KEY=pks_…      # pocketsms key create line-watch --scope sms
```

Then set the owner number once in **Settings → Line monitoring**. That is where the
alert goes; leave it empty and nothing is sent.

Why it is built the way it is:

- **It probes the cable, not the internet.** The probe is bound to `CABLE_IFACE` with
  `SO_BINDTODEVICE`, so a 4G link that is already carrying traffic cannot hide a dead
  cable.
- **It demotes, it does not delete.** On failure the cable's default route is kept at a
  high metric and the fallback is added at metric 0. Deleting the cable's last route
  blinds the probe to the cable coming back: the fallback becomes a one-way trap that
  only a reboot escapes.
- **It swaps the resolvers too.** The ISP box is still on the LAN, but its upstream is
  gone, so name resolution through it dies with the line.
- **It reports both transitions**, and pocketsms texts the owner once per outage
  (`LINE_ALERT_COOLDOWN`), plus a recovery message if you asked for one.
- **A restart resyncs the recorded state quietly** instead of leaving a stale "down" on
  the dashboard or sending a text nobody's outage earned.

Detection lives on the host rather than in the container on purpose: proving the cable
path needs a capability the service deliberately does not hold. The phone number, the
templates and the modem all stay in pocketsms.

Two caveats: anything else that rewrites `/etc/resolv.conf` (a DHCP client, Tailscale)
will fight it, and traffic over the cellular link spends that SIM's allowance — an
Orange cap tends to arrive as a dead session rather than as a portal page.

## Security notes

- Passwords are stored as PBKDF2-HMAC-SHA256, 260 000 rounds, per-account salt.
  API keys are stored as SHA-256 fingerprints; the plaintext is shown once.
- Web sessions are opaque random tokens in an `HttpOnly; SameSite=Lax` cookie.
  Every state-changing call from a browser must also carry
  `X-Requested-With: pocketsms`, which a cross-site form cannot set — so the cookie
  is not CSRF-exploitable.
- Failed sign-ins are throttled per source address (10 per 5 minutes).
- OTP codes are never logged, never returned by the API, and never stored in the
  clear. The API only ever echoes a masked number (`+336****989`).
- **The service reaches a router on your LAN. Keep it on your LAN**: bind it to a
  private address, or put it behind your reverse proxy with TLS and
  authentication. It is not designed to be exposed to the internet.
- If a number receives two-factor codes for your bank, think twice before
  forwarding that inbox to a chat app.
- Change the generated password on first sign-in. It is printed to the service
  log at first run, so anything able to read that log — `docker logs`, the
  journal, a log shipper — has already seen it.

## Troubleshooting

**Start with `pocketsms doctor`.** It checks the config, probes the router, the
login, the SIM, the signal and the database, and tells you which step failed.

| Symptom | Cause | Fix |
|---|---|---|
| Router not answering at all | still in ZeroCD mass-storage mode | `sudo ./scripts/bringup.sh` |
| Modem answered before, silent now | subnet collision, or the /32 route was lost on replug | re-run `bringup.sh`; check `ip route get 192.168.1.1` |
| `login refused: error 108002` | wrong router password | set `MODEM_PASSWORD` (Orange Airbox default: `admin`) |
| `error 100003` in the log | the session was not authenticated | harmless, pocketsms re-logs immediately |
| Messages arrive twice | should not happen | deduplication is by message fingerprint; if it does, open an issue |
| Nothing ever arrives in the inbox | the router keeps SMS on the SIM only | check the router's own web page; some firmwares store per slot |
| `state 902` in `/status` | no mobile data | **does not affect SMS** — the radio is registered |

Useful commands:

```bash
journalctl -u pocketsms -f            # live log
pocketsms doctor                      # full diagnostic
lsusb                               # 12d1:1f01 = still ZeroCD, 12d1:1c2b = ready
ip route get 192.168.1.1            # must go out the router's interface
curl -s http://192.168.1.1/api/webserver/SesTokInfo   # does the router answer at all?
```

## How it works

These routers implement the Huawei HiLink XML API. pocketsms speaks it over HTTP
instead of AT commands, because the hardware has no serial interface.

The parts that are genuinely hard, and that this project already handles:

- Login is mandatory; the password is hashed as
  `base64(sha256hex(user + base64(sha256hex(password)) + token))`.
- The CSRF token is **single-use**. Login returns a `#`-separated queue of about
  thirty of them; pocketsms consumes one per request and re-authenticates when the
  queue runs dry.
- The authenticated session is the login response's `Set-Cookie`, **not** the
  `SessionID` that `SesTokInfo` hands out. Reusing the latter yields `100003`
  forever — a genuinely maddening way to spend an evening.
- There is no push notification for inbound SMS, so polling plus fingerprint
  deduplication is the only correct design.

`pocketsms/hilink.py` is a single self-contained client if you want to reuse it.

## Development

```bash
python -m pytest -q          # no modem, no network needed
python -m pocketsms doctor     # against your real router
ruff check .                 # optional
```

Layout:

```
pocketsms/
  __main__.py   CLI: serve, doctor, user, key
  config.py     settings and modem autodetection
  hilink.py     the HiLink client (start here)
  store.py      SQLite: messages, users, sessions, keys, OTP
  otp.py        two-step verification
  poller.py     inbound polling and relays
  server.py     HTTP routing, auth, scopes
  webui.py      renders the page, injecting name and version
  webui.html    the whole web interface, inlined
scripts/bringup.sh    ZeroCD + /32 route
deploy/               systemd units, udev rule
```

Renaming the project is a `sed` away: the name lives in `pocketsms/__init__.py`,
`pyproject.toml` and the README.

## Prior art

[mattboston/sms-gateway](https://github.com/mattboston/sms-gateway) is a good
project doing the same job for **serial/AT modems** (`/dev/ttyUSB0`, `AT+CMGS`).
If that is what you own, use it. pocketsms exists because ISP-provided routers are
not serial modems, and because the AT transport desynchronises the moment an
unsolicited notification lands in the middle of a write.

## License

MIT — see [LICENSE](LICENSE).
