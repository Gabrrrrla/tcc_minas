#!/usr/bin/env bash
# MINAS — smoke test of the lab stack, AFTER it is up and BEFORE any experiment.
# Complements lab_preflight.sh (which checks the host before the stack starts).
#
#   bash scripts/lab_smoke.sh                 # from the repo root, on the lab host
#   SHAPER_WRITE_TEST=1 bash scripts/lab_smoke.sh   # also set/clear a 1 Mbps limit on SST 2
#
# Read-only except for the optional shaper write test. Prints PASS / WARN / FAIL.

set -u
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; WARNS=$((WARNS + 1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILS=$((FAILS + 1)); }
WARNS=0; FAILS=0
[ -f .env ] && set -a && . ./.env && set +a
PROM=${PROM:-http://localhost:9090}
GNB_SLICER_URL=${GNB_SLICER_URL:-http://10.11.0.1:8010}
MINAS_MODEL=${MINAS_MODEL:-qwen2.5:7b}

promq() {  # first value of an instant query, or empty
  curl -s -m 5 "${PROM}/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import json,sys
try:
    r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "")
except Exception: print("")'
}
psql_q() { docker exec postgres psql -U minas -d minas -Atc "$1" 2>/dev/null; }

echo "== containers"
for c in postgres nwdaf cn-nssmf ran-nssmf orchestrator collector upf upf-netdev upf-shaper prometheus amf smf iperf iperf-b; do
  st=$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$c" 2>/dev/null)
  case "$st" in
    "running 0") pass "$c running" ;;
    running*)    warn "$c running but restarted ${st#running } time(s) — docker logs $c" ;;
    "")          fail "$c does not exist" ;;
    *)           fail "$c is $st — docker logs $c" ;;
  esac
done

echo "== agents"
# CN-NSSMF by its bridge IP: the lab overlay does not publish :8001 (in use on the server)
for u in localhost:8000 10.11.0.60:8001 localhost:8002 localhost:8080; do
  curl -sf -m 5 "http://$u/health" >/dev/null && pass "health $u" || fail "no health on $u"
done
docker exec cn-nssmf sh -c 'echo $ENFORCEMENT_MODE $UPF_SHAPING' | grep -q '^real' \
  && pass "cn-nssmf ENFORCEMENT_MODE=real (UPF_SHAPING=$(docker exec cn-nssmf sh -c 'echo $UPF_SHAPING'))" \
  || warn "cn-nssmf is NOT in ENFORCEMENT_MODE=real — decisions will not reach the UPF"
docker exec ran-nssmf sh -c 'echo $ENFORCEMENT_MODE' | grep -q '^real' \
  && pass "ran-nssmf ENFORCEMENT_MODE=real" || warn "ran-nssmf is NOT in ENFORCEMENT_MODE=real"
docker exec ran-nssmf python -c "import os,urllib.request,json; print(json.load(urllib.request.urlopen(os.environ.get('OLLAMA_URL','http://host.docker.internal:11434')+'/api/tags',timeout=5))['models'][0]['name'])" >/dev/null 2>&1 \
  && pass "Ollama reachable from the containers" || fail "Ollama NOT reachable from the containers (OLLAMA_URL=http://10.11.0.1:11434? docker logs ollama-proxy; firewall accepting 10.11.0.0/24?)"

echo "== enforcement points"
if out=$(docker exec cn-nssmf python -c "import requests;r=requests.get('http://upf:9300/slices',timeout=5).json();print({k:(v['dev'],v['rate'] or 'no limit') for k,v in r.items()}, 'NODEV' if any('Cannot find device' in v['raw'] for v in r.values()) else '')" 2>&1); then
  pass "upf-shaper answers from cn-nssmf: ${out% NODEV}"
  case "$out" in *NODEV*) fail "upf-shaper cannot see a slice's TUN device (SLICE_TUN vs the UPF's TUN names)";; esac
else fail "upf-shaper not reachable at upf:9300 from cn-nssmf: $out"; fi
if [ "${SHAPER_WRITE_TEST:-0}" = "1" ]; then
  docker exec cn-nssmf python -c "
import requests
r=requests.put('http://upf:9300/slices/2',json={'mbr_dl_mbps':1},timeout=5).json(); assert r.get('rate'), r
r=requests.put('http://upf:9300/slices/2',json={'mbr_dl_mbps':None},timeout=5).json(); assert not r.get('limited'), r
print('ok')" >/dev/null 2>&1 && pass "shaper write test (set and cleared 1 Mbps on SST 2)" || fail "shaper write test failed"
fi
if out=$(docker exec ran-nssmf python -c "import os,requests;print(requests.get(os.environ['GNB_SLICER_URL']+'/health',timeout=5).json())" 2>&1); then
  pass "gnb-slicer answers from ran-nssmf: $out"
else fail "gnb-slicer not reachable from ran-nssmf at ${GNB_SLICER_URL} (started? firewall? GNB_SLICER_URL)"; fi

echo "== telemetry (Prometheus at ${PROM})"
for dev in ogstun ogstun2; do
  v=$(promq "8*rate(node_network_transmit_bytes_total{device=\"$dev\"}[30s])/1e6")
  [ -n "$v" ] && pass "DL throughput series for $dev: ${v} Mbps" || fail "no series for $dev (upf-netdev up? scrape target 'upf-netdev' UP?)"
done
for q in "sum(ran_ue)" "sum(fivegs_smffunction_sm_sessionnbr)"; do
  v=$(promq "$q"); [ -n "$v" ] && pass "$q = $v" || warn "$q has no data — check the metric name: curl amf:9090/metrics / smf:9090/metrics (collector QUERY_UES / QUERY_SESSIONS)"
done
down=$(curl -s -m 5 "${PROM}/api/v1/targets" | python3 -c 'import json,sys
print(" ".join(t["labels"]["job"] for t in json.load(sys.stdin)["data"]["activeTargets"] if t["health"]!="up"))' 2>/dev/null)
[ -z "$down" ] && pass "all Prometheus targets up" || warn "Prometheus targets down: $down"

echo "== database"
n=$(psql_q "select count(*) from core_kpis where collected_at > now() - interval '2 minutes' and thp_dl_mbps is not null")
[ "${n:-0}" -gt 0 ] && pass "collector wrote $n core_kpis rows with throughput in the last 2 min" \
  || fail "no recent core_kpis rows with throughput — docker logs collector"
h=$(psql_q "select count(*) from core_kpis where sst=2 and thp_dl_mbps is not null and collected_at > now() - interval '1 hour'")
[ "${h:-0}" -ge 30 ] && pass "NWDAF has $h samples of SST 2 in the last hour" \
  || warn "only ${h:-0} recent SST 2 samples — the NWDAF falls back to a mock below ~11; let the collector run ~5 min before UC1"
a=$(psql_q "select count(*) from intents where status in ('applied','degraded','scheduled')")
[ "${a:-0}" -eq 0 ] && pass "no intent in force or scheduled" || warn "$a intent(s) applied/degraded/scheduled from earlier runs — they keep their limits in force"

echo
echo "summary: ${FAILS} fail(s), ${WARNS} warning(s)"
[ "$FAILS" -eq 0 ]
