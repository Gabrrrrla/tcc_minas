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
│   ├── orchestrator/
│   │   ├── main.py             # Loop ReAct + system prompt 3GPP
│   │   ├── tools.py            # 5 ferramentas + dispatcher
│   │   └── db.py               # Conexão PostgreSQL
│   ├── cn-nssmf/               # (a implementar)
│   └── ran-nssmf/              # (a implementar)
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
| prometheus | prom/prometheus:v2.53.0 | 10.11.0.50 | 9090 |
| grafana | grafana/grafana:11.1.0 | 10.11.0.51 | 3001 |

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

### Placeholders pendentes em `srsran/gnb.yaml`

| Campo | Como obter |
|---|---|
| `GNB_BIND_ADDR` | IP do servidor na rede de fronthaul |
| `FRONTHAUL_IF` | `ip link show` no servidor |
| `DU_MAC` | `ip link show` no servidor |
| `RU_MAC` | MAC da Liteon Flexi |
| `DL_ARFCN` / `BAND` | Parâmetros configurados na Liteon |

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

Recebe intenção em linguagem natural e conduz o loop ReAct até resolver.

```bash
cd agents/orchestrator
pip install -r ../../requirements.txt
python main.py "Aumentar a taxa de dados garantida para a fatia de streaming de 10 Mbps para 20 Mbps entre 18h e 22h."
```

**Ferramentas disponíveis:**

| Ferramenta | Descrição |
|---|---|
| `record_intent` | Persiste intenção no PostgreSQL |
| `get_sla_status` | Lê KPIs da slice no banco |
| `invoke_cn_nssmf` | Envia diretiva ao CN-NSSMF |
| `invoke_ran_nssmf` | Envia diretiva ao RAN-NSSMF |
| `update_intent_status` | Atualiza ciclo de vida da intenção |

### CN-NSSMF (`agents/cn-nssmf/`) — a implementar
### RAN-NSSMF (`agents/ran-nssmf/`) — a implementar

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

## Variáveis de ambiente

Ver `.env.example`. Principais:

| Variável | Padrão | Descrição |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | URL do servidor Ollama |
| `MINAS_MODEL` | `llama3.1:70b` | Modelo LLM local |
| `CN_NSSMF_URL` | `http://cn-nssmf:8001` | URL do agente CN-NSSMF |
| `RAN_NSSMF_URL` | `http://ran-nssmf:8002` | URL do agente RAN-NSSMF |
| `POSTGRES_HOST` | `postgres` | Host do PostgreSQL |
