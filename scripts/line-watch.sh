#!/usr/bin/env bash
# Keep the primary connection honest, and fall back to the 4G router when it dies.
#
#   - probes the CABLE by binding the probe to that device, so a 4G link that is
#     already carrying traffic cannot hide a dead cable;
#   - when it fails, points the default route at the 4G router and swaps in public
#     resolvers (the ISP box stays on the LAN, but its upstream is gone, so name
#     resolution through it dies with the line);
#   - when it returns, puts the original route and resolvers back;
#   - reports either way to pocketsms, which texts the owner.
#
# Configuration comes from the environment, or from /etc/pocketsms-line-watch.conf.
# Every action is reversible and the original state is captured before anything is
# changed, so a restart can never mistake the 4G fallback for the original route.

set -u

CABLE_IFACE="${CABLE_IFACE:-eth0}"
MOBILE_IFACE="${MOBILE_IFACE:-eth1}"
MOBILE_GATEWAY="${MOBILE_GATEWAY:-192.168.1.1}"
MOBILE_SOURCE="${MOBILE_SOURCE:-192.168.1.240}"
PROBE_URL="${PROBE_URL:-http://1.1.1.1/}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-5}"
INTERVAL="${INTERVAL:-10}"
FAIL_AFTER="${FAIL_AFTER:-3}"
RECOVER_AFTER="${RECOVER_AFTER:-2}"
FALLBACK_DNS="${FALLBACK_DNS:-1.1.1.1 8.8.8.8}"
POCKETSMS_URL="${POCKETSMS_URL:-}"
POCKETSMS_API_KEY="${POCKETSMS_API_KEY:-}"
DRY_RUN="${DRY_RUN:-0}"
STATE_DIR="${STATE_DIR:-/run/pocketsms-line-watch}"
CONF="${POCKETSMS_LINE_CONF:-/etc/pocketsms-line-watch.conf}"

# shellcheck source=/dev/null
[ -r "$CONF" ] && . "$CONF"

mkdir -p "$STATE_DIR" 2>/dev/null || true

log() { printf '[line-watch] %s %s\n' "$(date '+%F %T')" "$*"; }
run() {
  if [ "$DRY_RUN" = "1" ]; then log "dry-run: $*"; else "$@"; fi
}

json_detail() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

# --- capture the original default route, once ---------------------------------
ROUTE_FILE="$STATE_DIR/original-route"
if [ -r "$ROUTE_FILE" ]; then
  read -r ORIGINAL_GW ORIGINAL_DEV <"$ROUTE_FILE"
else
  ORIGINAL_GW=$(ip route show default | awk 'NR==1{print $3}')
  ORIGINAL_DEV=$(ip route show default | awk 'NR==1{print $5}')
  [ -n "${ORIGINAL_GW:-}" ] && [ -n "${ORIGINAL_DEV:-}" ] \
    && printf '%s %s\n' "$ORIGINAL_GW" "$ORIGINAL_DEV" >"$ROUTE_FILE"
fi
if [ -z "${ORIGINAL_GW:-}" ] || [ -z "${ORIGINAL_DEV:-}" ]; then
  log "no default route to stand in for; nothing to watch"
  exit 1
fi

RESOLV_BACKUP="$STATE_DIR/resolv.conf.orig"
[ -f "$RESOLV_BACKUP" ] || cp /etc/resolv.conf "$RESOLV_BACKUP" 2>/dev/null || true

cable_is_up() {
  curl -s -o /dev/null --max-time "$PROBE_TIMEOUT" --interface "$CABLE_IFACE" "$PROBE_URL"
}

report() {
  local cable="$1" detail="$2" quiet="false" body
  [ "${3:-}" = "quiet" ] && quiet="true"
  if [ -z "$POCKETSMS_API_KEY" ] || [ -z "$POCKETSMS_URL" ]; then
    log "$cable: $detail (no pocketsms credentials, not reporting)"
    return 0
  fi
  body=$(printf '{"cable":"%s","detail":"%s","quiet":%s}' "$cable" \
                 "$(json_detail "$detail")" "$quiet")
  if curl -s -o /dev/null --max-time 10 -X POST "$POCKETSMS_URL/api/v1/line" \
       -H "X-API-Key: $POCKETSMS_API_KEY" -H 'Content-Type: application/json' \
       --data "$body"; then
    log "$cable: reported to pocketsms"
  else
    log "$cable: could not reach pocketsms at $POCKETSMS_URL"
  fi
}

use_fallback_dns() {
  grep -q 'pocketsms-line-watch' /etc/resolv.conf 2>/dev/null && return 0
  [ "$DRY_RUN" = "1" ] && { log "dry-run: would point /etc/resolv.conf at $FALLBACK_DNS"; return 0; }
  {
    echo '# managed by pocketsms-line-watch while the primary line is down'
    for ns in $FALLBACK_DNS; do echo "nameserver $ns"; done
  } >/etc/resolv.conf
}

restore_dns() {
  grep -q 'pocketsms-line-watch' /etc/resolv.conf 2>/dev/null || return 0
  [ "$DRY_RUN" = "1" ] && { log "dry-run: would restore /etc/resolv.conf"; return 0; }
  [ -f "$RESOLV_BACKUP" ] && cp "$RESOLV_BACKUP" /etc/resolv.conf
}

fail_over() {
  local detail
  if [ ! -e "/sys/class/net/$MOBILE_IFACE" ]; then
    detail="primary line down and $MOBILE_IFACE is not present - is the 4G router plugged in?"
    log "$detail"
    report down "$detail"
    return 0
  fi
  log "primary line down: default route -> $MOBILE_GATEWAY dev $MOBILE_IFACE"
  # The cable's default route is demoted, not deleted. The probe is bound to that
  # device (SO_BINDTODEVICE), so removing the last route on it blinds the watcher
  # and the cable coming back could never be noticed: the fallback would be a
  # one-way trap. Two defaults, ordered by metric, is what keeps it recoverable.
  run ip route replace default via "$ORIGINAL_GW" dev "$ORIGINAL_DEV" metric 1000
  run ip route replace default via "$MOBILE_GATEWAY" dev "$MOBILE_IFACE" \
      src "$MOBILE_SOURCE" metric 0
  use_fallback_dns
  report down "primary line down, default route moved to $MOBILE_IFACE via $MOBILE_GATEWAY"
}

and_restore() {
  log "primary line back: default route -> $ORIGINAL_GW dev $ORIGINAL_DEV"
  # Drop the fallback, then the demoted copy, then put the original back at the
  # top. Doing it in this order means the box always has a default route.
  run ip route del default via "$MOBILE_GATEWAY" dev "$MOBILE_IFACE" 2>/dev/null || true
  run ip route del default via "$ORIGINAL_GW" dev "$ORIGINAL_DEV" metric 1000 2>/dev/null || true
  run ip route add default via "$ORIGINAL_GW" dev "$ORIGINAL_DEV" metric 0
  restore_dns
  report up "primary line back, default route restored via $ORIGINAL_DEV"
}

state="up"
fails=0
oks=0
log "watching $CABLE_IFACE against $PROBE_URL; fallback $MOBILE_IFACE via $MOBILE_GATEWAY"
log "original default: $ORIGINAL_GW dev $ORIGINAL_DEV"
# Sync the recorded state without texting anybody: the watcher only reports
# transitions, so after a restart a stale state would otherwise sit there.
report up "watcher started on $CABLE_IFACE; primary line is up" quiet

stop() {
  log "stopping"
  [ "$state" = "down" ] && and_restore
  exit 0
}
trap stop TERM INT

while :; do
  if cable_is_up; then
    oks=$((oks + 1))
    fails=0
    if [ "$state" = "down" ] && [ "$oks" -ge "$RECOVER_AFTER" ]; then
      state="up"
      and_restore
    fi
  else
    fails=$((fails + 1))
    oks=0
    if [ "$state" = "up" ] && [ "$fails" -ge "$FAIL_AFTER" ]; then
      state="down"
      fail_over
    fi
  fi
  sleep "$INTERVAL"
done
