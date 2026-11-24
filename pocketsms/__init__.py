"""pocketsms - a self-hosted SMS gateway for the 4G routers handed out by French ISPs.

The Orange Airbox, SFR's pocket routers and most ISP-branded 4G dongles are
Huawei hardware running the HiLink firmware. That firmware exposes a full XML
API over HTTP: send and read SMS, query signal, inspect the SIM. This project
turns that into a small self-hosted service with a REST API, a web interface,
and an SMS one-time-password endpoint other services can call for two-step
verification.

It talks HTTP to the router instead of AT commands over a serial port, because
these devices do not expose a serial interface at all.
"""

__version__ = "0.1.0"

NAME = "pocketsms"
TAGLINE = "Self-hosted SMS gateway for ISP-provided 4G routers"
DESCRIPTION = (
    "Send and receive SMS through the 4G router your ISP gave you "
    "(Orange Airbox, SFR, Bouygues, Free), with a REST API, a web UI, "
    "and an SMS-OTP endpoint for two-step verification."
)

# Kept in one place so the project is trivial to rename.
GITHUB_TOPICS = [
    "sms-gateway", "hilink", "orange-airbox", "4g-router", "sms", "otp",
    "two-factor-authentication", "self-hosted", "docker", "huawei", "python",
]
