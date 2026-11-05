#!/usr/bin/env bash
# MINAS — instalação no servidor do lab (innovaserver), feita uma vez antes do dia.
# Pode rodar de novo quantas vezes quiser: cada etapa só faz o que ainda falta.
#
#   git clone https://github.com/Gabrrrrla/tcc_minas && cd tcc_minas
#   bash scripts/lab_install.sh
#
# NÃO sobe o MINAS, NÃO para o gNB nem o Open5GS do lab: só instala pacotes,
# prepara o .env, baixa o modelo e as imagens e constrói as imagens do MINAS,
# para que no dia baste subir. No fim, diz o que ainda falta.
set -u
cd "$(dirname "$0")/.."

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mAVISO\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf '  \033[31mFALHA\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
WARN=0; FAIL=0
MODEL=${MINAS_MODEL:-qwen2.5:7b}

echo "== 1. sudo"
if sudo -v; then ok "sudo"; else fail "sem sudo: o resto precisa dele"; exit 1; fi

echo "== 2. pacotes do sistema (slicer e scripts do lab rodam fora do Docker)"
PKGS="git tmux curl jq python3 python3-flask python3-yaml python3-requests python3-psycopg2 python3-matplotlib python3-numpy"
missing=$(for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done | xargs)
if [ -z "$missing" ]; then ok "todos instalados"
elif sudo apt-get update -qq && sudo apt-get install -y -qq $missing >/dev/null; then ok "instalados: $missing"
else fail "apt-get install $missing"; fi
sudo python3 -c 'import flask, yaml' 2>/dev/null && ok "python3 do root tem flask + yaml (gnb_slicer.py)" || fail "python3 do root sem flask/yaml"

echo "== 3. Docker"
DOCKER=docker
if ! command -v docker >/dev/null; then fail "Docker não instalado (https://docs.docker.com/engine/install/)"; exit 1; fi
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
  warn "seu usuário não está no grupo docker; usando sudo docker (para corrigir: sudo usermod -aG docker \$USER e entrar de novo)"
fi
$DOCKER compose version >/dev/null 2>&1 && ok "$($DOCKER compose version | head -1)" || { fail "falta o plugin docker compose v2"; exit 1; }

echo "== 4. SCTP (N2 entre o gNB e o AMF do MINAS)"
if sudo modprobe sctp; then ok "módulo sctp carregado"; else fail "modprobe sctp"; fi
if [ -f /etc/modules-load.d/sctp.conf ]; then ok "sctp carrega no boot"
else echo sctp | sudo tee /etc/modules-load.d/sctp.conf >/dev/null && ok "sctp adicionado ao boot (/etc/modules-load.d/sctp.conf)"; fi

echo "== 5. .env do MINAS"
[ -f .env ] || { cp env.example .env && ok ".env criado a partir do env.example"; }
setenv() {  # setenv KEY VALUE: troca a linha KEY=... ou acrescenta
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
setenv MINAS_MODEL "$MODEL"
setenv OLLAMA_URL "http://host.docker.internal:11434"
setenv MINAS_TZ "America/Sao_Paulo"
setenv CORE_SOURCE prometheus
setenv GNB_SLICER_URL "http://10.11.0.1:8010"
setenv UPF_SHAPING true
setenv ENFORCEMENT_TIMEOUT_S 150
setenv RAN_PRB_TOTAL 273
grep -q "^RAN_MBPS_PER_PRB=" .env || setenv RAN_MBPS_PER_PRB 0.40   # ajustado no dia, depois do E0
ok ".env com os valores do lab (273 PRB, slicer em 10.11.0.1:8010, timeout 150 s)"

echo "== 6. Ollama e modelo $MODEL"
if ! curl -sf -m 5 localhost:11434/api/tags >/dev/null; then
  if command -v ollama >/dev/null; then
    warn "Ollama instalado mas parado: tentando sudo systemctl start ollama"
    sudo systemctl start ollama 2>/dev/null
  else
    # Instalador oficial (https://ollama.com/download/linux): cria o serviço
    # systemd e usa a GPU NVIDIA se o driver estiver instalado.
    echo "  Ollama não encontrado: instalando (instalador oficial)"
    if curl -fsSL https://ollama.com/install.sh | sh; then
      # instalado por nós: já deixa ouvindo em todas as interfaces (os agentes rodam em container)
      sudo mkdir -p /etc/systemd/system/ollama.service.d
      printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' | sudo tee /etc/systemd/system/ollama.service.d/minas.conf >/dev/null
      sudo systemctl daemon-reload && sudo systemctl enable --now ollama >/dev/null 2>&1
      sudo systemctl restart ollama
      ok "Ollama instalado como serviço, ouvindo em 0.0.0.0:11434"
    else fail "instalação do Ollama (curl -fsSL https://ollama.com/install.sh | sh)"; fi
  fi
  for _ in $(seq 1 20); do curl -sf -m 2 localhost:11434/api/tags >/dev/null && break; sleep 1; done
fi
if curl -sf -m 5 localhost:11434/api/tags -o /tmp/minas_tags.json; then
  ok "Ollama responde em localhost:11434"
  if grep -q "\"$MODEL\"" /tmp/minas_tags.json; then ok "$MODEL já baixado"
  else
    # pela API, para funcionar também com o Ollama em container (sem o comando ollama no host)
    echo "  baixando $MODEL (~4,7 GB)..."
    if curl -sf -m 3600 localhost:11434/api/pull -d "{\"model\":\"$MODEL\",\"stream\":false}" | grep -q '"success"'; then ok "$MODEL baixado"
    else fail "não consegui baixar $MODEL (tente: ollama pull $MODEL)"; fi
  fi
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then ok "GPU: $(nvidia-smi -L | head -1)"
  else warn "sem nvidia-smi: o Ollama vai rodar em CPU, competindo com o gNB"; fi
  if ss -ltn 2>/dev/null | grep -q '127.0.0.1:11434'; then
    fail "Ollama ouve só em 127.0.0.1: os agentes (em container) não alcançam. sudo systemctl edit ollama -> [Service] Environment=\"OLLAMA_HOST=0.0.0.0\" ; sudo systemctl restart ollama"
  fi
  if $DOCKER run --rm --add-host host.docker.internal:host-gateway curlimages/curl:8.10.1 -sf -m 5 http://host.docker.internal:11434/api/tags >/dev/null 2>&1; then
    ok "um container alcança o Ollama (host.docker.internal:11434)"
  else fail "um container NÃO alcança o Ollama em host.docker.internal:11434 (OLLAMA_HOST=0.0.0.0? firewall?)"; fi
else
  fail "Ollama não responde em localhost:11434 (está nesta máquina? está rodando?)"
fi

echo "== 7. Imagens Docker (demora na primeira vez)"
CF="-f docker-compose.yml -f docker-compose.lab.yml"
$DOCKER compose $CF pull --ignore-pull-failures -q >/dev/null 2>&1; ok "imagens prontas baixadas (Open5GS, Mongo, Postgres, Prometheus, iperf3...)"
if $DOCKER compose build -q && $DOCKER compose $CF build -q upf-shaper; then ok "imagens do MINAS construídas (minas-agent, minas-enforcer)"
else fail "build das imagens do MINAS (rode: $DOCKER compose build)"; fi
$DOCKER pull -q networkstatic/iperf3 >/dev/null && ok "networkstatic/iperf3 (scripts/ue_traffic.py)"

echo "== 8. Chips (scripts/sims.lab.js, fora do git)"
if [ -f scripts/sims.lab.js ] && ! grep -q "XXXXXXXXXX" scripts/sims.lab.js; then ok "scripts/sims.lab.js preenchido"
else
  [ -f scripts/sims.lab.js ] || cp scripts/sims.lab.example.js scripts/sims.lab.js
  warn "preencha IMSI/K/OPc dos 2 chips azuis em scripts/sims.lab.js (nano scripts/sims.lab.js)"
fi

echo "== 9. Template do gNB para o MINAS"
CFG=/home/innovaserver/ocudu/configs
if ls "$CFG"/*.yml >/dev/null 2>&1; then
  hits=$(grep -l "10.11.0.20" "$CFG"/*.yml 2>/dev/null | xargs)
  [ -n "$hits" ] && ok "yml apontando para o AMF do MINAS (10.11.0.20): $hits" \
                 || warn "nenhum yml em $CFG aponta para 10.11.0.20 (AMF do MINAS); o template de referência é srsran/gnb.lab.yaml (Gabriela)"
else warn "$CFG não encontrado: ajuste o caminho do gNB no dia"; fi

echo
echo "== Resumo: $FAIL falha(s), $WARN aviso(s)"
echo "Nada do MINAS foi iniciado. Para conferir o host antes do dia: bash scripts/lab_preflight.sh"
echo "(no preflight, FALHA por Open5GS nativo rodando é esperada enquanto o core do lab estiver no ar)"
[ "$FAIL" -eq 0 ]
