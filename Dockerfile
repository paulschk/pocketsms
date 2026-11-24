# pocketsms itself has no dependencies: the Python standard library is the whole
# runtime. iproute2 and curl are here for the *optional* failover watcher
# (scripts/line-watch.sh), which reaches the image through a compose profile so it
# can be deployed without installing anything on the host.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="pocketsms" \
      org.opencontainers.image.description="Self-hosted SMS gateway for ISP-provided 4G routers" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/paulschk/pocketsms"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POCKETSMS_DATA_DIR=/data

WORKDIR /app

# Not for the service: it speaks HTTP and needs neither. These are what `ip route`
# and the cable-bound probe need, for the optional line-watch profile.
RUN apt-get update \
 && apt-get install -y --no-install-recommends iproute2 curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY pocketsms ./pocketsms
COPY scripts ./scripts
COPY deploy ./deploy
RUN pip install --no-cache-dir --no-compile .

# An unprivileged account: the service never needs to be root. It talks HTTP to
# the router, so it does not even need access to /dev.
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin pocketsms \
 && mkdir -p /data && chown pocketsms:pocketsms /data

USER pocketsms
VOLUME ["/data"]
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://${POCKETSMS_BIND:-${BIND:-127.0.0.1}}:${POCKETSMS_PORT:-${PORT:-8090}}/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["pocketsms", "serve"]
