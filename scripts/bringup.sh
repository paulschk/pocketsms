#!/usr/bin/env bash
# Make an ISP-provided 4G router reachable from this host.
#
# Two problems, both solved here, both of which look like "the software is
# broken" if you do not know about them:
#
#  1. These routers boot in ZeroCD mass-storage mode: they present themselves as
#     a CD-ROM with the vendor's installer on it and offer no network at all.
#     One usb_modeswitch message turns them into a CDC-Ethernet device.
#
#  2. Their own LAN is 192.168.1.0/24 on most French ISP boxes - the same subnet
#     as a typical home network. The fix is not a network namespace but a /32
#     route bound to the router's interface: the router answers at 192.168.1.1
#     while the LAN's own /24 route keeps going out the LAN interface untouched.
#
# Idempotent: safe on every boot, every replug, and by hand.

set -u

IFACE="${HILINKD_IFACE:-eth1}"          # interface the router appears on
ROUTER_IP="${HILINKD_ROUTER_IP:-192.168.1.1}"
LOCAL_IP="${HILINKD_LOCAL_IP:-192.168.1.240}"   # must be free on your LAN
ZEROCD_IDS="12d1:1f01"
MODE_MESSAGE='55534243123456780000000000000011062000000101000100000000000000'

say() { printf '[hilinkd-bringup] %s\n' "$*"; }

# 1. Out of ZeroCD, if it is still there.
if command -v lsusb >/dev/null && lsusb | grep -q "$ZEROCD_IDS"; then
  say "router in ZeroCD mode, switching"
  if command -v usb_modeswitch >/dev/null; then
    usb_modeswitch -v 12d1 -p 1f01 -M "$MODE_MESSAGE" >/dev/null 2>&1 || true
    for _ in $(seq 1 15); do
      lsusb | grep -q "$ZEROCD_IDS" || break
      sleep 1
    done
  else
    say "usb_modeswitch is not installed: apt install usb-modeswitch"
  fi
fi

# 2. Wait for the interface.
for _ in $(seq 1 20); do
  [ -e "/sys/class/net/$IFACE" ] && break
  sleep 1
done
if [ ! -e "/sys/class/net/$IFACE" ]; then
  say "interface $IFACE is absent - is the router plugged in?"
  exit 1
fi

# 3. Address and route without disturbing the LAN.
ip link set "$IFACE" up
ip addr replace "${LOCAL_IP}/32" dev "$IFACE"
ip route replace "${ROUTER_IP}/32" dev "$IFACE" src "$LOCAL_IP"

# 4. Prove it answers before claiming success.
for _ in $(seq 1 10); do
  if curl -s -m 3 "http://${ROUTER_IP}/api/webserver/SesTokInfo" | grep -q SesInfo; then
    say "router reachable at ${ROUTER_IP} via ${IFACE} (${LOCAL_IP}/32)"
    exit 0
  fi
  sleep 2
done

say "the router did not answer on ${ROUTER_IP}"
say "check that ${LOCAL_IP} is free on your LAN - a collision makes this look broken"
exit 1
