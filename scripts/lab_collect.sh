#!/usr/bin/env bash
# MINAS — saves everything about one lab run into lab-data/<timestamp>_<label>/.
# Run it right AFTER each experiment (or at the end of the day with a wider --since).
#
#   bash scripts/lab_collect.sh <label> [minutes_back]      # default: last 30 min
#   bash scripts/lab_collect.sh e1-rep1
#   bash scripts/lab_collect.sh e2-rep3 20
#
# Saves (each file is independent; a missing piece is reported, never fatal):
#   meta.txt              time range, git commit, image ids, env of the agents
#   db/*.csv              rows of intents, policies, ran_allocations, events,
#                         sla_samples, core_kpis, ran_kpis, slice_load in the range
#   logs/*.log            docker logs of every MINAS / core container in the range
#   prometheus/*.json     DL/UL throughput per slice TUN (query_range, 2 s step)
#   gnb/                  rendered gNB config, its template and the gNB stdout
#   probe/                the newest enforcement-probe JSONL(s) in the range
#   ue/                   EMPTY: copy the UEs' iperf logs here yourself
#   notes.md              template for what you saw (fill it in!)

set -u
LABEL=${1:?usage: lab_collect.sh <label> [minutes_back]}
MIN=${2:-30}
[ -f .env ] && set -a && . ./.env && set +a
PROM=${PROM:-http://localhost:9090}
GNB_CONFIG_OUT=${GNB_CONFIG_OUT:-/tmp/gnb.minas.yaml}
GNB_LOG=${GNB_LOG:-/tmp/gnb.stdout}
SLICE_TUN=${SLICE_TUN:-1:ogstun,2:ogstun2}

END=$(date -u +%s); START=$((END - MIN * 60))
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
OUT="lab-data/$(date +%Y%m%d-%H%M)_${LABEL}"
mkdir -p "$OUT"/{db,logs,prometheus,gnb,probe,ue}
ok()   { printf '  saved   %s\n' "$*"; }
miss() { printf '  MISSING %s\n' "$*"; }

echo "== $OUT  ($(iso $START) .. $(iso $END))"

{
  echo "label: $LABEL"
  echo "range_utc: $(iso $START) .. $(iso $END)"
  echo "host_time_at_collect: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "git: $(git rev-parse --short HEAD 2>/dev/null) $(git status --porcelain 2>/dev/null | wc -l) uncommitted file(s)"
  for c in orchestrator cn-nssmf ran-nssmf nwdaf collector upf upf-shaper; do
    echo "image[$c]: $(docker inspect -f '{{.Config.Image}} {{.Image}}' "$c" 2>/dev/null)"
  done
  for c in cn-nssmf ran-nssmf orchestrator collector; do
    echo "env[$c]: $(docker exec "$c" sh -c 'env | grep -E "^(MINAS_MODEL|ENFORCEMENT_MODE|UPF_SHAPING|GNB_SLICER_URL|ENFORCEMENT_TIMEOUT_S|RAG_ENABLED|GUARDRAILS_ENABLED|UC1_ENABLED|SLA_MONITOR_ENABLED|CORE_SOURCE|RAN_SOURCE|OLLAMA_URL|RAN_PRB_TOTAL|RAN_MBPS_PER_PRB)=" | sort | tr "\n" " "' 2>/dev/null)"
  done
} > "$OUT/meta.txt" && ok meta.txt
[ -f .env ] && cp .env "$OUT/env.txt" && ok env.txt

echo "-- database"
dump() {  # table, time column
  if docker exec postgres psql -U minas -d minas -c \
      "\\copy (SELECT * FROM $1 WHERE $2 >= to_timestamp($START) ORDER BY $2) TO STDOUT WITH CSV HEADER" \
      > "$OUT/db/$1.csv" 2>/dev/null; then
    ok "db/$1.csv ($(($(wc -l < "$OUT/db/$1.csv") - 1)) rows)"
  else miss "db/$1.csv"; fi
}
dump intents received_at
dump policies applied_at
dump ran_allocations applied_at
dump events created_at
dump sla_samples sampled_at
dump core_kpis collected_at
dump ran_kpis collected_at
dump slice_load computed_at

echo "-- container logs"
for c in orchestrator cn-nssmf ran-nssmf nwdaf collector upf-shaper upf amf smf; do
  if docker logs --since "$(iso $START)" "$c" > "$OUT/logs/$c.log" 2>&1; then ok "logs/$c.log"
  else miss "logs/$c.log"; rm -f "$OUT/logs/$c.log"; fi
done

echo "-- prometheus"
for pair in ${SLICE_TUN//,/ }; do
  sst=${pair%%:*}; dev=${pair#*:}
  for dir in transmit receive; do
    f="$OUT/prometheus/sst${sst}_${dev}_${dir}.json"
    if curl -sf -m 20 "${PROM}/api/v1/query_range" \
         --data-urlencode "query=8*rate(node_network_${dir}_bytes_total{device=\"$dev\"}[20s])/1e6" \
         --data-urlencode "start=$START" --data-urlencode "end=$END" --data-urlencode "step=2" > "$f"; then
      ok "prometheus/$(basename "$f") (Mbps; transmit = downlink)"
    else miss "prometheus/$(basename "$f")"; rm -f "$f"; fi
  done
done

echo "-- gNB (only if this runs on the gNB host)"
for f in "$GNB_CONFIG_OUT" "${GNB_TEMPLATE:-srsran/gnb.lab.yaml}" "$GNB_LOG"; do
  [ -n "$f" ] && [ -f "$f" ] && cp "$f" "$OUT/gnb/" && ok "gnb/$(basename "$f")"
done
# the gNB's own log (all_level: info at 100 MHz) grows fast: keep only its tail
if [ -f /tmp/gnb.log ]; then
  tail -c 50M /tmp/gnb.log > "$OUT/gnb/gnb.log.tail" && ok "gnb/gnb.log.tail (last 50 MB of /tmp/gnb.log)"
fi

echo "-- probe results"
n=0
for f in agents/benchmark/results/enforcement-probe_*.jsonl agents/benchmark/results/window-probes_*.jsonl; do
  [ -f "$f" ] || continue
  [ "$(stat -c %Y "$f")" -ge "$START" ] && cp "$f" "$OUT/probe/" && ok "probe/$(basename "$f")" && n=$((n + 1))
done
[ "$n" -eq 0 ] && miss "no probe JSONL written in the range"

cat > "$OUT/notes.md" <<EOF
# ${LABEL} — $(date '+%Y-%m-%d %H:%M')

- Experimento (E0/E1/E2/E3/E4):
- UEs usadas (qual SIM/IMSI, qual fatia, comando iperf):
- Alvo da intenção / janela:
- UPF_SHAPING:
- Vazão antes / durante / depois (o que viu no terminal):
- Restart do gNB: quanto tempo? as UEs voltaram sozinhas? quanto tempo sem tráfego?
- Algo estranho (erros, UE caiu, gNB não subiu, etc.):
- Arquivos das UEs copiados para ue/ ? (s/n)
EOF
ok "notes.md (preencha agora, enquanto lembra)"

echo
echo "next: copy the UEs' iperf logs into $OUT/ue/ and fill in $OUT/notes.md"
