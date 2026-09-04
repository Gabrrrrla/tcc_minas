# MINAS — Multi-Agent Intent-Driven Network Analytics and Slicing

TCC II — Ciência da Computação, UNISINOS  
Orientador: Prof. Dr. Cristiano Bonato Both

Sistema de orquestração autônoma de fatias de rede 5G baseado em Multi-Agent System (MAS) e Large Language Models (LLMs). O operador expressa objetivos em linguagem natural; o sistema interpreta, negocia recursos entre domínios e aplica as configurações nas funções de núcleo e acesso rádio.

---

## Arquitetura

```
Operador (linguagem natural)
        │
        ▼
┌─────────────────────┐
│    Orquestrador     │  ← LLM + ReAct + tool-calling
│  (agente central)   │
└──────┬──────────────┘
       │ diretivas
  ┌────┴────┐
  ▼         ▼
CN-NSSMF  RAN-NSSMF
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

**Três camadas:**
- **Intenção** — entrada do operador em linguagem natural
- **Orquestração agêntica** — Orquestrador + CN-NSSMF + RAN-NSSMF (agentes ReAct, sem fine-tuning)
- **Infraestrutura** — Open5GS 5G Core + srsRAN + Liteon Flexi DU/RU

**Dois slices:**
- `SST=1` (eMBB) — streaming / UC2
- `SST=2` (URLLC) — missão crítica / UC1

---

## Casos de uso

### UC1 — Predição e ajuste de recursos (SST=2)
CN-NSSMF consulta NWDAF periodicamente. A NWDAF aplica Random Forest sobre séries históricas de telemetria para prever utilização de recursos com horizonte de 60 segundos. Se a previsão ultrapassar o limiar, o orquestrador amplia os recursos da slice. Se insuficientes, aplica degradação graciosa e registra violação de SLA.

### UC2 — Política de QoS agendada (SST=1)
Orquestrador interpreta intenção com janela temporal, decompõe em duas diretivas paralelas (CN-NSSMF reconfigura QoS no PCF/SMF; RAN-NSSMF ajusta PRBs). Ao fim da janela, o orquestrador envia diretivas de reversão e restaura as configurações anteriores.

---

## Estrutura do repositório

```
tcc_II/
├── docker-compose.yml          # Stack completa (Open5GS + monitoramento + banco)
├── requirements.txt            # Dependências Python dos agentes
├── .env.example                # Variáveis de ambiente necessárias
│
├── open5gs/                    # Configurações do 5G Core
│   ├── amf.yaml                # SST=1 + SST=2, TAC=1, PLMN 00101
│   ├── smf.yaml                # DNN internet (10.45.0.0/16) + slice2 (10.46.0.0/16)
│   ├── upf.yaml                # ogstun + ogstun2, advertise 127.0.0.1
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
│   ├── schema.sql              # Schema PostgreSQL (KPIs, intents, negotiations, policies)
│   ├── Dockerfile              # imagem única dos 3 agentes (build context = raiz)
│   ├── db.py                   # conexão PostgreSQL — compartilhada
│   ├── react.py                # cliente Ollama + loop ReAct — compartilhado
│   ├── orchestrator/
│   │   ├── main.py             # HTTP (POST /intent) + CLI + system prompt 3GPP
│   │   └── tools.py            # 5 ferramentas + dispatcher
│   ├── cn-nssmf/               # esqueleto: servidor HTTP /directive
│   │   ├── main.py             # Flask (POST /directive, GET /health)
│   │   ├── tools.py            # 5 ferramentas + dispatcher
│   │   └── nwdaf_client.py     # stub das interfaces normativas da NWDAF (TS 23.288)
│   ├── ran-nssmf/               # esqueleto: servidor HTTP /directive
│   │   ├── main.py             # Flask (POST /directive, GET /health)
│   │   └── tools.py            # 5 ferramentas + dispatcher
│   └── collector/              # amostrador core_kpis + ran_kpis (fonte: prometheus | o1 | mock)
│       ├── main.py             # loop de coleta -> INSERT core_kpis / ran_kpis
│       └── o1_client.py        # stub da interface O1/NETCONF do gNB (fonte ideal p/ RAN)
│
├── scripts/
│   ├── provision.js            # Cadastra UE1 (SST=1+2) e UE2 (SST=2) no MongoDB
│   └── inspect.js              # Consulta subscriber no MongoDB
│
└── monitoring/
    ├── prometheus.yml          # Scrape: AMF, SMF, UPF (:9090)
    └── grafana/provisioning/
        └── datasources/
            └── prometheus.yml
```

---

## Serviços Docker

| Container | Imagem | IP | Porta exposta |
|---|---|---|---|
| mongodb | mongo:4.4 | 10.11.0.2 | — |
| nrf | gradiant/open5gs:2.6.4 | 10.11.0.10 | — |
| ausf | gradiant/open5gs:2.6.4 | 10.11.0.11 | — |
| udm | gradiant/open5gs:2.6.4 | 10.11.0.12 | — |
| udr | gradiant/open5gs:2.6.4 | 10.11.0.13 | — |
| pcf | gradiant/open5gs:2.6.4 | 10.11.0.14 | — |
| bsf | gradiant/open5gs:2.6.4 | 10.11.0.15 | — |
| nssf | gradiant/open5gs:2.6.4 | 10.11.0.16 | — |
| scp | gradiant/open5gs:2.6.4 | 10.11.0.17 | — |
| amf | gradiant/open5gs:2.6.4 | 10.11.0.20 | **38412/sctp** |
| smf | gradiant/open5gs:2.6.4 | 10.11.0.21 | — |
| upf | gradiant/open5gs:2.6.4 | 10.11.0.22 | **2152/udp** |
| webui | gradiant/open5gs-webui:2.6.4 | 10.11.0.30 | 3000 |
| postgres | postgres:16-alpine | 10.11.0.40 | 5432 |
| orchestrator | build `agents/Dockerfile` | 10.11.0.55 | 8000 |
| prometheus | prom/prometheus:v2.53.0 | 10.11.0.50 | 9090 |
| grafana | grafana/grafana:11.1.0 | 10.11.0.51 | 3001 |
| cn-nssmf | build `agents/Dockerfile` | 10.11.0.60 | 8001 |
| ran-nssmf | build `agents/Dockerfile` | 10.11.0.62 | 8002 |
| collector | build `agents/Dockerfile` | 10.11.0.61 | — |

---

## Como subir

### Pré-requisitos
- Docker e Docker Compose instalados
- (para os agentes) Python 3.11+ e Ollama rodando localmente

### 1. Clonar e configurar

```bash
git clone <url-do-repo>
cd tcc_II
cp .env.example .env
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
| Open5GS WebUI | http://localhost:3000 | admin / 1432 |
| Grafana | http://localhost:3001 | admin / minas |
| Prometheus | http://localhost:9090 | — |

---

## Integração com srsRAN (lab UNISINOS)

O srsRAN roda **fora do Docker**, no mesmo servidor físico. O gNB aponta para o AMF em:

```
amf:
  addr: 127.0.0.1
  port: 38412
```

---

## Agentes Python

Os agentes usam **Ollama** como servidor de inferência local — sem dependência de APIs externas, preservando a privacidade dos dados de telemetria.

### Instalar e subir o Ollama

```bash
# instalar: https://ollama.com
ollama pull llama3.1:70b   # baixa o modelo (necessário apenas uma vez)
ollama serve               # sobe o servidor de inferência em localhost:11434
```

### Orquestrador (`agents/orchestrator/`)

Recebe intenção em linguagem natural e conduz o loop ReAct até resolver. Roda em
dois modos:

```bash
cd agents/orchestrator
pip install -r ../../requirements.txt

# modo CLI (uma intenção, imprime o resultado)
python main.py "Aumentar a taxa de dados garantida para a fatia de streaming de 10 Mbps para 20 Mbps entre 18h e 22h."

# modo serviço (sem argumentos) — escuta em :8000
python main.py
curl -X POST localhost:8000/intent -H 'content-type: application/json' \
  -d '{"intent": "Aumentar a taxa garantida da fatia de streaming para 20 Mbps entre 18h e 22h."}'
```

**Ferramentas disponíveis:**

| Ferramenta | Descrição |
|---|---|
| `record_intent` | Persiste intenção no PostgreSQL |
| `get_sla_status` | Lê KPIs da slice no banco |
| `invoke_cn_nssmf` | Envia diretiva ao CN-NSSMF |
| `invoke_ran_nssmf` | Envia diretiva ao RAN-NSSMF |
| `update_intent_status` | Atualiza ciclo de vida da intenção |

### CN-NSSMF (`agents/cn-nssmf/`) — esqueleto

Agente de domínio do núcleo 5G. Sobe como serviço HTTP e recebe diretivas do
orquestrador em `POST /directive` (a ferramenta `invoke_cn_nssmf` do orquestrador
aponta para `http://cn-nssmf:8001`). Cada diretiva dispara um loop ReAct próprio.

```bash
cd agents/cn-nssmf
pip install -r ../../requirements.txt
python main.py          # escuta em :8001

# testar
curl -X POST localhost:8001/directive -H 'content-type: application/json' \
  -d '{"intent_id": 1, "action": "apply_qos", "sst": 1, "target_thp_mbps": 20}'
```

**Ferramentas disponíveis:**

| Ferramenta | Descrição | Estado |
|---|---|---|
| `query_nwdaf` | Analytics/predição da NWDAF via `Nnwdaf_AnalyticsInfo` (TS 23.288) | stub (mock) |
| `configure_qos` | Aplica GBR/MBR/5QI da slice no PCF (PCC rule) + SMF (sessão) | stub (`# TODO` PCF/SMF) |
| `revert_qos` | Restaura a configuração anterior de uma intent | real (tabela `policies`) |
| `get_core_kpis` | Lê a telemetria de núcleo mais recente da slice | real (tabela `core_kpis`) |
| `record_policy` | Persiste a política aplicada | real (tabela `policies`) |

### RAN-NSSMF (`agents/ran-nssmf/`) — esqueleto

Agente de domínio do acesso rádio. Mesmo molde do CN-NSSMF: serviço HTTP, recebe
diretivas do orquestrador em `POST /directive` (`http://ran-nssmf:8002`), cada uma
dispara um loop ReAct. Ações: `apply_resources`, `revert_resources`, `check_sla`.

```bash
cd agents/ran-nssmf
pip install -r ../../requirements.txt
python main.py          # escuta em :8002

curl -X POST localhost:8002/directive -H 'content-type: application/json' \
  -d '{"intent_id": 1, "action": "apply_resources", "sst": 1, "target_thp_mbps": 20}'
```

**Ferramentas disponíveis:**

| Ferramenta | Descrição | Estado |
|---|---|---|
| `get_ran_kpis` | Agrega os últimos ~30 s de `ran_kpis` da slice | real (tabela `ran_kpis`) |
| `get_slice_load` | Índice de carga da slice; computa e persiste se estiver defasado | real (tabela `slice_load`) |
| `estimate_capacity` | PRBs necessários p/ um alvo de throughput e se cabem no orçamento | modelo estático (`# TODO` link adaptation) |
| `allocate_prb` | Define a fração de PRB da slice no gNB | stub (`# TODO` RIC/xApp E2) |
| `revert_prb` | Restaura a alocação anterior de uma intent | stub (`# TODO` persistir alocações) |

Modelo de rádio configurável por env: `RAN_PRB_TOTAL` (default 51), `RAN_MBPS_PER_PRB` (default 0.40).

### Collector (`agents/collector/`)

Amostrador de série temporal: a cada `COLLECT_INTERVAL` segundos grava uma linha
por slice em `core_kpis` **e** `ran_kpis`, para que as ferramentas de leitura dos
agentes (`get_core_kpis`, `get_sla_status`, …) e o modelo preditivo da NWDAF
tenham histórico.

Fonte plugável por tabela:

| Fonte | `core_kpis` | `ran_kpis` | Descrição |
|---|:---:|:---:|---|
| `prometheus` | ✔ (padrão) | ✔ | Prometheus HTTP API sobre os exportadores Open5GS. RAN só consegue aproximar throughput pelos contadores N3 da UPF (HANDOVER-2026-08-25, "Opção 1"); RSRP/SINR/MCS/PRB ficam `NULL`. |
| `o1` | — | ✔ | **ideal para RAN**: NETCONF/YANG contra o gNB (TS 28.552). Stub em `o1_client.py` — não conectado (depende do software do gNB, HANDOVER-2026-09-01 §3). |
| `mock` | ✔ | ✔ (padrão) | linhas sintéticas, para desenvolver o pipeline antes das métricas reais existirem. |

Open5GS 2.6.4 expõe poucas métricas rotuladas por slice, então o agregado é
atribuído a todas as slices — ver `# TODO` sobre o rótulo `snssai` em `main.py`.

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
| `CORE_SOURCE` | `prometheus` | `prometheus` \| `mock` |
| `RAN_SOURCE` | `mock` | `prometheus` \| `o1` \| `mock` |

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
| `policies` | Políticas aplicadas e seu ciclo de vida |

---


