# MINAS - Multi-Agent Intent-Driven Network Analytics and Slicing

TCC II - Ciência da Computação, UNISINOS  
Orientador: Prof. Dr. Cristiano Bonato Both

Sistema de orquestração autônoma de fatias de rede 5G baseado em Multi-Agent System (MAS) e Large Language Models (LLMs). O operador expressa objetivos em linguagem natural e o sistema interpreta, negocia recursos entre domínios e aplica as configurações nas funções de núcleo e acesso rádio.

---

## Arquitetura

```
Operador (linguagem natural)
        │
        ▼
┌─────────────────────┐
│    Orquestrador     │  ← LLM + ReAct + tool-calling (cliente MCP)
│  (agente central)   │
└──────┬──────────────┘
       │ diretivas via MCP (streamable HTTP)
  ┌────┴────┐
  ▼         ▼
CN-NSSMF  RAN-NSSMF   ← servidores MCP (cada ação = ferramenta MCP)
(núcleo)   (rádio)
  │             │
  ▼             ▼
Open5GS      srsRAN
(Docker)   (servidor lab)
              │
              ▼
         Liteon Flexi
           (RU física)
```

A coordenação orquestrador e agentes de domínio é feita por **MCP** (Model
Context Protocol): cada agente de domínio roda um servidor MCP que expõe suas
ações de diretiva como ferramentas; o orquestrador é cliente MCP e descobre
essas ferramentas dinamicamente. A NWDAF usa HTTP por design, modelando a
interface normativa 3GPP `Nnwdaf_AnalyticsInfo` (TS 23.288 / TS 29.520).

**Três camadas:**
- **Intenção** - entrada do operador em linguagem natural
- **Orquestração agêntica** - Orquestrador + CN-NSSMF + RAN-NSSMF (agentes ReAct, sem fine-tuning)
- **Infraestrutura** - Open5GS 5G Core + srsRAN + Liteon Flexi DU/RU

**Dois slices:**
- `SST=1` (eMBB) - streaming / UC2
- `SST=2` (URLLC) - missão crítica / UC1

---

## Casos de uso

### UC1 - Predição e ajuste de recursos (SST=2)
CN-NSSMF consulta NWDAF periodicamente. A NWDAF aplica Random Forest sobre séries históricas de telemetria para prever utilização de recursos com horizonte de 60 segundos. Se a previsão ultrapassar o limiar, o orquestrador amplia os recursos da slice. Se insuficientes, aplica degradação graciosa e registra violação de SLA.

### UC2 - Política de QoS agendada (SST=1)
Orquestrador interpreta intenção com janela temporal, decompõe em duas diretivas paralelas (CN-NSSMF reconfigura QoS no PCF/SMF; RAN-NSSMF ajusta PRBs). Ao fim da janela, o orquestrador envia diretivas de reversão e restaura as configurações anteriores.

---

## Estrutura do repositório

```
tcc_minas/
├── docker-compose.yml          # Stack completa (Open5GS + monitoramento + banco)
├── docker-compose.lab.yml      # Overlay do lab: GTP-U pela bridge, NAT das UEs, iperf, collector real
├── requirements.txt            # Dependências Python dos agentes
├── env.example                 # Variáveis de ambiente (copiar para .env)
│
├── open5gs/                    # Configurações do 5G Core
│   ├── amf.yaml                # SST=1 + SST=2, TAC=1, PLMN 00101
│   ├── smf.yaml                # DNN internet (10.45.0.0/16) + slice2 (10.46.0.0/16)
│   ├── upf.yaml                # ogstun + ogstun2, advertise 127.0.0.1
│   ├── upf.lab.yaml            # igual, advertise 10.11.0.22 (gNB no host, via bridge)
│   ├── nrf.yaml
│   ├── scp.yaml
│   ├── ausf.yaml
│   ├── udm.yaml
│   ├── udr.yaml
│   ├── pcf.yaml
│   ├── nssf.yaml
│   └── bsf.yaml
│
├── srsran/
│   └── gnb.yaml                # Configuração do gNB (preencher placeholders antes do lab)
│
├── agents/
│   ├── schema.sql              # Schema PostgreSQL (KPIs, intents, policies, ran_allocations, events, sla_samples)
│   ├── migrations/             # ALTERs p/ bancos já criados (schema.sql só roda num volume novo)
│   ├── Dockerfile              # imagem única dos agentes (build context = raiz)
│   ├── db.py                   # conexão PostgreSQL - compartilhada (thread-safe, autocommit)
│   ├── timeutil.py             # fuso do operador (MINAS_TZ): "agora" no prompt, horário sem fuso = local
│   ├── react.py                # cliente Ollama + loop ReAct - compartilhado
│   ├── guardrails.py           # validação determinística de parâmetros (pré-dispatch, todos os agentes)
│   ├── mcp_common.py           # helpers de cliente MCP (list_remote_tools / call_remote_tool)
│   ├── orchestrator/
│   │   ├── main.py             # HTTP (POST /intent, POST /event) + CLI + RAG + system prompt 3GPP; cliente MCP
│   │   ├── tools.py            # 3 ferramentas locais + descoberta MCP + dedup semântica + agendamento
│   │   ├── scheduler.py        # janelas (UC2): ativa no window_start, reverte no window_end; via MCP
│   │   └── sla_monitor.py      # amostra o SLA dos intents em vigor -> sla_samples / eventos de violação
│   ├── cn-nssmf/
│   │   ├── main.py             # FastMCP servidor :8001/mcp; ferramentas apply_qos/revert_qos/query_nwdaf/check_sla
│   │   ├── tools.py            # 5 ferramentas internas (ReAct) + dispatcher
│   │   ├── monitor.py          # UC1 proativo: consulta a NWDAF e avisa o orquestrador (POST /event)
│   │   └── nwdaf_client.py     # cliente da NWDAF (TS 23.288); cai pra mock se ela estiver fora
│   ├── ran-nssmf/
│   │   ├── main.py             # FastMCP servidor :8002/mcp; ferramentas apply_resources/revert_resources/check_sla
│   │   └── tools.py            # 5 ferramentas internas (ReAct) + dispatcher
│   ├── nwdaf/
│   │   ├── main.py             # Flask POST /analytics; Random Forest real p/ SLICE_LOAD_LEVEL
│   │   ├── dataset.py          # janelas de lag compartilhadas (serviço e RQ4)
│   │   └── benchmark_rq4.py    # RF vs GBM vs LSTM (+ baselines) offline sobre core_kpis (RQ4)
│   ├── rag/
│   │   ├── corpus.py           # 35 chunks curados (TS 23.501/23.288/28.312/38.300/28.552 + ReAct/MCP)
│   │   └── retriever.py        # embeddings bge-small-en-v1.5; recover por cosseno
│   ├── benchmark/
│   │   ├── intent_set.py       # 16 intents com gabarito (ground truth para RQ1-RQ3)
│   │   ├── run_benchmark.py    # runner de uma célula do fatorial; output JSONL
│   │   ├── run_llm_benchmark.py # itera sobre modelos, recria containers, agrega resultados
│   │   ├── run_window_probes.py # ciclo de vida das janelas ponta a ponta (agenda -> ativa -> reverte)
│   │   └── sla_report.py       # taxa de cumprimento de SLA a partir de sla_samples
│   └── collector/              # amostrador core_kpis + ran_kpis (fonte: prometheus | synthetic | o1 | mock)
│       ├── main.py             # loop de coleta -> INSERT core_kpis / ran_kpis
│       ├── synth.py            # tráfego sintético com estrutura temporal (diário + AR(1) + picos)
│       ├── backfill_synthetic.py # gera dias de histórico sintético num banco dedicado
│       └── o1_client.py        # stub da interface O1/NETCONF do gNB (fonte ideal p/ RAN)
│
├── scripts/
│   ├── provision.js            # Cadastra UE1 (SST=1+2) e UE2 (SST=2) no MongoDB
│   ├── inspect.js              # Consulta subscriber no MongoDB
│   └── lab_preflight.sh        # checagens no servidor do lab (SCTP, portas, sub-redes, Ollama, CPU, relógio)
│
└── monitoring/
    ├── prometheus.yml          # Scrape: AMF, SMF, UPF (:9090) + upf-netdev (:9100, TUN por slice)
    └── grafana/provisioning/
        └── datasources/
            └── prometheus.yml
```

---

## Serviços Docker

| Container | Imagem | IP | Porta exposta |
|---|---|---|---|
| mongodb | mongo:4.4 | 10.11.0.2 | - |
| nrf | gradiant/open5gs:2.6.4 | 10.11.0.10 | - |
| ausf | gradiant/open5gs:2.6.4 | 10.11.0.11 | - |
| udm | gradiant/open5gs:2.6.4 | 10.11.0.12 | - |
| udr | gradiant/open5gs:2.6.4 | 10.11.0.13 | - |
| pcf | gradiant/open5gs:2.6.4 | 10.11.0.14 | - |
| bsf | gradiant/open5gs:2.6.4 | 10.11.0.15 | - |
| nssf | gradiant/open5gs:2.6.4 | 10.11.0.16 | - |
| scp | gradiant/open5gs:2.6.4 | 10.11.0.17 | - |
| amf | gradiant/open5gs:2.6.4 | 10.11.0.20 | **38412/sctp** |
| smf | gradiant/open5gs:2.6.4 | 10.11.0.21 | - |
| upf | gradiant/open5gs:2.6.4 | 10.11.0.22 | **2152/udp** |
| upf-netdev | prom/node-exporter:v1.8.2 | (rede do upf) | - |
| webui | gradiant/open5gs-webui:2.6.4 | 10.11.0.30 | 3000 |
| postgres | postgres:16-alpine | 10.11.0.40 | 5432 |
| orchestrator | build `agents/Dockerfile` | 10.11.0.55 | 8000 |
| prometheus | prom/prometheus:v2.53.0 | 10.11.0.50 | 9090 |
| grafana | grafana/grafana:11.1.0 | 10.11.0.51 | 3001 |
| cn-nssmf | build `agents/Dockerfile` | 10.11.0.60 | 8001 |
| ran-nssmf | build `agents/Dockerfile` | 10.11.0.62 | 8002 |
| nwdaf | build `agents/Dockerfile` | 10.11.0.63 | 8080 |
| collector | build `agents/Dockerfile` | 10.11.0.61 | - |

---

## Como subir

### Pré-requisitos
- Docker e Docker Compose instalados
- (para os agentes) Python 3.11+ e Ollama rodando localmente

### 1. Clonar e configurar

```bash
git clone <url-do-repo>
cd tcc_minas
cp env.example .env
# editar .env se necessário (OLLAMA_URL, MINAS_MODEL)
```

### 2. Subir o core 5G

```bash
docker compose up -d
```

O container `provision` cadastra automaticamente os dois UEs de teste no MongoDB.

### 3. Verificar

```bash
docker compose logs -f amf       # deve mostrar: AMF initialize...OK
docker compose logs provision     # deve mostrar: Subscriber ...001 provisioned
```

### 4. Interfaces de monitoramento

| Interface | URL | Credenciais |
|---|---|---|
| Open5GS WebUI | http://localhost:3000 | admin / 1423 |
| Grafana | http://localhost:3001 | admin / minas |
| Prometheus | http://localhost:9090 | - |

---

## Integração com srsRAN (lab UNISINOS)

O srsRAN roda **fora do Docker** (DPDK), no mesmo servidor físico, e fala com o
core direto pela bridge `minas-net`, sem NAT:

```
gNB (host) ──N2/SCTP──> AMF 10.11.0.20:38412      (bind do gNB: 10.11.0.1, gateway da bridge)
gNB (host) <─N3/GTP-U─> UPF 10.11.0.22:2152       (open5gs/upf.lab.yaml anuncia esse IP)
```

Passo a passo no servidor do lab:

1. `bash scripts/lab_preflight.sh` — só lê o host: módulo SCTP, portas 38412/2152
   livres, Open5GS nativo rodando, sub-redes 10.11/10.45/10.46 em conflito,
   Ollama alcançável e escutando fora do loopback, isolamento de CPU do gNB,
   hugepages, relógio/NTP.
2. Preencher `srsran/gnb.yaml` (ou partir do gnb.yaml do Miguel): `amf.addr`
   10.11.0.20, `bind_addr` 10.11.0.1, parâmetros da RU; PLMN/TAC/S-NSSAI iguais
   a `open5gs/amf.yaml` e aos SIMs.
3. IMSI/K/OPc reais dos SIMs em `scripts/provision.js`.
4. `docker compose -f docker-compose.yml -f docker-compose.lab.yml up -d` —
   o overlay troca a config da UPF, adiciona NAT das UEs, um servidor `iperf3`
   (10.11.0.70) e põe o collector em modo `prometheus`.
5. Subir o gNB → conferir NG Setup no log do AMF → ligar a UE → sessões PDU nas
   duas slices → `iperf3 -c 10.11.0.70 -R` da UE → throughput por slice no
   Prometheus (`node_network_transmit_bytes_total{device="ogstun"}`).
6. Só então os agentes/intents. O LLM de preferência fora do servidor do gNB
   (`OLLAMA_URL`): inferência em CPU compete com as threads de tempo real.

Ainda **não** existe enforcement real no gNB/PCF/SMF (`allocate_prb`/`configure_qos`
só registram) nem fonte de KPIs de rádio do srsRAN (`RAN_SOURCE=srsran`).

---

## Agentes Python

Os agentes usam **Ollama** como servidor de inferência local, sem dependência de APIs externas.

### Instalar e subir o Ollama

```bash
# instalar: https://ollama.com
ollama serve               # sobe o servidor de inferência em localhost:11434
ollama pull qwen2.5:7b     # modelo padrão do MINAS (ver justificativa abaixo)
```

### Escolha do modelo LLM

O modelo é selecionado pela variável `MINAS_MODEL` no `.env`.

**`qwen2.5:7b` - padrão do MINAS (desde 12/09/2026):**

Escolhido depois de um smoke test comparando 3 candidatos: foi o único que completou o loop ReAct de ponta a ponta (`record_intent` -> `cn_nssmf_*`/`ran_nssmf_*` -> `update_intent_status`), usando o formato de tool-calling estruturado do Ollama corretamente. Sem fine-tuning de telecom, a mitigação de alucinação de domínio depende dos `guardrails.py` determinísticos. O smoke test foi informal (poucas execuções), não o benchmark rigoroso prometido no TCC I (P6: comparar candidatos num conjunto de intenções derivado da TS 28.312, medindo acurácia de tool-calling). Esse benchmark formal ainda é trabalho pendente.

**Por que não OTel-LLM-E4B-IT (ou qualquer outro tamanho da família OTel-LLM):**

O projeto [OTel (Open Telco AI)](https://github.com/farbodtavakkoli/OTel), com contribuição da GSMA, disponibiliza a série [OTel-LLM](https://huggingface.co/collections/farbodtavakkoli/otel-llm) (270 M a 32 B parâmetros), fine-tuned em specs 3GPP/O-RAN/ETSI/ITU. Era a escolha natural pro domínio do MINAS: **OTel-LLM-E4B-IT** tem 91,7% de correctness no eval "context-grounded generation" da própria OTel (melhor resultado publicado na categoria).

**Não funcionou (1ª tentativa, sem RAG):** toda a família OTel-LLM é treinada com a mesma receita: pergunta + trecho de contexto recuperado + resposta, mais exemplos de **abstenção** quando o contexto não contém a resposta. Sem RAG, o modelo nunca recebia o bloco de "contexto recuperado" que espera, e o reflexo treinado era abster-se em vez de tentar uma ferramenta: nunca chamou nenhuma ferramenta, respondendo direto "Answer not found in the retrieved context." (`llama3.1:8b` também foi testado e descartado: emite a chamada de ferramenta como texto solto em vez de usar o campo `tool_calls` estruturado do Ollama.)

**RAG (P3) já foi implementado** (`agents/rag/`) e o teste foi repetido com contexto de verdade injetado. Resultado: **ainda não funciona, mas por um motivo diferente**. O modelo parou de abster-se, só que em vez de chamar as ferramentas ele **alucionou uma resposta completa** (números de throughput/SLA inventados, sem nenhum `tool_use` no trace). O RAG resolveu o sintoma (abstenção) mas não a causa raiz: o próprio model card da OTel avisa que o mix de treino não tem exemplos de tool-calling específicos de telecom, com ou sem contexto recuperado. Achado de 12/09/2026, ver `HANDOVER-2026-09-12.md`.

Guia de conversão (mantido pra quando isso for revisitado; os modelos são publicados em `.bin` pytorch; pra usar via Ollama, converter para GGUF com `llama.cpp`; a OTel também lista quantizações prontas em `inference/ollama` no repo, **confira lá primeiro**, pode poupar todo o processo abaixo):

```bash
# 0. Espaço em disco: reserve ~70GB temporários (31,5GB download + ~16GB
#    intermediário f16 + ~5GB final - dá pra apagar os dois primeiros depois)

# 1. Baixar o modelo do HuggingFace
huggingface-cli download farbodtavakkoli/OTel-LLM-E4B-IT --local-dir otel-e4b

# 2. Converter HF -> GGUF f16 (convert_hf_to_gguf.py só aceita
#    f32/f16/bf16/q8_0/tq1_0/tq2_0/auto - NÃO aceita q4_k_m direto)
git clone https://github.com/ggml-org/llama.cpp
pip install -r llama.cpp/requirements.txt
python llama.cpp/convert_hf_to_gguf.py otel-e4b --outfile otel-e4b-f16.gguf --outtype f16

# 3. Quantizar f16 -> Q4_K_M com o binário llama-quantize (compilado - baixe um
#    release pronto em https://github.com/ggml-org/llama.cpp/releases em vez
#    de compilar do zero)
./llama-quantize otel-e4b-f16.gguf otel-e4b-q4_k_m.gguf Q4_K_M

# 4. Registrar no Ollama
ollama create otel-llm-e4b-it -f - <<'EOF'
FROM ./otel-e4b-q4_k_m.gguf
EOF

# 5. Apontar o MINAS para o novo modelo
echo "MINAS_MODEL=otel-llm-e4b-it" >> .env
```

Outras variantes da série, por tamanho *efetivo* nomeado pela OTel (o tamanho real em disco pode ser maior, como no E4B acima; confira o repositório de cada uma antes de baixar):

| Modelo | Parâmetros (nome) | Observação |
|---|---|---|
| OTel-LLM-1B-IT | 1 B | menor da linha, CPU ok |
| OTel-LLM-3B-IT | 3 B | leve |
| OTel-LLM-E4B-IT | "E4B" (efetivo) | ~8B params reais, ~31,5GB de download, ~5GB depois de quantizado; não funciona sem RAG (ver acima) |
| OTel-LLM-7B-IT | 7 B | |
| OTel-LLM-8.3B-IT | 8.3 B | |
| OTel-LLM-14B-IT | 14 B | maior, exige mais RAM/VRAM |

**Genérico (fallback, sem fine-tuning de domínio):**
```bash
ollama pull llama3.1:70b
MINAS_MODEL=llama3.1:70b
```



### Orquestrador (`agents/orchestrator/`)

Recebe intenção em linguagem natural e conduz o loop ReAct até resolver. Roda em
dois modos:

```bash
cd agents/orchestrator
pip install -r ../../requirements.txt

# modo CLI (uma intenção, imprime o resultado)
python main.py "Aumentar a taxa de dados garantida para a fatia de streaming de 10 Mbps para 20 Mbps entre 18h e 22h."

# modo serviço (sem argumentos), escuta em :8000
python main.py
curl -X POST localhost:8000/intent -H 'content-type: application/json' \
  -d '{"intent": "Aumentar a taxa garantida da fatia de streaming para 20 Mbps entre 18h e 22h."}'
```

**Ferramentas disponíveis:**

Locais (só PostgreSQL):

| Ferramenta | Descrição |
|---|---|
| `record_intent` | Persiste intenção no PostgreSQL |
| `get_sla_status` | Lê KPIs da slice no banco |
| `update_intent_status` | Atualiza ciclo de vida da intenção |

De domínio, **descobertas via MCP** nos servidores CN-NSSMF/RAN-NSSMF na
primeira execução e apresentadas ao LLM com prefixo de agente (para os dois
`check_sla` não colidirem):

| Ferramenta (no LLM) | Servidor MCP | Ação remota |
|---|---|---|
| `cn_nssmf_apply_qos` / `cn_nssmf_revert_qos` / `cn_nssmf_query_nwdaf` / `cn_nssmf_check_sla` | `http://cn-nssmf:8001` | `apply_qos` / … |
| `ran_nssmf_apply_resources` / `ran_nssmf_revert_resources` / `ran_nssmf_check_sla` | `http://ran-nssmf:8002` | `apply_resources` / … |

**Data, hora e fuso:** toda intenção chega ao LLM precedida da data/hora atual
em `MINAS_TZ` (default `America/Sao_Paulo`, `agents/timeutil.py`), e horário
sem fuso é lido como local — "das 18h às 22h" vira 21:00–01:00 UTC no banco.

**Janelas (UC2), `scheduler.py`:** thread que a cada `SCHEDULER_INTERVAL_SECONDS`
(default 15s) faz duas coisas:
- *Ativação* — intenção cuja janela começa no futuro (mais de
  `ACTIVATION_GRACE_SECONDS`, default 60s) é gravada como `scheduled` e nada
  toca o core/RAN; o orquestrador bloqueia `cn_nssmf_apply_*`/`ran_nssmf_apply_*`
  para ela de forma determinística. No `window_start`, o scheduler manda as duas
  diretivas via MCP e marca `applied`/`degraded`/`failed` pelo status
  estruturado dos domínios; se um lado falha, desfaz o outro; janela que passou
  inteira sem ativar vira `failed`.
- *Reversão* — no `window_end`, chama `revert_qos`/`revert_resources`, que
  executam direto no banco, sem LLM. Só marca `reverted` quando os dois devolvem
  `reverted` (ou `noop`); senão tenta de novo.

**UC1, eventos (`POST /event`):** o monitor do CN-NSSMF avisa quando a NWDAF
prevê demanda acima da garantia. O endpoint responde 202 e, em segundo plano,
o LLM recebe um `## Event` para ampliar a slice até previsão × (1 +
`UC1_HEADROOM`); o resultado vem do status estruturado dos domínios. Se a RAN
só atende parte (`degraded`), é a degradação graciosa: evento
`sla_violation_predicted` na tabela `events` + WARNING no log (redistribuição
entre slices não implementada).

**Monitor de SLA (`sla_monitor.py`):** a cada `SLA_SAMPLE_SECONDS` (30s),
para cada intenção em vigor, grava em `sla_samples` o throughput observado da
slice vs. a garantia atual (só com telemetria fresca); após
`SLA_VIOLATION_STREAK` (3) amostras seguidas abaixo, evento `sla_violation`.

Nenhuma dessas threads roda no modo CLI (processo de execução única).

### CN-NSSMF (`agents/cn-nssmf/`) - esqueleto

**Monitor proativo do UC1 (`monitor.py`):** a cada `UC1_POLL_SECONDS` (30s)
consulta a NWDAF (`SLICE_LOAD_LEVEL`) para a intenção mais recente, em vigor e
sem janela, de cada slice em `UC1_SLICES` (default `2`). Se a demanda prevista
passar da garantia (GBR da política ativa) + `UC1_MARGIN` (10%), grava um evento
`predicted_exhaustion` e avisa o orquestrador (`POST /event`) — quem decide é o
orquestrador. Previsão mock/dado velho nunca dispara; no máximo um evento por
intenção a cada `UC1_COOLDOWN_SECONDS` (300s). `UC1_ENABLED=false` desliga
(recomendado durante benchmarks de LLM).

Agente de domínio do núcleo 5G. Sobe como **servidor MCP** (streamable HTTP,
endpoint `:8001/mcp`) e expõe as ações de diretiva como ferramentas MCP
(`apply_qos`, `revert_qos`, `query_nwdaf`, `check_sla`). Cada chamada dispara
um loop ReAct próprio sobre as ferramentas internas abaixo.

```bash
cd agents/cn-nssmf
pip install -r ../../requirements.txt
python main.py          # servidor MCP em :8001/mcp

# liveness
curl localhost:8001/health

# testar via MCP (Python) - precisa de Ollama pra o loop ReAct completar
python - <<'PY'
import sys; sys.path.insert(0, "..")   # agents/ no path
from mcp_common import list_remote_tools, call_remote_tool
print(list_remote_tools("http://localhost:8001"))
print(call_remote_tool("http://localhost:8001", "apply_qos",
                       {"intent_id": 1, "sst": 1, "target_thp_mbps": 20}))
PY
```

**Ferramentas internas (loop ReAct):**

| Ferramenta | Descrição | Estado |
|---|---|---|
| `query_nwdaf` | Analytics/predição da NWDAF via `Nnwdaf_AnalyticsInfo` (TS 23.288) | real p/ `SLICE_LOAD_LEVEL`; cai pra mock se a NWDAF estiver fora ou p/ os outros `analytics_id` |
| `configure_qos` | Aplica GBR/MBR/5QI da slice no PCF (PCC rule) + SMF (sessão) | stub (`# TODO` PCF/SMF); já grava a política em `policies` (o revert depende disso) |
| `revert_qos` | Restaura a configuração anterior de uma intent | real (tabela `policies`); determinístico quando chamado via MCP |
| `get_core_kpis` | Lê a telemetria de núcleo mais recente da slice | real (tabela `core_kpis`) |
| `record_policy` | Corrige os valores da política da intent (upsert, não duplica) | real (tabela `policies`) |

### RAN-NSSMF (`agents/ran-nssmf/`) - esqueleto

Agente de domínio do acesso rádio. Mesmo molde do CN-NSSMF: **servidor MCP**
(streamable HTTP, `:8002/mcp`), ferramentas `apply_resources`,
`revert_resources`, `check_sla`, cada chamada dispara um loop ReAct.

```bash
cd agents/ran-nssmf
pip install -r ../../requirements.txt
python main.py          # servidor MCP em :8002/mcp
curl localhost:8002/health
```

**Ferramentas internas (loop ReAct):**

| Ferramenta | Descrição | Estado |
|---|---|---|
| `get_ran_kpis` | Agrega os últimos ~30 s de `ran_kpis` da slice | real (tabela `ran_kpis`) |
| `get_slice_load` | Índice de carga da slice; computa e persiste se estiver defasado | real (tabela `slice_load`) |
| `estimate_capacity` | PRBs necessários p/ um alvo de throughput e se cabem no orçamento (desconta uso e reservas ativas das outras slices) | modelo estático (`# TODO` link adaptation) |
| `allocate_prb` | Define a fração de PRB da slice no gNB | stub (`# TODO` RIC/xApp E2); persiste em `ran_allocations` mas não toca o gNB |
| `revert_prb` | Restaura a alocação anterior de uma intent | real (tabela `ran_allocations`); determinístico quando chamado via MCP |

Modelo de rádio configurável por env: `RAN_PRB_TOTAL` (default 51), `RAN_MBPS_PER_PRB` (default 0.40).

### NWDAF (`agents/nwdaf/`)

Não é um agente ReAct - é um microsserviço de analytics chamado pelo
`query_nwdaf` do CN-NSSMF (`agents/cn-nssmf/nwdaf_client.py`). Expõe
`POST /analytics` no formato `Nnwdaf_AnalyticsInfo` (TS 23.288 / TS 29.520).

```bash
cd agents/nwdaf
pip install -r ../../requirements.txt
python main.py          # escuta em :8080

curl -X POST localhost:8080/analytics -H 'content-type: application/json' \
  -d '{"analytics_id": "SLICE_LOAD_LEVEL", "sst": 2, "horizon_seconds": 60}'
```

| `analytics_id` | Estado |
|---|---|
| `SLICE_LOAD_LEVEL` | real - `RandomForestRegressor` treinado sob demanda em janelas de lag sobre o histórico de `core_kpis.thp_dl_mbps` da slice, prevendo `horizon_seconds` à frente |
| `NF_LOAD` / `USER_DATA_CONGESTION` / `ABNORMAL_BEHAVIOUR` | mock - precisam de features que o schema ainda não coleta (métricas de NF, sinais de congestionamento por usuário, baseline de anomalia) |

Se o histórico ainda for curto (`collector` rodando há pouco tempo), cai pra
mock com `reason: "insufficient history in core_kpis"`; se a amostra mais
recente for mais velha que `NWDAF_MAX_STALENESS_S` (default 120 s — collector
parado), cai pra mock com `reason: "stale history ..."` em vez de prever sobre
dado velho. Se a NWDAF estiver
fora do ar, o `nwdaf_client` do CN-NSSMF absorve o erro e também cai pra mock,
o loop ReAct não quebra.

O script `agents/nwdaf/benchmark_rq4.py` roda a comparação RF vs. Gradient Boosting vs.
LSTM (prometida no TCC I cap. 4) de forma offline sobre o histórico de `core_kpis`:

```bash
POSTGRES_HOST=localhost python agents/nwdaf/benchmark_rq4.py
```

Inclui dois baselines ingênuos (`baseline_persistence` = último valor,
`baseline_train_mean`): um modelo só "aprende" algo se bater os dois. Sobre o
histórico do collector `mock` (ruído i.i.d.) todos empatam com a média; para
um resultado interpretável, rode sobre histórico sintético com estrutura num
banco dedicado:

```bash
docker exec postgres psql -U minas -d minas -c "CREATE DATABASE minas_synth"
docker exec -i postgres psql -U minas -d minas_synth < agents/schema.sql
POSTGRES_HOST=localhost POSTGRES_DB=minas_synth python agents/collector/backfill_synthetic.py --days 7
POSTGRES_HOST=localhost POSTGRES_DB=minas_synth NWDAF_HISTORY_LIMIT=20000 python agents/nwdaf/benchmark_rq4.py
```

O arquivo de saída leva o nome do banco (`rq4_minas_synth_*.jsonl`).

Configurável por env: `NWDAF_N_LAGS` (default 5), `NWDAF_HISTORY_LIMIT`
(default 500), `NWDAF_MIN_TRAINING_ROWS` (default 5 no serviço, 20 no RQ4),
`NWDAF_SLICE_CAPACITY_MBPS` (default `RAN_PRB_TOTAL × RAN_MBPS_PER_PRB` =
20,4 — o mesmo modelo de rádio da RAN; normaliza Mbps previsto em carga 0–1),
`NWDAF_MAX_STALENESS_S` (default 120). As janelas de lag (`agents/nwdaf/dataset.py`,
compartilhado com o RQ4) usam a mediana do intervalo entre amostras e descartam
janelas que atravessam buracos do collector.

### Collector (`agents/collector/`)

Amostrador de série temporal: a cada `COLLECT_INTERVAL` segundos grava uma linha
por slice em `core_kpis` **e** `ran_kpis`, para que as ferramentas de leitura dos
agentes (`get_core_kpis`, `get_sla_status`, …) e o modelo preditivo da NWDAF
tenham histórico.

Fonte plugável por tabela:

| Fonte | `core_kpis` | `ran_kpis` | Descrição |
|---|:---:|:---:|---|
| `prometheus` | ✔ (padrão) | ✔ | Prometheus HTTP API. Throughput **por slice** vem dos contadores das TUN da UPF (`ogstun` = SST 1, `ogstun2` = SST 2, via `upf-netdev`); UEs/sessões vêm do AMF/SMF (agregado). Na RAN, RSRP/SINR/MCS/PRB ficam `NULL`. |
| `o1` | - | ✔ | **ideal para RAN**: NETCONF/YANG contra o gNB (TS 28.552). Stub em `o1_client.py`, não conectado (depende do software do gNB, HANDOVER-2026-09-01 §3). |
| `synthetic` | ✔ | ✔ | série por slice **com estrutura temporal** (ciclo diário no fuso local + AR(1) + picos, `synth.py`); core e RAN usam o mesmo valor no ciclo e o PRB sai do modelo de rádio da RAN. Use em vez de `mock` quando algo aprende com os dados (NWDAF, UC1). |
| `mock` | ✔ | ✔ (padrão) | ruído i.i.d., só para exercitar o pipeline. |

UEs e sessões PDU ainda são agregados (Open5GS 2.6.4 tem poucas métricas
rotuladas por slice) - ver `# TODO(lab)` em `main.py`. Na stack mockada (sem
Prometheus), use `CORE_SOURCE=mock`: senão toda amostra de núcleo falha e
`core_kpis` para de crescer.

```bash
cd agents/collector
pip install -r ../../requirements.txt
CORE_SOURCE=mock RAN_SOURCE=mock SLICES=1,2 python main.py
```

| Variável | Padrão | Descrição |
|---|---|---|
| `PROMETHEUS_URL` | `http://prometheus:9090` | endpoint do Prometheus |
| `COLLECT_INTERVAL` | `10` | segundos entre amostras |
| `SLICES` | `1,2` | SSTs a coletar |
| `CORE_SOURCE` | `prometheus` | `prometheus` \| `synthetic` \| `mock` |
| `SLICE_TUN` | `1:ogstun,2:ogstun2` | SST → interface TUN da UPF |
| `RAN_SOURCE` | `mock` | `prometheus` \| `synthetic` \| `o1` \| `mock` |
| `SYNTH_SEED` | `0` | semente da fonte `synthetic` |

---

## Guardrails (`agents/guardrails.py`)

Validação determinística invocada **antes de todo `dispatch_tool`** nos três agentes.
Verifica restrições derivadas das specs 3GPP (TS 23.501, TS 28.312):
- SST ∈ {1, 2}
- throughput > 0
- GBR ≤ MBR
- 5QI ∈ [1, 86]
- `window_end` > `window_start`, e `window_end` ainda no futuro
- throughput / GBR / MBR ≤ `GUARDRAIL_MAX_RATE_MBPS` (default 1000 = Session-AMBR provisionado)

Uma violação retorna `{"error": "...", "guardrail": true}` como resultado da
ferramenta. O LLM recebe o erro e pode corrigir no próximo passo do ReAct.

Controlável por env para o benchmark de ablação (RQ3):

| Variável | Padrão | Efeito quando `false` |
|---|---|---|
| `GUARDRAILS_ENABLED` | `true` | Loga a violação mas não bloqueia a chamada |

---

## RAG (`agents/rag/`)

Corpus de 35 trechos curados das specs 3GPP (TS 23.501, TS 23.288, TS 28.312,
TS 38.300, TS 28.552) + padrões ReAct e MCP, embeddados com `BAAI/bge-small-en-v1.5`.
O orquestrador recupera os trechos mais similares à intenção recebida e os injeta
no contexto antes do loop ReAct começar.

Controlável por env para o benchmark de ablação (RQ3):

| Variável | Padrão | Efeito quando `false` |
|---|---|---|
| `RAG_ENABLED` | `true` | Loop ReAct roda sem contexto recuperado |

---

## Benchmark (`agents/benchmark/`)

Scripts para a avaliação sistemática do TCC II:

| Script | O que faz |
|---|---|
| `intent_set.py` | 16 intents com gabarito (SST, throughput, janela, ferramentas esperadas) |
| `run_benchmark.py` | Roda uma célula do fatorial (1 modelo × 1 configuração de ablação) e salva JSONL |
| `run_llm_benchmark.py` | Itera sobre múltiplos modelos: recria containers, aguarda /health, delega ao `run_benchmark.py`, imprime tabela comparativa |
| `run_window_probes.py` | Intenções com janelas curtas alguns minutos à frente: mede agendamento, aplicação antecipada (não pode haver), atraso de ativação, atraso de reversão e reversão correta |
| `sla_report.py` | Taxa de cumprimento de SLA (Seção 5.4) a partir de `sla_samples` |

Para rodar o benchmark de LLM (requer Docker + Ollama), com os monitores em
segundo plano desligados para não disparar chamadas ao LLM no meio das medições:

```bash
UC1_ENABLED=false SLA_MONITOR_ENABLED=false \
python agents/benchmark/run_llm_benchmark.py \
  --models qwen2.5:7b llama3.1:8b \
  --repeats 3

POSTGRES_HOST=localhost python agents/benchmark/run_window_probes.py --probes 4
POSTGRES_HOST=localhost python agents/benchmark/sla_report.py
```

A taxa de SLA só mede a garantia quando havia demanda na taxa-alvo (no lab,
iperf na taxa do intent durante a janela); com o collector mock/synthetic,
throughput baixo é slice ociosa, não violação.

---

## Banco de dados (PostgreSQL)

Schema em `agents/schema.sql`. Tabelas principais:

| Tabela | Descrição |
|---|---|
| `ran_kpis` | Métricas RAN por UE por slice (RSRP, SINR, PRBs, throughput) |
| `core_kpis` | Métricas do core por slice (UEs, PDU sessions, throughput) |
| `slice_load` | Índice de carga computado por slice |
| `intents` | Intenções recebidas pelo orquestrador |
| `negotiations` | Rodadas de negociação CN-NSSMF ↔ RAN-NSSMF |
| `policies` | Políticas de QoS aplicadas e seu ciclo de vida |
| `ran_allocations` | Alocações de PRB por intent (permite revert_prb rastreável) |
| `events` | Avisos do UC1 (`predicted_exhaustion`), violações de SLA (`sla_violation`, `sla_violation_predicted`) |
| `sla_samples` | Amostras do monitor de SLA por intent em vigor |

`schema.sql` só roda quando o volume do Postgres é criado. Para um banco que já
existe, aplique as migrações em ordem (são idempotentes):

```bash
for f in agents/migrations/*.sql; do docker exec -i postgres psql -U minas -d minas < "$f"; done
```

---


