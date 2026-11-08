#!/usr/bin/env bash
# (Re)starts the srsRAN/OCUDU gNB from the config rendered by gnb_slicer.py.
# Called by agents/enforcers/gnb_slicer.py with GNB_CONFIG_OUT and GNB_LOG set.
#
#   GNB_BIN         gnb binary (default: gnb on PATH; e.g. ./gnb with GNB_DIR)
#   GNB_DIR         directory to start it from (relative paths in the configs)
#   GNB_CPUS        CPU list for taskset, e.g. 0-17 (default: no pinning)
#   GNB_EXTRA_ARGS  extra arguments after the rendered config,
#                   e.g. "-c metricas.yml"
#
# UNISINOS lab (OCUDU): GNB_DIR=<pasta do gnb> GNB_BIN=./gnb GNB_CPUS=0-17
#                       GNB_EXTRA_ARGS="-c metricas.yml"
#   -> taskset -c 0-17 ./gnb -c /tmp/gnb.minas.yaml -c metricas.yml
# Must be run as root (DPDK / hugepages).
set -euo pipefail
GNB_BIN="${GNB_BIN:-gnb}"
CFG="${GNB_CONFIG_OUT:?}"
LOG="${GNB_LOG:-/tmp/gnb.stdout}"
PROC="$(basename "$GNB_BIN")"
PIDFILE="${GNB_PIDFILE:-/tmp/gnb.minas.pid}"

# Stop only the gNB this script started (pidfile), never someone else's: on
# the UNISINOS server the lab's own gNB runs as a k3s pod, and killing it
# would just make k3s restart it and fight us for the RU.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  pid=$(cat "$PIDFILE")
  kill -INT "$pid" || true
  for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  kill -KILL "$pid" 2>/dev/null || true
fi
rm -f "$PIDFILE"
if pgrep -x "$PROC" >/dev/null; then
  echo "gnb_restart.sh: another $PROC is running (PID $(pgrep -x "$PROC" | xargs)), not started by MINAS." >&2
  echo "Stop the lab's gNB first (UNISINOS: ran/ocudu/gnb.sh stop)." >&2
  exit 1
fi
[ -n "${GNB_DIR:-}" ] && cd "$GNB_DIR"
PIN=()
[ -n "${GNB_CPUS:-}" ] && PIN=(taskset -c "$GNB_CPUS")
: > "$LOG"   # (gnb_slicer.py also empties it before calling this)
# shellcheck disable=SC2086  # GNB_EXTRA_ARGS is a word list on purpose
nohup "${PIN[@]}" "$GNB_BIN" -c "$CFG" ${GNB_EXTRA_ARGS:-} > "$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"   # taskset execs the gnb, so $! is the gnb's own PID
disown
