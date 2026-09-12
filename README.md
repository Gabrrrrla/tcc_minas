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

A coordenação orquestrador ↔ agentes de domínio é feita por **MCP** (Model
Context Protocol): cada agente de domínio roda um servidor MCP que expõe suas
ações de diretiva como ferramentas; o orquestrador é cliente MCP e descobre
essas ferramentas dinamicamente. A NWDAF fica em HTTP de propósito — modela a
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
│   ├── db.py                   # conexão PostgreSQL - compartilhada
│   ├── react.py                # cliente Ollama + loop ReAct - compartilhado
│   ├── orchestrator/
│   │   ├── main.py             # HTTP (POST /intent) + CLI + system prompt 3GPP; cliente MCP
│   │   ├── tools.py            # 3 ferramentas locais + descoberta MCP das ferramentas de domínio
│   │   └── scheduler.py        # thread de reversão por janela temporal (UC2); revert via MCP
│   ├── mcp_common.py          # helpers de cliente MCP (list_remote_tools / call_remote_tool)
│   ├── cn-nssmf/               # esqueleto: servidor MCP (streamable HTTP, :8001/mcp)
│   │   ├── main.py             # FastMCP; ferramentas apply_qos/revert_qos/query_nwdaf/check_sla + GET /health
│   │   ├── tools.py            # 5 ferramentas internas (ReAct) + dispatcher
│   │   └── nwdaf_client.py     # cliente da NWDAF (TS 23.288); cai pra mock se ela estiver fora
│   ├── ran-nssmf/               # esqueleto: servidor MCP (streamable HTTP, :8002/mcp)
│   │   ├── main.py             # FastMCP; ferramentas apply_resources/revert_resources/check_sla + GET /health
│   │   └── tools.py            # 5 ferramentas internas (ReAct) + dispatcher
│   ├── nwdaf/                   # analytics - não é agente ReAct, é serviço de ML
│   │   └── main.py             # Flask (POST /analytics); Random Forest real p/ SLICE_LOAD_LEVEL
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
| nwdaf | build `agents/Dockerfile` | 10.11.0.63 | 8080 |
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
| Prometheus | http://localhost:9090 | - |

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
ollama serve               # sobe o servidor de inferência em localhost:11434
ollama pull qwen2.5:7b     # modelo padrão do MINAS (ver justificativa abaixo)
```

### Escolha do modelo LLM

O modelo é selecionado pela variável `MINAS_MODEL` no `.env`.

**`qwen2.5:7b` - padrão do MINAS (desde 12/09/2026):**

Escolhido depois de um smoke test ao vivo comparar 3 candidatos: foi o único que conduziu o loop ReAct completo de ponta a ponta (`record_intent` → `cn_nssmf_*`/`ran_nssmf_*` → `update_intent_status`) de forma consistente, usando o protocolo de tool-calling estruturado do Ollama corretamente. Sem fine-tuning de telecom, a mitigação de alucinação de domínio depende inteiramente dos `guardrails.py` determinísticos. Isso foi um teste informal (poucas execuções), não o benchmark rigoroso que o TCC I promete (P6: comparar candidatos num conjunto de intenções derivado da TS 28.312, medindo acurácia de tool-calling), esse benchmark formal ainda é trabalho pendente.

**Por que não OTel-LLM-E4B-IT (ou qualquer outro tamanho da família OTel-LLM):**

O projeto [OTel (Open Telco AI)](https://github.com/farbodtavakkoli/OTel), com contribuição da GSMA, disponibiliza a série [OTel-LLM](https://huggingface.co/collections/farbodtavakkoli/otel-llm) (270 M–32 B parâmetros), fine-tuned em specs 3GPP/O-RAN/ETSI/ITU. Era a escolha óbvia pro domínio do MINAS, e chegou a ser o padrão por um tempo: **OTel-LLM-E4B-IT** tem 91,7% de correctness no eval "context-grounded generation" da própria OTel (melhor resultado publicado na categoria).

**Não funcionou:** toda a família OTel-LLM é treinada com a mesma receita, pergunta + trecho de contexto recuperado + resposta, mais exemplos de **abstenção** quando o contexto não contém a resposta. Como o MINAS ainda não tem RAG (item pendente P3, injetar trechos de TS 28.312/23.288 antes do loop ReAct), o modelo nunca recebe o bloco de "contexto recuperado" que espera, e o reflexo treinado é abster-se em vez de tentar uma ferramenta. No smoke test ele **nunca chamou nenhuma ferramenta**, respondendo direto "Answer not found in the retrieved context." Isso não se resolve com ajuste de prompt, é a receita de treino inteira, compartilhada por todos os tamanhos da série, não só o E4B. (`llama3.1:8b` também foi testado e descartado: emite a chamada de ferramenta como texto solto em vez de usar o campo `tool_calls` estruturado do Ollama.)

Retomar o OTel quando o RAG (P3) existir, alimentá-lo com um bloco de contexto recuperado de verdade pode destravar o comportamento pretendido; até lá, ele não consegue conduzir o loop ReAct de jeito nenhum (não é "pior que o qwen", é não-funcional nesse uso).

Guia de conversão (mantido pra quando isso for revisitado, os modelos são publicados em `.bin` pytorch; pra usar via Ollama, converter para GGUF com `llama.cpp`; a OTel também lista quantizações prontas em `inference/ollama` no repo, **confira lá primeiro**, pode poupar todo o processo abaixo):

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

Outras variantes da série, por tamanho *efetivo* nomeado pela OTel (o tamanho real em disco pode ser maior, como no E4B acima — confira o repositório de cada uma antes de baixar):

| Modelo | Parâmetros (nome) | Observação |
|---|---|---|
| OTel-LLM-1B-IT | 1 B | menor da linha, CPU ok |
| OTel-LLM-3B-IT | 3 B | leve |
| OTel-LLM-E4B-IT | "E4B" (efetivo) | ~8B params reais, ~31,5GB de download, ~5GB depois de quantizado — não funciona sem RAG (ver acima) |
| OTel-LLM-7B-IT | 7 B | — |
| OTel-LLM-8.3B-IT | 8.3 B | — |
| OTel-LLM-14B-IT | 14 B | maior, exige mais RAM/VRAM |

**Genérico (fallback, sem fine-tuning de domínio):**
```bash
ollama pull llama3.1:70b
MINAS_MODEL=llama3.1:70b
```



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

Locais (só PostgreSQL):

| Ferramenta | Descrição |
|---|---|
| `record_intent` | Persiste intenção no PostgreSQL |
| `get_sla_status` | Lê KPIs da slice no banco |
| `update_intent_status` | Atualiza ciclo de vida da intenção |

De domínio - **descobertas via MCP** nos servidores CN-NSSMF/RAN-NSSMF na
primeira execução e apresentadas ao LLM com prefixo de agente (pra os dois
`check_sla` não colidirem):

| Ferramenta (no LLM) | Servidor MCP | Ação remota |
|---|---|---|
| `cn_nssmf_apply_qos` / `cn_nssmf_revert_qos` / `cn_nssmf_query_nwdaf` / `cn_nssmf_check_sla` | `http://cn-nssmf:8001` | `apply_qos` / … |
| `ran_nssmf_apply_resources` / `ran_nssmf_revert_resources` / `ran_nssmf_check_sla` | `http://ran-nssmf:8002` | `apply_resources` / … |

**Reversão por janela temporal (UC2):** o serviço sobe uma thread
(`scheduler.py`) que a cada `SCHEDULER_INTERVAL_SECONDS` (default 15s)
verifica `intents` com `window_end` vencido e status `applied`/`degraded`, e
chama as ferramentas MCP `revert_qos`/`revert_resources` nos servidores
CN-NSSMF/RAN-NSSMF - sem passar pelo LLM, já que é um gatilho determinístico
por tempo. Só marca a intent como `reverted` quando os dois agentes
confirmam; senão tenta de novo na próxima varredura. Não roda no modo CLI
(processo de execução única).

### CN-NSSMF (`agents/cn-nssmf/`) - esqueleto

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
| `configure_qos` | Aplica GBR/MBR/5QI da slice no PCF (PCC rule) + SMF (sessão) | stub (`# TODO` PCF/SMF) |
| `revert_qos` | Restaura a configuração anterior de uma intent | real (tabela `policies`) |
| `get_core_kpis` | Lê a telemetria de núcleo mais recente da slice | real (tabela `core_kpis`) |
| `record_policy` | Persiste a política aplicada | real (tabela `policies`) |

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
| `estimate_capacity` | PRBs necessários p/ um alvo de throughput e se cabem no orçamento | modelo estático (`# TODO` link adaptation) |
| `allocate_prb` | Define a fração de PRB da slice no gNB | stub (`# TODO` RIC/xApp E2) |
| `revert_prb` | Restaura a alocação anterior de uma intent | stub (`# TODO` persistir alocações) |

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
mock com `reason: "insufficient history in core_kpis"`. Se a NWDAF estiver
fora do ar, o `nwdaf_client` do CN-NSSMF absorve o erro e também cai pra mock,
o loop ReAct não quebra. Comparação com Gradient Boosting e LSTM (pedida no
TCC I cap. 4) é trabalho de avaliação para o relatório, não foi feita aqui.

Configurável por env: `NWDAF_N_LAGS` (default 5), `NWDAF_HISTORY_LIMIT`
(default 500), `NWDAF_MIN_TRAINING_ROWS` (default 5),
`NWDAF_SLICE_CAPACITY_MBPS` (default 100 - normaliza Mbps previsto em carga 0–1).

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
atribuído a todas as slices - ver `# TODO` sobre o rótulo `snssai` em `main.py`.

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


