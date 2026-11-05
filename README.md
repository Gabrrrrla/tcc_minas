# MINAS - Multi-Agent Intent-Driven Network Analytics and Slicing

TCC II - Ciência da Computação, UNISINOS
Orientador: Prof. Dr. Cristiano Bonato Both

Sistema multiagente que traduz intenções de operador em linguagem natural em configuração das fatias de rede 5G, no núcleo e no acesso rádio. Um orquestrador decompõe cada intenção em diretivas para um agente de núcleo (CN-NSSMF) e um agente de rádio (RAN-NSSMF), coordenados por MCP. Guardrails determinísticos validam toda chamada de ferramenta, RAG ancora a interpretação em trechos das specs 3GPP, uma NWDAF prevê a carga das fatias, e código determinístico (não o modelo) ativa e reverte as intenções com janela.

**Estado (03/11/2026):**
- avaliação sem enforcement concluída: benchmark em 5 rodadas, baseline monolítico e probes de janela, de falha e do UC1 (seção [Benchmark e experimentos](#benchmark-e-experimentos));
- enforcement real implementado (`ENFORCEMENT_MODE=real`) e ensaiado com uma UPF real e um gNB simulado; **a validação no laboratório com gNB e UEs reais está pendente** (ver `LAB-2026-11-03.md`, não versionado).

---

## Arquitetura

```
Operador (linguagem natural)
        │  POST /intent
        ▼
┌─────────────────────┐      RAG (trechos 3GPP)
│    Orquestrador     │  ←   LLM + ReAct + tool-calling (cliente MCP)
│  + scheduler (código)│
└──────┬──────────────┘
       │ diretivas via MCP (streamable HTTP)
  ┌────┴─────────┐
  ▼              ▼
CN-NSSMF       RAN-NSSMF      ← servidores MCP; cada diretiva roda um loop ReAct próprio
(núcleo)       (rádio)
  │  │  NWDAF (RandomForest, Nnwdaf_AnalyticsInfo)
  │  │
  │  └─ ENFORCEMENT_MODE=real ─┐
  ▼                            ▼
upf-shaper (tc na UPF)      gnb_slicer (sched_cfg + restart do gNB)
Open5GS (Docker)            srsRAN Project (host do lab) ── RU física
```

A coordenação entre o orquestrador e os agentes de domínio é feita por **MCP** (Model Context Protocol): cada agente de domínio é um servidor MCP que expõe suas diretivas como ferramentas, e o orquestrador, como cliente, as descobre dinamicamente. A NWDAF usa HTTP, modelando a interface 3GPP `Nnwdaf_AnalyticsInfo` (TS 23.288 / TS 29.520).

**Duas fatias:**
- `SST=1` (eMBB, DNN `internet`, UEs em 10.45.0.0/16, TUN `ogstun`) - streaming / UC2
- `SST=2` (URLLC, DNN `slice2`, UEs em 10.46.0.0/16, TUN `ogstun2`) - missão crítica / UC1

---

## Casos de uso

### UC1 - Predição e ajuste de recursos (SST=2)
Intenção sem janela ("keep 6 Mbps guaranteed on the URLLC slice"). O monitor do CN-NSSMF consulta a NWDAF a cada 30 s, com horizonte de 60 s. Se a demanda prevista passa da garantia em vigor + 10 %, avisa o orquestrador, que eleva a garantia nos dois domínios para previsão × 1,1, ou marca `degraded` se a RAN não comporta. No TCC I, o UC1 era definido por disponibilidade de memória; foi redefinido em vazão, que é o que os dois domínios controlam.

### UC2 - Política de QoS agendada (SST=1)
Intenção com janela ("20 Mbps on the streaming slice from 18:00 to 22:00"). O orquestrador grava a intenção como `scheduled`; no início da janela o scheduler manda as duas diretivas (o CN-NSSMF configura a política de QoS e o RAN-NSSMF reserva PRBs); no fim, reverte as duas. A ativação e a reversão são feitas por código, não pelo modelo.

---

## Estrutura do repositório

```
tcc_minas/
├── docker-compose.yml          # Stack completa (Open5GS + monitoramento + banco + agentes)
├── docker-compose.lab.yml      # Overlay do lab: GTP-U pela bridge, NAT das UEs, iperf/iperf-b,
│                               #   collector real, upf-shaper, ENFORCEMENT_MODE=real
├── requirements.txt            # Dependências Python dos agentes
├── env.example                 # Variáveis de ambiente (copiar para .env)
│
├── open5gs/                    # Configurações do 5G Core
│   ├── amf.yaml                # SST=1 + SST=2, TAC=1, PLMN 00101
│   ├── smf.yaml                # DNN internet (10.45.0.0/16) + slice2 (10.46.0.0/16)
│   ├── upf.yaml                # ogstun + ogstun2, advertise 127.0.0.1
│   ├── upf.lab.yaml            # igual, advertise 10.11.0.22 (gNB no host, via bridge)
│   ├── amf.lab.yaml            # lab: S-NSSAI com SD (SST 1/SD 111111, SST 2/SD 222222), como o gNB do lab
│   ├── nssf.lab.yaml           # lab: mesmas S-NSSAI com SD
│   └── nrf/scp/ausf/udm/udr/pcf/nssf/bsf.yaml
│
├── srsran/
│   └── gnb.yaml                # Esqueleto do gNB; no lab, o template é o gnb.lab.yaml (não versionado),
│                               #   feito a partir da config do laboratório
│
├── agents/
│   ├── schema.sql              # Schema PostgreSQL
│   ├── migrations/             # ALTERs p/ bancos já criados (schema.sql só roda num volume novo)
│   ├── Dockerfile              # imagem única dos agentes (minas-agent:local; build context = raiz)
│   ├── db.py                   # conexão PostgreSQL compartilhada (thread-safe, autocommit)
│   ├── timeutil.py             # fuso do operador (MINAS_TZ): "agora" no prompt, horário sem fuso = local
│   ├── react.py                # cliente Ollama + loop ReAct compartilhado
│   ├── guardrails.py           # validação determinística de parâmetros (pré-dispatch, todos os agentes)
│   ├── mcp_common.py           # helpers de cliente MCP (list_remote_tools / call_remote_tool)
│   ├── enforcement.py          # cliente do enforcement real (push_core -> upf-shaper, push_ran -> gnb_slicer)
│   ├── enforcers/
│   │   ├── upf_shaper.py       # sidecar na UPF: limite de DL por fatia com tc tbf (porta 9300)
│   │   ├── gnb_slicer.py       # no host do gNB: min_prb_policy_ratio por fatia + restart (porta 8010)
│   │   └── Dockerfile          # imagem do upf-shaper
│   ├── orchestrator/
│   │   ├── main.py             # HTTP (POST /intent, POST /event) + CLI + RAG + system prompt; cliente MCP
│   │   ├── tools.py            # 3 ferramentas locais + descoberta MCP + dedup + agendamento
│   │   ├── scheduler.py        # janelas (UC2): ativa, reverte e limpa intenções failed com resíduo
│   │   └── sla_monitor.py      # amostra o SLA das intenções em vigor -> sla_samples / eventos
│   ├── cn-nssmf/
│   │   ├── main.py             # servidor MCP :8001/mcp: apply_qos/revert_qos/query_nwdaf/check_sla
│   │   ├── tools.py            # 5 ferramentas internas (ReAct) + dispatcher
│   │   ├── monitor.py          # UC1 proativo: consulta a NWDAF e avisa o orquestrador (POST /event)
│   │   └── nwdaf_client.py     # cliente da NWDAF (TS 23.288); cai pra mock se ela estiver fora
│   ├── ran-nssmf/
│   │   ├── main.py             # servidor MCP :8002/mcp: apply_resources/revert_resources/check_sla
│   │   └── tools.py            # 5 ferramentas internas (ReAct) + dispatcher + controle de admissão
│   ├── monolithic/
│   │   └── main.py             # baseline: um agente ReAct único com as 13 ferramentas (:8010)
│   ├── nwdaf/
│   │   ├── main.py             # Flask POST /analytics; RandomForest real p/ SLICE_LOAD_LEVEL
│   │   ├── dataset.py          # janelas de lag compartilhadas (serviço e RQ4)
│   │   └── benchmark_rq4.py    # RF vs GBM vs LSTM (+ baselines) offline sobre core_kpis (RQ4)
│   ├── rag/
│   │   ├── corpus.py           # 35 trechos curados (TS 23.501/23.288/28.312/38.300/28.552 + ReAct/MCP)
│   │   └── retriever.py        # embeddings bge-small-en-v1.5; top 3 com similaridade >= 0,35
│   ├── benchmark/              # benchmark, probes, baseline e figuras (ver seção própria)
│   └── collector/              # amostrador core_kpis + ran_kpis (prometheus | synthetic | o1 | mock)
│       ├── main.py             # loop de coleta -> INSERT core_kpis / ran_kpis
│       ├── synth.py            # tráfego sintético com estrutura temporal (diário + AR(1) + picos)
│       ├── backfill_synthetic.py # gera dias de histórico sintético num banco dedicado
│       └── o1_client.py        # stub da interface O1/NETCONF do gNB
│
├── scripts/
│   ├── provision.js            # Cadastra UE1 (SST=1+2) e UE2 (SST=2) no MongoDB
│   ├── provision.lab.js        # igual, com o SD do lab (IMSI/K/OPc reais entram só no dia, sem commit)
│   ├── inspect.js              # Consulta subscriber no MongoDB
│   ├── lab_preflight.sh        # checagens do host do lab ANTES de subir (SCTP, portas, sub-redes, Ollama, CPU, NTP)
│   ├── lab_smoke.sh            # teste de fumaça DEPOIS de subir (containers, health, enforcement, Prometheus, KPIs)
│   ├── lab_collect.sh          # salva tudo de um experimento em lab-data/<ts>_<label>/
│   └── gnb_restart.sh          # reinicia o gNB a partir da config gerada pelo gnb_slicer
│
└── monitoring/
    ├── prometheus.yml          # Scrape: AMF, SMF, UPF (:9090) + upf-netdev (:9100, TUN por fatia)
    └── grafana/provisioning/datasources/prometheus.yml
```

Não versionados (`.gitignore`): `tcc1/`, `tcc2/` (texto do TCC), `agents/benchmark/results/`, `lab-data/` e os `.md` de trabalho da raiz (handovers, revisão da banca, roteiro do lab).

---

## Serviços Docker

| Container | Imagem | IP | Porta exposta |
|---|---|---|---|
| mongodb | mongo:4.4 | 10.11.0.2 | - |
| nrf / ausf / udm / udr / pcf / bsf / nssf / scp | gradiant/open5gs:2.6.4 | 10.11.0.10–17 | - |
| amf | gradiant/open5gs:2.6.4 | 10.11.0.20 | **38412/sctp** |
| smf | gradiant/open5gs:2.6.4 | 10.11.0.21 | - |
| upf | gradiant/open5gs:2.6.4 | 10.11.0.22 | **2152/udp** |
| upf-netdev | prom/node-exporter:v1.8.2 | (rede do upf) | - |
| webui | gradiant/open5gs-webui:2.6.4 | 10.11.0.30 | 3000 |
| postgres | postgres:16-alpine | 10.11.0.40 | 5432 |
| prometheus | prom/prometheus:v2.53.0 | 10.11.0.50 | 9090 |
| grafana | grafana/grafana:11.1.0 | 10.11.0.51 | 3001 |
| orchestrator | build `agents/Dockerfile` | 10.11.0.55 | 8000 |
| cn-nssmf | build `agents/Dockerfile` | 10.11.0.60 | 8001 |
| collector | build `agents/Dockerfile` | 10.11.0.61 | - |
| ran-nssmf | build `agents/Dockerfile` | 10.11.0.62 | 8002 |
| nwdaf | build `agents/Dockerfile` | 10.11.0.63 | 8080 |

Só com o overlay `docker-compose.lab.yml`:

| Container | O que é | IP / porta |
|---|---|---|
| iperf | servidor `iperf3 -s` da UE-A | 10.11.0.70 |
| iperf-b | segundo servidor, para a UE-B (o `iperf3 -s` atende um cliente por vez) | 10.11.0.71 |
| upf-shaper | sidecar na rede do `upf` (`NET_ADMIN`), `agents/enforcers/upf_shaper.py` | upf:9300 |

O `gnb_slicer.py` não é container: roda nativo no host do gNB (porta 8010), porque o srsRAN roda fora do Docker.

---

## Como subir

### Pré-requisitos
- Docker e Docker Compose
- Ollama rodando no host (ou em outra máquina, via `OLLAMA_URL`) com `qwen2.5:7b`
- Python 3.11+ no host para os scripts de benchmark

### Modo benchmark (sem rádio, enforcement mock)

```bash
git clone <url-do-repo> && cd tcc_minas
cp env.example .env          # OLLAMA_URL, MINAS_MODEL, MINAS_TZ
docker compose up -d
docker compose logs -f amf        # AMF initialize...OK
docker compose logs provision     # Subscriber ...001 provisioned
curl localhost:8000/health
```

Sem gNB, ponha no `.env` `CORE_SOURCE=synthetic` e `RAN_SOURCE=synthetic` (o default do compose, `prometheus`, só tem dados com UEs gerando tráfego). É assim que todas as medições do TCC foram feitas: `ENFORCEMENT_MODE` não definido = `mock`, os agentes decidem, validam e gravam, mas nada é escrito na rede.

| Interface | URL | Credenciais |
|---|---|---|
| Open5GS WebUI | http://localhost:3000 | admin / 1423 |
| Grafana | http://localhost:3001 | admin / minas |
| Prometheus | http://localhost:9090 | - |

### Modo lab (srsRAN + UEs reais, enforcement real)

O srsRAN roda **fora do Docker** (DPDK), no mesmo servidor físico, e fala com o core direto pela bridge `minas-net`, sem NAT:

```
gNB (host) ──N2/SCTP──> AMF 10.11.0.20:38412      (bind do gNB: 10.11.0.1, gateway da bridge)
gNB (host) <─N3/GTP-U─> UPF 10.11.0.22:2152       (open5gs/upf.lab.yaml anuncia esse IP)
```

Resumo (o passo a passo completo, com os experimentos E0–E4, o que salvar e o plano B de cada passo, está em `LAB-2026-11-03.md`):

1. `bash scripts/lab_preflight.sh`: checa o host (SCTP, portas, sub-redes, Ollama, isolamento de CPU, NTP).
2. Template do gNB (`srsran/gnb.lab.yaml`, não versionado) a partir da config do laboratório, com `amf.addr 10.11.0.20`, `bind_addr 10.11.0.1`, N3 também em `10.11.0.1` e PLMN/TAC/S-NSSAI (com SD) iguais a `open5gs/amf.lab.yaml`. IMSI/K/OPc reais em `scripts/provision.lab.js`, **só localmente** (o repo é público).
3. No host do gNB, subir o `gnb_slicer.py` (ver [Enforcement real](#enforcement-real)).
4. `.env`: `ENFORCEMENT_MODE=real` (padrão do overlay), `GNB_SLICER_URL`, `ENFORCEMENT_TIMEOUT_S` maior que o tempo de restart do gNB, e `RAN_PRB_TOTAL`/`RAN_MBPS_PER_PRB` da célula (no lab: 100 MHz, SCS 30 kHz = 273 PRB; Mbps/PRB medido).
5. Subir:
   ```bash
   docker compose build
   docker compose -f docker-compose.yml -f docker-compose.lab.yml build upf-shaper
   docker compose -f docker-compose.yml -f docker-compose.lab.yml up -d
   ```
6. gNB → NG Setup no log do AMF → UEs → sessões nas duas fatias → `iperf3 -c 10.11.0.70 -R` (UE-B: `10.11.0.71`).
7. `SHAPER_WRITE_TEST=1 bash scripts/lab_smoke.sh` → 0 FAIL.
8. Depois de cada experimento: `bash scripts/lab_collect.sh <label> [minutos]`.

O LLM não deve disputar CPU com o gNB: em GPU, ou em outra máquina (`OLLAMA_URL`). Sem `isolcpus` nos núcleos do gNB, prenda os containers nos outros depois de subir: `docker update --cpuset-cpus <núcleos livres> $(docker ps -q)`.

---

## Agentes

Os agentes usam **Ollama** como servidor de inferência local, sem APIs externas.

```bash
ollama serve
ollama pull qwen2.5:7b     # modelo padrão do MINAS
```

### Escolha do modelo

O modelo é escolhido por `MINAS_MODEL`. O benchmark (5 rodadas, 240 execuções por condição) comparou três modelos de até 8 B parâmetros:

| Modelo | Intenções válidas atendidas | Observação |
|---|---|---|
| `qwen2.5:7b` | 88 % (77 % conferindo também o GBR do core) | o único que conduziu o loop nos dois domínios; **padrão** |
| `llama3.1:8b` | 51 % | completa as intenções com janela, quase nunca as do UC1 |
| `otel-llm-e4b-it` | 0 % | especializado em telecom; nenhuma chamada de ferramenta em 240 execuções |

**OTel-LLM-E4B-IT.** A série [OTel-LLM](https://huggingface.co/collections/farbodtavakkoli/otel-llm) é fine-tuned em specs 3GPP/O-RAN/ETSI/ITU com o formato pergunta + contexto recuperado + resposta. Sem RAG, o modelo se abstinha ("Answer not found in the retrieved context"); com RAG, passou a inventar respostas completas, também sem chamar ferramentas. O próprio model card avisa que o treino não tem exemplos de tool-calling. Para reproduzir, os modelos vêm em `.bin` PyTorch e precisam ser convertidos para GGUF (confira antes as quantizações prontas em `inference/ollama` no repositório da OTel):

```bash
huggingface-cli download farbodtavakkoli/OTel-LLM-E4B-IT --local-dir otel-e4b   # ~31,5 GB
git clone https://github.com/ggml-org/llama.cpp && pip install -r llama.cpp/requirements.txt
python llama.cpp/convert_hf_to_gguf.py otel-e4b --outfile otel-e4b-f16.gguf --outtype f16
./llama-quantize otel-e4b-f16.gguf otel-e4b-q4_k_m.gguf Q4_K_M    # binário de um release do llama.cpp
printf 'FROM ./otel-e4b-q4_k_m.gguf\n' | ollama create otel-llm-e4b-it -f -
```

### Orquestrador (`agents/orchestrator/`)

Recebe a intenção e conduz o loop ReAct. Modo serviço (`python main.py`, porta 8000) ou CLI (`python main.py "<intenção>"`, sem as threads de fundo).

```bash
curl -X POST localhost:8000/intent -H 'content-type: application/json' \
  -d '{"intent": "Guarantee 20 Mbps on the streaming slice (SST=1) from 18:00 to 22:00 tomorrow."}'
```

**Ferramentas.** Locais: `record_intent`, `get_sla_status`, `update_intent_status`. De domínio, descobertas via MCP e apresentadas com prefixo do agente: `cn_nssmf_apply_qos` / `cn_nssmf_revert_qos` / `cn_nssmf_query_nwdaf` / `cn_nssmf_check_sla` (`http://cn-nssmf:8001`) e `ran_nssmf_apply_resources` / `ran_nssmf_revert_resources` / `ran_nssmf_check_sla` (`http://ran-nssmf:8002`).

**Regras determinísticas:**
- *Data e fuso:* toda intenção chega ao LLM precedida da data/hora atual em `MINAS_TZ` (default `America/Sao_Paulo`); horário sem fuso é lido como local.
- *Agendamento:* intenção cuja janela começa no futuro (mais de `ACTIVATION_GRACE_SECONDS`, 60 s) é gravada como `scheduled`, e as chamadas `*_apply_*` para ela são bloqueadas em código.
- *Status final:* vem dos status estruturados dos domínios, não da resposta do modelo. Se um domínio falha, ou se só um dos dois foi acionado, os dois são revertidos e a intenção fica `failed`.
- *Dedup:* um pedido igual a uma intenção recebida nos últimos 2 minutos não cria outra; intenções `reverted` ou `failed` não bloqueiam um pedido novo.

**Scheduler (`scheduler.py`),** a cada `SCHEDULER_INTERVAL_SECONDS` (15 s):
- *Ativação:* no `window_start`, manda as duas diretivas via MCP e marca `applied`/`degraded`/`failed`; se um lado falha, desfaz o outro. Janela que fecha sem ativação: reverte os dois domínios e marca `failed`.
- *Reversão:* no `window_end`, chama `revert_qos`/`revert_resources` (determinísticos, sem LLM); só marca `reverted` quando os dois confirmam, senão tenta de novo.
- *Limpeza:* intenção `failed` que ainda tem política ou alocação ativa é revertida a cada ciclo.

**UC1, eventos (`POST /event`):** o monitor do CN-NSSMF avisa quando a NWDAF prevê demanda acima da garantia. O endpoint responde 202 e, em segundo plano, o LLM recebe um `## Event` para elevar a garantia a previsão × (1 + `UC1_HEADROOM`). Se a RAN só atende parte, a intenção fica `degraded` e é gravado um evento `sla_violation_predicted` (redistribuição entre fatias não implementada).

**Monitor de SLA (`sla_monitor.py`):** a cada 30 s grava em `sla_samples` a vazão observada de cada fatia com intenção em vigor; após 3 amostras seguidas abaixo da garantia, evento `sla_violation`. Só tem sentido com tráfego oferecido na taxa-alvo.

### CN-NSSMF (`agents/cn-nssmf/`)

Agente do núcleo. Servidor MCP (`:8001/mcp`) com as diretivas `apply_qos`, `revert_qos`, `query_nwdaf` e `check_sla`; cada chamada roda um loop ReAct próprio sobre as ferramentas internas:

| Ferramenta | O que faz |
|---|---|
| `query_nwdaf` | Analytics da NWDAF via `Nnwdaf_AnalyticsInfo`; real p/ `SLICE_LOAD_LEVEL`, mock p/ os outros ou se a NWDAF estiver fora |
| `configure_qos` | Define 5QI/GBR/MBR da fatia e grava em `policies`. Com `ENFORCEMENT_MODE=real`, antes de gravar, aplica o limite de DL na UPF (MBR = GBR). O 5QI/GBR não é provisionado no PCF/SMF |
| `revert_qos` | Restaura a configuração anterior (determinístico quando chamado via MCP) |
| `get_core_kpis` | Telemetria de núcleo mais recente da fatia |
| `record_policy` | Complementa uma política que já está em vigor; não cria política nova |

Dentro de uma diretiva `apply_qos`, o GBR é fixado em código no alvo da intenção: se o orquestrador não mandou o alvo, o CN-NSSMF o lê da intenção, e `configure_qos` recusa qualquer GBR diferente (desligado com `GUARDRAILS_ENABLED=false`).

**Monitor do UC1 (`monitor.py`):** a cada `UC1_POLL_SECONDS` (30 s) consulta a NWDAF para a intenção sem janela mais recente de cada fatia em `UC1_SLICES` (default `2`). Se a demanda prevista passa da garantia + `UC1_MARGIN` (10 %), grava `predicted_exhaustion` e avisa o orquestrador. Previsão mock ou dado velho nunca dispara; no máximo um evento por intenção a cada `UC1_COOLDOWN_SECONDS` (300 s). `UC1_ENABLED=false` desliga (recomendado durante o benchmark).

### RAN-NSSMF (`agents/ran-nssmf/`)

Agente do rádio. Servidor MCP (`:8002/mcp`) com `apply_resources`, `revert_resources` e `check_sla`:

| Ferramenta | O que faz |
|---|---|
| `get_ran_kpis` | Agrega os últimos ~30 s de `ran_kpis` da fatia |
| `get_slice_load` | Índice de carga da fatia (calcula e persiste se estiver defasado) |
| `estimate_capacity` | PRBs necessários para o alvo e se cabem: capacidade da célula menos uso e reservas das outras fatias |
| `allocate_prb` | Reserva PRBs da fatia e grava em `ran_allocations`; se o alvo não cabe, aloca o disponível e devolve `degraded`. Com `ENFORCEMENT_MODE=real`, antes de gravar, envia a reserva ao `gnb_slicer` |
| `revert_prb` | Restaura a alocação anterior (determinístico quando chamado via MCP) |

Modelo da célula por env: `RAN_PRB_TOTAL` (default 51 = 20 MHz com SCS 30 kHz) e `RAN_MBPS_PER_PRB` (default 0,40), ou seja, 20,4 Mbps. No lab, ajustar à capacidade medida.

### Baseline monolítico (`agents/monolithic/`)

Um único agente ReAct com as 13 ferramentas dos três agentes, o mesmo modelo, RAG, guardrails e regras de status/agendamento; muda só a estrutura (sem decomposição em diretivas nem MCP). Mesmo contrato `POST /intent` do orquestrador, na porta 8010. Como subir está no docstring de `agents/monolithic/main.py`.

### NWDAF (`agents/nwdaf/`)

Microsserviço de analytics (não é agente ReAct), chamado pelo `query_nwdaf` do CN-NSSMF. `POST /analytics` no formato `Nnwdaf_AnalyticsInfo`:

```bash
curl -X POST localhost:8080/analytics -H 'content-type: application/json' \
  -d '{"analytics_id": "SLICE_LOAD_LEVEL", "sst": 2, "horizon_seconds": 60}'
```

| `analytics_id` | Estado |
|---|---|
| `SLICE_LOAD_LEVEL` | real: `RandomForestRegressor` (100 árvores, profundidade 6) treinado sob demanda sobre as últimas amostras de `core_kpis.thp_dl_mbps` da fatia (5 lags, 10 s), prevendo `horizon_seconds` à frente |
| `NF_LOAD` / `USER_DATA_CONGESTION` / `ABNORMAL_BEHAVIOUR` | mock |

Com histórico curto ou velho (`NWDAF_MAX_STALENESS_S`, 120 s), cai para mock com o motivo no `reason`. Variáveis: `NWDAF_N_LAGS` (5), `NWDAF_HISTORY_LIMIT` (500), `NWDAF_MIN_TRAINING_ROWS` (5 no serviço, 20 no RQ4), `NWDAF_SLICE_CAPACITY_MBPS` (default `RAN_PRB_TOTAL × RAN_MBPS_PER_PRB`).

### Collector (`agents/collector/`)

A cada `COLLECT_INTERVAL` (10 s) grava uma linha por fatia em `core_kpis` e `ran_kpis`.

| Fonte | `core_kpis` | `ran_kpis` | Descrição |
|---|:---:|:---:|---|
| `prometheus` | ✔ (padrão) | ✔ | Vazão por fatia dos contadores das TUN da UPF (`upf-netdev`); UEs/sessões do AMF/SMF (agregado). RSRP/SINR/MCS/PRB ficam `NULL` |
| `o1` | - | ✔ | NETCONF/YANG contra o gNB (TS 28.552); stub, não conectado |
| `synthetic` | ✔ | ✔ | série com ciclo diário + AR(1) + picos (`synth.py`); usada no benchmark e no RQ4 |
| `mock` | ✔ | ✔ (padrão) | ruído i.i.d., só para exercitar o pipeline |

Variáveis: `PROMETHEUS_URL`, `COLLECT_INTERVAL`, `SLICES` (`1,2`), `CORE_SOURCE`, `RAN_SOURCE`, `SLICE_TUN` (`1:ogstun,2:ogstun2`), `SYNTH_SEED`. Sem Prometheus, use `CORE_SOURCE=synthetic` ou `mock`, senão toda amostra de núcleo falha.

---

## Guardrails (`agents/guardrails.py`)

Validação determinística antes de todo `dispatch_tool`, nos três agentes:
- SST ∈ {1, 2}
- vazão > 0
- GBR ≤ MBR
- 5QI ∈ [1, 86]
- `window_end` > `window_start`, e `window_end` no futuro
- vazão / GBR / MBR ≤ `GUARDRAIL_MAX_RATE_MBPS` (default 1000 = Session-AMBR provisionado)

Uma violação volta ao modelo como `{"error": "...", "guardrail": true}`, e ele pode corrigir no passo seguinte. `GUARDRAILS_ENABLED=false` (ablação do RQ3) só registra a violação, sem bloquear.

## RAG (`agents/rag/`)

Corpus de 35 trechos curados (TS 23.501, TS 23.288, TS 28.312, TS 38.300, TS 28.552, mais os padrões ReAct e MCP), embeddados com `BAAI/bge-small-en-v1.5`. Para cada intenção, o orquestrador injeta até 3 trechos com similaridade de cosseno ≥ 0,35. `RAG_ENABLED=false` (ablação do RQ3) roda o loop sem contexto recuperado.

---

## Enforcement real

`ENFORCEMENT_MODE=real` (padrão no `docker-compose.lab.yml`) faz cada decisão ser enviada ao equipamento **antes** de ser gravada. Se o envio falha, a ferramenta devolve erro ao agente e nada fica registrado como em vigor. Os dois pontos recebem sempre o estado completo (da fatia ou da célula), nunca um delta, para que um retry não desalinhe o testbed.

| Domínio | Ponto de enforcement | Como atua | Observação |
|---|---|---|---|
| Núcleo | `upf-shaper` (sidecar na UPF, `upf:9300`) | `tc tbf` no TUN da fatia (`ogstun`/`ogstun2`): limite de DL = MBR | É um **teto**, não uma garantia. `UPF_SHAPING=false` desliga o limite (necessário para o UC1 enxergar a demanda) |
| Rádio | `gnb_slicer.py` (nativo no host do gNB, porta 8010) | grava `min_prb_policy_ratio = ceil(100·PRB/PRB_TOTAL)` da fatia no `sched_cfg` do srsRAN e reinicia o gNB | O srsRAN só lê essa config no start: cada mudança reinicia o gNB e derruba as UEs por alguns segundos. É aqui que a garantia se realiza |

Subir o slicer no host do gNB:

```bash
sudo apt install -y python3-flask python3-yaml     # para o python3 do root
sudo GNB_TEMPLATE=srsran/gnb.lab.yaml \
     GNB_DIR=<pasta do gnb> GNB_BIN=./gnb GNB_CPUS=<núcleos do gNB> \
     GNB_EXTRA_ARGS="-c <outros yml>" \
     GNB_READY_PATTERN="<linha de pronto>" GNB_READY_TIMEOUT=120 \
     python3 agents/enforcers/gnb_slicer.py 2>&1 | tee gnb_slicer.log
```

O `scripts/gnb_restart.sh` (padrão de `GNB_RESTART_CMD`) roda `taskset -c $GNB_CPUS $GNB_BIN -c /tmp/gnb.minas.yaml $GNB_EXTRA_ARGS` a partir de `GNB_DIR`. O slicer mantém as entradas de fatia do template (inclusive o SD de cada uma) e só preenche o `sched_cfg`; `GNB_SLICE_SD` força um SD único e normalmente fica vazio. Ao iniciar, ele avisa se o template não tem a lista de fatias em `GNB_SLICING_PATH` (default `cell_cfg.slicing`) ou se os SST não batem. Variáveis dos agentes: `GNB_SLICER_URL` (default `http://10.11.0.1:8010`), `ENFORCEMENT_TIMEOUT_S` (default 90, deve cobrir o restart do gNB), `UPF_SHAPING` (default `true`).

---

## Benchmark e experimentos

Todos os scripts ficam em `agents/benchmark/`, rodam a partir da raiz do repo com `POSTGRES_HOST=localhost` e gravam em `agents/benchmark/results/` (não versionado). Durante as medições, desligue os monitores de fundo (`UC1_ENABLED=false SLA_MONITOR_ENABLED=false`) e mantenha `ENFORCEMENT_MODE=mock`.

| Script | O que mede | No TCC |
|---|---|---|
| `intent_set.py` | 16 intenções com gabarito: UC1/UC2 × explícita/coloquial × válida/inválida | Workload |
| `run_benchmark.py` | uma condição (modelo × RAG × guardrails), N repetições, JSONL | RQ1–RQ3 |
| `run_llm_benchmark.py` | várias condições: recria os containers, espera `/health`, chama o `run_benchmark.py` | RQ1–RQ3 |
| `run_rounds.py` | rodadas independentes das 6 condições, ordem embaralhada, retomável | RQ1–RQ3 (5 rodadas) |
| `run_monolithic.py` / `compare_monolithic.py` | baseline com um agente só e comparação pareada com o MINAS | Monolithic Baseline |
| `run_window_probes.py` | janelas curtas: agendamento, nada aplicado antes, atrasos de ativação e reversão | RQ1, ciclo de vida |
| `run_fault_probes.py` | falha da RAN (sem tool-calling, fora a janela toda, fora no fim da janela) | Fault Probes |
| `run_uc1_probes.py` | rampa/degrau de demanda sintética sobre uma intenção do UC1 | Proactive Adjustment |
| `run_enforcement_probe.py` | só no lab: vazão por fatia na UPF antes, durante e depois de uma janela | Enforcement (`% LAB`) |
| `classify_invalid.py` | classifica as respostas às intenções inválidas (recusa explicada, valor trocado, outro) | RQ3 |
| `sla_report.py` | taxa de cumprimento de SLA a partir de `sla_samples` | só com tráfego real |
| `plots.py` | figuras do capítulo de resultados (PDF + PNG) e os números por trás delas | Results |

O RQ4 (preditores) fica em `agents/nwdaf/benchmark_rq4.py`.

```bash
# RQ1-RQ3: 5 rodadas x 6 condições x 16 intenções x 3 repetições
UC1_ENABLED=false SLA_MONITOR_ENABLED=false python agents/benchmark/run_rounds.py --rounds 5 --first 1

# baseline monolítico (agente em :8010, monitores da stack parados)
python agents/benchmark/run_monolithic.py --rounds 5
python agents/benchmark/compare_monolithic.py

# probes
python agents/benchmark/run_window_probes.py --probes 4
python agents/benchmark/run_fault_probes.py --reps 2
python agents/benchmark/run_uc1_probes.py --reps 3

# RQ4 sobre histórico sintético num banco dedicado
docker exec postgres psql -U minas -d minas -c "CREATE DATABASE minas_synth"
docker exec -i postgres psql -U minas -d minas_synth < agents/schema.sql
POSTGRES_DB=minas_synth python agents/collector/backfill_synthetic.py --days 7
POSTGRES_DB=minas_synth NWDAF_HISTORY_LIMIT=20000 python agents/nwdaf/benchmark_rq4.py

# figuras
python agents/benchmark/plots.py \
  --rounds agents/benchmark/results/run2 agents/benchmark/results/rounds/r* \
  --probes agents/benchmark/results/probes-final/window-probes_rep*.jsonl \
  --rq4    agents/benchmark/results/rq4_minas_synth_*.jsonl \
  --out    tcc2/figures/results
python agents/benchmark/plots.py --enforcement <enforcement-probe.jsonl> --out tcc2/figures/results
```

**Imagens.** A imagem medida no benchmark está guardada como `minas-agent:bench-c9781c3`; `minas-agent:local` tem as correções posteriores (D13–D16 do TCC: rollback no fechamento da janela, GBR do core fixado no alvo, status final pelos domínios, `record_policy` só sobre política em vigor). Para reproduzir os números do TCC, use a imagem guardada.

---

## Banco de dados (PostgreSQL)

Schema em `agents/schema.sql`:

| Tabela | Descrição |
|---|---|
| `intents` | Intenções: fatia, alvo, janela, status (`scheduled`/`applied`/`degraded`/`failed`/`reverted`) |
| `policies` | Políticas de QoS por intenção (5QI, GBR, MBR) e seu ciclo de vida |
| `ran_allocations` | Reservas de PRB por intenção (permite `revert_prb` rastreável) |
| `core_kpis` | Métricas do núcleo por fatia (UEs, sessões PDU, vazão) |
| `ran_kpis` | Métricas de rádio por fatia (RSRP, SINR, PRBs, vazão) |
| `slice_load` | Índice de carga calculado por fatia |
| `events` | Avisos do UC1 (`predicted_exhaustion`) e violações de SLA (`sla_violation`, `sla_violation_predicted`) |
| `sla_samples` | Amostras do monitor de SLA por intenção em vigor |
| `negotiations` | Rodadas de negociação CN-NSSMF ↔ RAN-NSSMF (não usada pelo fluxo atual) |

`schema.sql` só roda quando o volume do Postgres é criado. Para um banco que já existe, aplique as migrações em ordem (são idempotentes):

```bash
for f in agents/migrations/*.sql; do docker exec -i postgres psql -U minas -d minas < "$f"; done
```
