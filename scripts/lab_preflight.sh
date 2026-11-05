#!/usr/bin/env bash
# MINAS — pre-flight checks for the lab server (srsRAN gNB native + MINAS in Docker).
# Read-only: it inspects the host and prints PASS / WARN / FAIL, changes nothing.
#
#   bash scripts/lab_preflight.sh            # from the repo root, on the lab host
#
# Reads OLLAMA_URL / MINAS_MODEL / MINAS_TZ from .env when present.

set -u
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; WARNS=$((WARNS + 1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILS=$((FAILS + 1)); }
WARNS=0; FAILS=0

[ -f .env ] && set -a && . ./.env && set +a
OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}
MINAS_MODEL=${MINAS_MODEL:-qwen2.5:7b}
MINAS_TZ=${MINAS_TZ:-America/Sao_Paulo}

echo "== host"
[ "$(uname -s)" = "Linux" ] && pass "Linux $(uname -r)" || fail "not Linux — the lab setup assumes a Linux host"
echo "  RAM: $(free -g 2>/dev/null | awk '/Mem:/{print $2" GB total, "$7" GB available"}')"
echo "  CPUs: $(nproc)"

echo "== docker"
if command -v docker >/dev/null; then
  docker info >/dev/null 2>&1 && pass "docker usable by $(whoami)" || fail "docker installed but not usable (daemon down or user not in 'docker' group)"
  docker compose version >/dev/null 2>&1 && pass "$(docker compose version | head -1)" || fail "docker compose plugin missing"
else
  fail "docker not installed"
fi

echo "== N2 (SCTP) / N3 (GTP-U)"
if lsmod 2>/dev/null | grep -q '^sctp'; then pass "sctp kernel module loaded"
else warn "sctp module not loaded — run: sudo modprobe sctp  (NGAP needs it)"; fi
if ss -A sctp -ln 2>/dev/null | grep -q ':38412'; then
  fail "something already listens on SCTP 38412 (another AMF?) — $(ss -A sctp -lnp 2>/dev/null | grep 38412 | head -1)"
else pass "SCTP 38412 free"; fi
if ss -lun 2>/dev/null | grep -q ':2152 '; then
  fail "UDP 2152 (GTP-U) already in use — another UPF? $(ss -lunp 2>/dev/null | grep ':2152 ' | head -1)"
else pass "UDP 2152 free"; fi
if pgrep -fa 'open5gs-(amf|smf|upf|nrf)d' >/dev/null 2>&1; then
  warn "native Open5GS processes running: $(pgrep -fa 'open5gs-(amf|smf|upf|nrf)d' | awk '{print $2}' | xargs) — stop them or they clash with the containers"
else pass "no native Open5GS processes"; fi

echo "== addressing (docker-compose.yml uses 10.11.0.0/24; UEs 10.45.0.0/16, 10.46.0.0/16)"
clash=$(ip -o addr show 2>/dev/null | grep -v 'br-' | grep " inet 10.11.0." || true)
if [ -n "$clash" ]; then fail "10.11.0.x already used on the host: $(echo "$clash" | awk '{print $2, $4}' | head -2 | xargs)"
else pass "10.11.0.x not used by a host interface"; fi
# The UE pools live only inside the UPF container (own netns, NAT to 10.11.0.22),
# so a host ogstun left by a stopped native Open5GS does not clash.
for net in 10.45. 10.46.; do
  clash=$(ip -o addr show 2>/dev/null | grep -v 'br-' | grep " inet ${net}" || true)
  if [ -n "$clash" ]; then warn "${net}x on a host interface ($(echo "$clash" | awk '{print $2}' | head -1)): fine while the native Open5GS is stopped — the MINAS UPF NATs UE traffic inside its container"
  else pass "${net}x not used by a host interface"; fi
done

echo "== TCP ports published by the stack"
for p in 3000 3001 5432 8000 8001 8002 8080 9090; do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}$"; then warn "TCP ${p} already in use — compose publish will fail for that service"
  else pass "TCP ${p} free"; fi
done

echo "== LLM (Ollama) at ${OLLAMA_URL}"
if curl -sf -m 5 "${OLLAMA_URL}/api/tags" -o /tmp/minas_ollama_tags.json; then
  pass "Ollama reachable from the host"
  grep -q "\"${MINAS_MODEL}\"" /tmp/minas_ollama_tags.json && pass "model ${MINAS_MODEL} present" \
    || fail "model ${MINAS_MODEL} not pulled — ollama pull ${MINAS_MODEL}"
  case "${OLLAMA_URL}" in
    *localhost*|*127.0.0.1*|*host.docker.internal*)
      if ss -ltn 2>/dev/null | grep -q '127.0.0.1:11434'; then
        fail "Ollama on this host listens on 127.0.0.1 only — containers can't reach it. Set OLLAMA_HOST=0.0.0.0 (systemctl edit ollama) and restart"
      fi
      if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
        pass "GPU for Ollama: $(nvidia-smi -L | head -1)"
      else
        warn "Ollama on the gNB host without a GPU: CPU inference competes with the gNB's real-time threads. Prefer another machine (OLLAMA_URL), or pin it off the gNB cores"
      fi
      ;;
  esac
else
  fail "Ollama not reachable at ${OLLAMA_URL}"
fi

echo "== real-time / DPDK (gNB)"
if grep -qE 'isolcpus|nohz_full' /proc/cmdline; then pass "isolated cores: $(grep -oE '(isolcpus|nohz_full)=[^ ]+' /proc/cmdline | xargs)"
else warn "no isolcpus/nohz_full on the kernel cmdline — after compose up, keep the containers off the gNB cores: docker update --cpuset-cpus <free cpus, e.g. 18-31> \$(docker ps -q)"; fi
hp=$(awk '/HugePages_Total/{print $2}' /proc/meminfo 2>/dev/null)
[ "${hp:-0}" -gt 0 ] && pass "hugepages: ${hp}" || warn "no hugepages configured (DPDK fronthaul usually needs them)"

echo "== gnb_slicer.py (runs with sudo python3, next to the gNB)"
if sudo -n python3 -c 'import flask, yaml' 2>/dev/null; then pass "root's python3 has flask + pyyaml"
elif sudo -n true 2>/dev/null; then fail "root's python3 lacks flask/pyyaml — sudo apt install -y python3-flask python3-yaml"
else warn "could not check root's python3 without a password — run: sudo python3 -c 'import flask, yaml'"; fi
TPL=${GNB_TEMPLATE:-srsran/gnb.lab.yaml}
if [ -f "$TPL" ]; then
  grep -q '10.11.0.20' "$TPL" && pass "$TPL points the gNB at the MINAS AMF (10.11.0.20)"     || fail "$TPL does not mention 10.11.0.20 — set cu_cp.amf.addr: 10.11.0.20, bind_addr: 10.11.0.1"
else warn "gNB template $TPL not found (LAB guide, P2)"; fi

echo "== time (enforcement windows are wall-clock)"
tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)
[ "$tz" = "$MINAS_TZ" ] && pass "host timezone ${tz}" || warn "host timezone '${tz}' != MINAS_TZ '${MINAS_TZ}' (containers use MINAS_TZ; fine, but logs will differ)"
[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] && pass "clock NTP-synchronized" \
  || warn "clock not NTP-synchronized — windows open/close by this clock"

echo
echo "summary: ${FAILS} fail(s), ${WARNS} warning(s)"
[ "$FAILS" -eq 0 ]
