#!/usr/bin/env bash
# MINAS — setup of the lab server (innovaserver), done once before the experiments.
# It can be run again as many times as needed: each step only does what is missing.
#
#   git clone https://github.com/Gabrrrrla/tcc_minas && cd tcc_minas
#   bash scripts/lab_install.sh
#
# It does NOT start MINAS and does NOT stop the lab gNB or Open5GS: it only installs
# packages, prepares the .env, downloads the model and the images, and builds the
# MINAS images, so that on the day it is enough to start. At the end, it lists what is missing.
set -u
cd "$(dirname "$0")/.."

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL + 1)); }
WARN=0; FAIL=0
MODEL=${MINAS_MODEL:-qwen2.5:7b}

echo "== 1. sudo"
if sudo -v; then ok "sudo"; else fail "no sudo: the remaining steps need it"; exit 1; fi

echo "== 2. system packages (the slicer and the lab scripts run outside Docker)"
PKGS="git tmux curl jq python3 python3-flask python3-yaml python3-requests python3-psycopg2 python3-matplotlib python3-numpy"
missing=$(for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done | xargs)
if [ -z "$missing" ]; then ok "all installed"
elif sudo apt-get update -qq && sudo apt-get install -y -qq $missing >/dev/null; then ok "installed: $missing"
else fail "apt-get install $missing"; fi
sudo python3 -c 'import flask, yaml' 2>/dev/null && ok "root python3 has flask + yaml (gnb_slicer.py)" || fail "root python3 lacks flask/yaml"

echo "== 3. Docker"
DOCKER=docker
if ! command -v docker >/dev/null; then fail "Docker not installed (https://docs.docker.com/engine/install/)"; exit 1; fi
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
  warn "your user is not in the docker group; using sudo docker (to fix: sudo usermod -aG docker \$USER and log in again)"
fi
$DOCKER compose version >/dev/null 2>&1 && ok "$($DOCKER compose version | head -1)" || { fail "the docker compose v2 plugin is missing"; exit 1; }

echo "== 4. SCTP (N2 between the gNB and the MINAS AMF)"
if sudo modprobe sctp; then ok "sctp module loaded"; else fail "modprobe sctp"; fi
if [ -f /etc/modules-load.d/sctp.conf ]; then ok "sctp loads at boot"
else echo sctp | sudo tee /etc/modules-load.d/sctp.conf >/dev/null && ok "sctp added to boot (/etc/modules-load.d/sctp.conf)"; fi

echo "== 5. MINAS .env"
[ -f .env ] || { cp env.example .env && ok ".env created from env.example"; }
setenv() {  # setenv KEY VALUE: replaces the KEY=... line or appends it
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
setenv MINAS_MODEL "$MODEL"
setenv OLLAMA_URL "http://10.11.0.1:11434"   # ollama-proxy relay (docker-compose.lab.yml); Ollama stays on 127.0.0.1
setenv MINAS_TZ "America/Sao_Paulo"
setenv CORE_SOURCE prometheus
setenv GNB_SLICER_URL "http://10.11.0.1:8010"
setenv UPF_SHAPING true
setenv ENFORCEMENT_TIMEOUT_S 150
setenv RAN_PRB_TOTAL 273
grep -q "^RAN_MBPS_PER_PRB=" .env || setenv RAN_MBPS_PER_PRB 0.40   # adjusted on the day, after measuring the cell (E0)
ok ".env with the lab values (273 PRB, slicer at 10.11.0.1:8010, timeout 150 s)"

echo "== 6. Ollama and model $MODEL"
if ! curl -sf -m 5 localhost:11434/api/tags >/dev/null; then
  if command -v ollama >/dev/null; then
    warn "Ollama installed but stopped: trying sudo systemctl start ollama"
    sudo systemctl start ollama 2>/dev/null
  else
    # Official installer (https://ollama.com/download/linux): creates the systemd
    # service and uses the NVIDIA GPU if the driver is installed.
    echo "  Ollama not found: installing (official installer)"
    if curl -fsSL https://ollama.com/install.sh | sh; then
      # stays on 127.0.0.1 (the default): 0.0.0.0 would expose Ollama on the public IP.
      # The containers reach it through the ollama-proxy relay (10.11.0.1:11434).
      sudo systemctl enable --now ollama >/dev/null 2>&1
      ok "Ollama installed as a service (127.0.0.1:11434)"
    else fail "Ollama installation (curl -fsSL https://ollama.com/install.sh | sh)"; fi
  fi
  for _ in $(seq 1 20); do curl -sf -m 2 localhost:11434/api/tags >/dev/null && break; sleep 1; done
fi
if curl -sf -m 5 localhost:11434/api/tags -o /tmp/minas_tags.json; then
  ok "Ollama answers on localhost:11434"
  if grep -q "\"$MODEL\"" /tmp/minas_tags.json; then ok "$MODEL already downloaded"
  else
    echo "  downloading $MODEL (~4.7 GB)..."
    if command -v ollama >/dev/null; then
      ollama pull "$MODEL" 2>&1 | tail -3 | sed 's/^/    /'
    else  # Ollama in a container, without the command on the host: through the API, showing the error
      curl -s -m 3600 localhost:11434/api/pull -d "{\"model\":\"$MODEL\",\"stream\":false}" | sed 's/^/    /'; echo
    fi
    if curl -sf -m 5 localhost:11434/api/tags | grep -q "\"$MODEL\""; then ok "$MODEL downloaded"
    else
      fail "could not download $MODEL (error above). If the network blocks the Ollama registry, copy the model from another machine"
      echo "    test: curl -sI https://registry.ollama.ai/v2/library/qwen2.5/manifests/7b | head -1"
    fi
  fi
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then ok "GPU: $(nvidia-smi -L | head -1)"
  else warn "no nvidia-smi: Ollama will run on the CPU, competing with the gNB"; fi
  if ss -ltn 2>/dev/null | grep -qE '(0\.0\.0\.0|\*|\[::\]):11434'; then
    warn "Ollama listens on every interface (exposed on the public IP, and the ollama-proxy relay cannot use 10.11.0.1:11434). Switch back to 127.0.0.1"
  else ok "Ollama only on 127.0.0.1; the containers use the ollama-proxy relay (10.11.0.1:11434), tested by lab_smoke.sh"; fi
else
  fail "Ollama does not answer on localhost:11434 (is it on this machine? is it running?)"
fi

echo "== 7. Docker images (slow the first time; agents/Dockerfile uses HF_HUB_DISABLE_XET=1 because the university network blocks Xet)"
CF="-f docker-compose.yml -f docker-compose.lab.yml"
$DOCKER compose $CF pull --ignore-pull-failures -q >/dev/null 2>&1; ok "prebuilt images downloaded (Open5GS, Mongo, Postgres, Prometheus, iperf3...)"
if $DOCKER compose build -q && $DOCKER compose $CF build -q upf-shaper; then ok "MINAS images built (minas-agent, minas-enforcer)"
else fail "build of the MINAS images (run: $DOCKER compose build)"; fi
$DOCKER pull -q networkstatic/iperf3 >/dev/null && ok "networkstatic/iperf3 (scripts/ue_traffic.py)"

echo "== 8. SIM cards (scripts/sims.lab.js, outside git)"
if [ -f scripts/sims.lab.js ] && ! grep -q "XXXXXXXXXX" scripts/sims.lab.js; then ok "scripts/sims.lab.js filled in"
else
  [ -f scripts/sims.lab.js ] || cp scripts/sims.lab.example.js scripts/sims.lab.js
  warn "fill in the IMSI/K/OPc of the 2 lab SIM cards in scripts/sims.lab.js (nano scripts/sims.lab.js)"
fi

echo "== 9. gNB template for MINAS"
CFG=/home/innovaserver/ocudu/configs
if ls "$CFG"/*.yml >/dev/null 2>&1; then
  hits=$(grep -l "10.11.0.20" "$CFG"/*.yml 2>/dev/null | xargs)
  [ -n "$hits" ] && ok "yml pointing to the MINAS AMF (10.11.0.20): $hits" \
                 || warn "no yml in $CFG points to 10.11.0.20 (MINAS AMF); the reference template is srsran/gnb.lab.yaml"
else warn "$CFG not found: adjust the gNB path on the day"; fi

echo
echo "== Summary: $FAIL failure(s), $WARN warning(s)"
echo "Nothing from MINAS was started. To check the host before the day: bash scripts/lab_preflight.sh"
echo "(in the preflight, a FAIL because native Open5GS is running is expected while the lab core is up)"
[ "$FAIL" -eq 0 ]
