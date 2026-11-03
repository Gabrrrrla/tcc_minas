#!/usr/bin/env bash
# (Re)starts the srsRAN gNB from the config rendered by gnb_slicer.py.
# Called by agents/enforcers/gnb_slicer.py with GNB_CONFIG_OUT and GNB_LOG set.
#
#   GNB_BIN   path to the gnb binary (default: gnb on PATH)
#
# Adjust to how the gNB is run in the lab (e.g. a systemd unit:
#   GNB_RESTART_CMD="systemctl restart srsran-gnb" with the unit pointing at
#   GNB_CONFIG_OUT). Must be run as root (DPDK / hugepages).
set -euo pipefail
GNB_BIN="${GNB_BIN:-gnb}"
CFG="${GNB_CONFIG_OUT:?}"
LOG="${GNB_LOG:-/tmp/gnb.stdout}"

if pgrep -x gnb >/dev/null; then
  pkill -INT -x gnb || true
  for _ in $(seq 1 40); do pgrep -x gnb >/dev/null || break; sleep 0.25; done
  pkill -KILL -x gnb 2>/dev/null || true
fi
: > "$LOG"   # (gnb_slicer.py also empties it before calling this)
nohup "$GNB_BIN" -c "$CFG" > "$LOG" 2>&1 < /dev/null &
disown
