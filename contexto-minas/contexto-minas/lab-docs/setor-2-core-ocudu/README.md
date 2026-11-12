# Setor 2 — Core em Kubernetes + OCUDU + LiteOn (início em 2026-10-21)

Organização pedida pelo usuário: **o que já foi feito é o Setor 1** (core Open5GS em k3s com UERANSIM — arquivos 01–08
da raiz deste repositório e a versão `v1.0-ueransim` do open5GK3S). **Não se mexe no Setor 1.** Tudo o que for novo,
a partir de agora, é documentado **aqui**, reaproveitando o conhecimento e o código anteriores.

## Git do open5GK3S

| Referência | O que é |
|---|---|
| tag **`v1.0-ueransim`** (commit `86a965a`) | Setor 1 congelado: core + UERANSIM, validado no torre2 (7/7, reboot OK) |
| branch `main` | parada no mesmo commit do Setor 1 |
| branch **`ocudu`** | **Setor 2**: desenvolvimento novo. O clone do torre2 está nela (`git switch ocudu`) |

Voltar ao Setor 1 a qualquer momento: `git switch --detach v1.0-ueransim` (ou `git switch main`).

## Ponto de partida técnico

- Link core ↔ gNB pronto e permanente: innovaserver `ens4f3np3` 10.10.0.1/24 ↔ torre2 `enp5s0f1` 10.10.0.2/24, DAC 10G,
  MTU 9000 (detalhes no `../06-ran-ocudu-liteon.md`).
- Próximo passo técnico: AMF e UPF do torre2 com IP no link (Multus + macvlan) e gNB apontando para eles.

## Proposta: um arquivo de configuração único para core e OCUDU (em estudo)

Ideia: o `config.env` (na branch `ocudu`) passa a ser a fonte única de **tudo o que precisa bater dos dois lados**:
PLMN, TAC, slices, IPs de N2/N3 e a interface do link. A partir dele o projeto gera:
- os values do Helm do core (como hoje) + a rede Multus/macvlan do AMF e do UPF;
- um **arquivo parcial do OCUDU** (ex.: `ran/ocudu/generated/core-link.yml`) com `cu_cp.amf.addr/bind_addr`, PLMN,
  TAC, slices e o bind do N3. O gNB é iniciado com a config do RU escolhida **mais** esse arquivo:
  `./gnb -c gnb_ru_liteon_SLICE.yml -c core-link.yml` (o OCUDU aceita vários `-c`; o repositório dele já traz arquivos
  parciais como `slicing.yml`, `qos.yml`, `low_latency.yml`). A ordem/mescla de listas precisa ser testada.
- O que é do hardware do RU (MACs, VLAN, CPUs, janelas t1a/ta4, antenas) continua nos arquivos do innovaserver, que o
  usuário escolhe conforme o experimento.

## Diário

### 2026-10-21 — slices e assinantes
- Assinantes do innovaserver (só campos não secretos lidos): 6 SIMs.
  | IMSI | Slices (SST/SD → DNNs) |
  |---|---|
  | 001010123456789 | 1/— → internet, ims |
  | 001010000000040 | 1/111111 → internet, ims |
  | 001010000000001 | 1/111111 → internet, ims; 2/222222 → internet2 |
  | 001010000000032 | 1/— → internet |
  | 001010000000022 | 1/111111 → internet; 2/222222 → internet2 |
  | 001010000000052 | 1/111111 → internet |
- Core do innovaserver: 20 S-NSSAI (SST 1–4 × sem SD/111111/222222/333333/444444), pool único 10.45.0.0/16 sem DNN.
- Branch `ocudu`, commit `9218542`: `SLICES` = 1/111111 internet 10.45/16 + **2/222222 internet2 10.46/16** (as duas
  que o `gnb_ru_liteon_SLICE.yml` anuncia); UE simulado mudou para **MSIN 0000009001** (o 0000000001 é um SIM real).
- **Não coberto (limite do gerador, decisão: deixar para depois):** slice sem SD (SIMs 0123456789 e 0032) e DNN `ims`
  dentro da 1/111111 (o projeto exige um DNN por slice). Celulares usam `internet` por padrão.
- Cópia dos assinantes: o assistente **não** copia chaves de SIM (bloqueado como manuseio de credencial). O usuário roda
  a cópia direta innovaserver → torre2 (`mongoexport | mongoimport --mode=upsert --upsertFields=imsi`).
- **Concluído (2026-10-21):** install da branch `ocudu` aplicado (AMF com 1/111111 e 2/222222; `test.sh` 7/7 com o UE
  simulado `001010000009001`). Os 6 SIMs do innovaserver estão no MongoDB do torre2 (7 assinantes no total).
  Primeira importação parou no meio: o registro antigo `…0001` (UE simulado de antes) tinha outro `_id` e o upsert
  falhou, abortando o lote. Solução: apagar o registro antigo e importar de novo.
  Cuidado: um `uninstall.sh` apaga o PVC do MongoDB e, com ele, esses SIMs; para recuperar, repetir a importação.

### 2026-10-21 — Multus instalado no torre2 (branch `ocudu`, arquivos em `helm/multus/`)
- `helm upgrade --install multus rke2-multus --repo https://rke2-charts.rancher.io --version v4.3.110 -n kube-system -f helm/multus/values.yaml`
  com as pastas do k3s (`confDir`/`multusAutoconfigDir` = `/var/lib/rancher/k3s/agent/etc/cni/net.d`,
  `binDir` = `/var/lib/rancher/k3s/data/cni/`). O init `cni-plugins` instala macvlan, static etc. (o k3s não traz).
- Pegadinhas: (1) sem `multusAutoconfigDir` o Multus procura o flannel em `/etc/cni/net.d` e não sobe a config;
  (2) o chart **não traz o CRD** `NetworkAttachmentDefinition`: `kubectl apply -f helm/multus/crd.yaml`
  (cópia do upstream multus-cni v4.2.2).
- Rede `ran-link` (namespace open5gs): macvlan bridge sobre `enp5s0f1`, MTU 9000, IP por anotação no pod.
- Teste com pod descartável 10.10.0.99: ping 10.10.0.1 OK (0,2 ms, jumbo 8972 OK), internet pelo eth0 OK.
- Macvlan: o host (10.10.0.2) não enxerga os pods na ran-link; o innovaserver enxerga.
- Achado: o chart do AMF (Gradiant) fixa `ngap.server: - dev: eth0` (sem value); o do UPF aceita `config.upf.gtpu.dev`;
  o do UERANSIM gNB aceita `interfaces.n2/n3.dev`, `amf.ip` e `podAnnotations`.
- **Pausa (2026-10-21):** usuário pediu para não mexer em nada enquanto sobe a rede no innovaserver. Pendente a
  decisão A (AMF, UPF e gNB simulado na ran-link; recomendada) × B (AMF/UPF na ran-link, UERANSIM desligado).
  Estado deixado: Multus + ran-link instalados no torre2, core intacto (16 pods, test.sh 7/7).

### 2026-10-21 — Opção A implementada (branch `ocudu`, commit `66fca79`, decisão 30 no open5GK3S)
- `config.env`: `RAN_LINK_ENABLED=true`, `RAN_LINK_IFACE=enp5s0f1`, `RAN_LINK_CIDR=10.10.0.0/24`, `RAN_LINK_MTU=9000`,
  **AMF_N2_IP=10.10.0.10**, **UPF_N3_IP=10.10.0.11**, **GNB_SIM_IP=10.10.0.20**, `MULTUS_CHART_VERSION=v4.3.110`.
- `install.sh`: novo passo `step_ran_link` (CRD + Multus + NAD gerada em `helm/generated/ran-link-nad.yaml`).
- AMF: anotação Multus + post-renderer `helm/postrender/amf-ngap-net1.sh` (N2 em net1). UPF: `gtpu.dev: net1`.
  gNB simulado: `amf.ip`, `n2/n3 dev net1`, rádio no eth0. `uninstall.sh` remove o Multus e o CRD.
- Validado a seco (validate_config, helm template + post-renderer). Falta: `sudo ./install.sh` pelo usuário + test.sh.
- Para o gNB real (OCUDU), quando for a hora: `cu_cp.amf.addr: 10.10.0.10`, `cu_cp.amf.bind_addr: 10.10.0.1`,
  N3 bind `10.10.0.1` (o UPF responde em 10.10.0.11).
- **Aplicado e validado (2026-10-21 17:39):** install OK; AMF net1 10.10.0.10, UPF net1 10.10.0.11, gNB simulado
  net1 10.10.0.20; AMF "gNB-N2 accepted[10.10.0.20]"; UPF `gtp_server [10.10.0.11]:2152`; `test.sh` 7/7 (NG Setup
  pelo link, ping 8.8.8.8 OK). Do innovaserver: ping 10.10.0.10 e 10.10.0.11 com jumbo (8972, DF) ~0,3 ms.
- **Próximo passo:** apontar o gNB real (OCUDU) para o core do torre2 — precisa de permissão para mexer no innovaserver.

### 2026-10-21 — Config do gNB para o core do torre2 (innovaserver, com permissão do usuário)
- Criado `~/ocudu/configs/gnb_ru_liteon_SLPIT_CORE.yml` (nome pedido pelo usuário) = cópia da `gnb_ru_liteon_SLICE.yml`
  com `cu_cp.amf.addr: 10.10.0.10`, `cu_cp.amf.bind_addr: 10.10.0.1` e `cu_up.ngu.socket[0].bind_addr: 10.10.0.1`.
  A original não foi alterada. Subir (em `~/ocudu/build/apps/gnb`):
  `sudo ./gnb -c ../../../configs/gnb_ru_liteon_SLPIT_CORE.yml -c ../../../configs/metricas.yml`.
- Pré-voo às 14:58: GNSS travado, relógio = UTC, **XDP ausente** (bad message ~1800/min) e gNB rodando com a SLICE.yml
  (core bare metal). Recarregar o XDP com o link do RU de pé antes do teste.

### 2026-10-21 18:03 UTC — primeiro gNB real no core do torre2
- `gnb_ru_liteon_SLPIT_CORE.yml`: **NG Setup OK** (`gNB-N2 accepted[10.10.0.1]`), SIM `…0001` **registrou**;
  sessão `internet2` (2/222222) recebeu **10.46.0.2**, UPF `gtp_connect [10.10.0.1]:2152`.
  `ims` recusado ("No IP addresses available": DNN ims não existe no torre2) — esperado.
- **Problema PFCP (causa provável da "dessincronia" da decisão 28):** desde 17:39:44 o UPF responde
  `Cannot find PFCP-Node … from [10.42.0.68]:16098`. O SMF fala com o UPF pelo **Service** `open5gs-upf-pfcp`
  (ClusterIP 10.43.82.211) e o UPF vê o SMF chegando da **porta 16098** em vez de 8805 ⇒ o conntrack reescreveu a
  porta de origem (colisão de tuplas UDP 8805↔8805 entre o tráfego via Service e o tráfego direto UPF→SMF).
  Efeito: heartbeats e modificações de sessão (PFCP type 52) rejeitados; SMF desassocia/reassocia em loop.
  Correção proposta: SMF endereçar o UPF sem DNAT (Service headless ou IP do pod). Paliativo: `./scripts/heal.sh`.
- Paliativo (restart UPF→SMF→gNB sim→UEs às 18:05) **não resolveu**: colisão voltou na hora (porta 1155). O UPF não
  inicia associação (sem `client` PFCP); só o SMF, pelo Service.
- **Correção preparada (open5GK3S decisão 31):** `upf.services.pfcp.clusterIP: None` (headless) + install apaga o
  Service antigo. Validado a seco. Falta `sudo ./install.sh`.
- Usuário quer, depois disso, o sistema **estável** para conectar UEs reais (demora: reiniciar celular etc.).
- **Correção do PFCP aplicada e validada (install 18:08 UTC):** Service `open5gs-upf-pfcp` agora headless (`None`);
  SMF↔UPF associados pelos IPs dos pods; **0** `Cannot find PFCP-Node` e 0 de-associações em 3 min; gNB real
  reconectou sozinho; SIM `…0022` registrou. 16/16 pods.
- `NETWORK_NAME="InnovaSplit"` (commit `fb84965`) — entra no próximo `install.sh --deploy-only`.
- 18:14 UTC: usuário rodou `install.sh --deploy-only` → AMF com `network_name: InnovaSplit`; PFCP sem erros; 16/16.
- A partir daqui: **fase de testes com UEs reais**. Assistente só observa (vigia só-leitura nas duas máquinas).

### 2026-10-21 — UEs reais funcionando + início do painel de logs/métricas
- Usuário confirmou: celular navegando pelo core do torre2, **speed test OK**.
- Pedido: site local no servidor com abas de logs (gNB, core, tudo) e métricas (UL/DL, BLER…), **parte do core**
  (mesma pasta/documentação do open5GK3S).
- Levantamento (só leitura):
  - gNB: `metricas.yml` = `metrics.enable_json: true` + `remote_control` em **0.0.0.0:8001** (WebSocket). Inscrição:
    enviar `{"cmd":"metrics_subscribe"}`. Do torre2 (10.10.0.2 → 10.10.0.1:8001) funciona sem mudar o innovaserver:
    ~2 msgs/s com `timestamp`, `du`, `cells[].cell_metrics` e `cells[].ue_list[]` (por UE: dl/ul_brate, dl/ul_mcs,
    cqi, dl_ri, dl/ul_nof_ok/nok → BLER, pusch_snr_db, pusch_rsrp_db, ta_ns, bsr, rnti, pci…). Havia 2 UEs.
  - Grafana do usuário no innovaserver: telegraf (`ws_adapter.py`) → InfluxDB 3 → Grafana (porta 3300).
  - Log do gNB: `/tmp/gnb.log` no innovaserver (precisa de algo lá para chegar ao torre2).
  - Open5GS no torre2: métricas Prometheus **desligadas** (`metrics.enabled: false` nos charts); ligar = reinício do core.

### 2026-10-21 ~18:15–18:25 UTC — testes com pessoas na rede (não derrubar)
- …0040: `internet` slice 1 → 10.45.0.3 OK. …0001: `internet2` slice 2 → 10.46.0.2 OK (pede `ims` em loop; recusado no
  SMF, ruído). …0022: registra, mas a sessão é recusada no **AMF** (`DNN Not Supported OR Not Subscribed in the Slice`,
  gmm-handler.c:1361 do 2.7.5 = par (S-NSSAI, DNN) pedido não está na assinatura). Mesmas slices do …0001 ⇒ problema
  provável no APN/slice configurado no celular. Opções: ajustar APN no celular; captura `tcpdump -i enp5s0f1 sctp`
  no torre2 para ver o DNN (NAS sem cifra por padrão); ou acrescentar o DNN pedido na WebUI.
- GNSS do innovaserver oscilando (holdover 15:20:49, volta ~15:22, perde 15:24 hora local). XDP fora; ~1850 bad/min.
- Usuário: o …0022 é um **modem Telit FN990** (não celular). GNSS: "pode dar uma esquecida" por ora (vigia não alerta mais).

### 2026-10-21 ~18:35–18:42 UTC — testes de estresse (UDP DL a partir do UPF; iperf/iperf3 já existem na imagem do UPF)
- Modem Telit FN990 (…0022): `AT+CGDCONT` cid1 internet / cid2 ims / cid3 internet2; `AT+C5GNSSAI` = 01.111111:02.222222;
  cid1 ativo ⇒ sessão `internet` 10.45.0.4 desde 18:31:55. Avisos a cada 16 s = cid2 `ims` (não assinado) — inofensivo.
- 10.46.0.2 (…0001, rnti 18099), UDP DL: 50M e 150M entregues integralmente; teto ~**300–350 Mbit/s** (oferta 500/800M
  descartada); DL BLER ~1 %, MCS 18–24, CQI 12–15, RI 4. **UL BLER 25–45 % com SNR ~31 dB e UL MCS 27** (suspeito:
  adaptação de enlace de UL agressiva — investigar).
- 10.45.0.3 (…0040): o UE fez novo registro com SUCI às 18:36:34 (OLD UE Context Release/Implicit NG release), a sessão
  foi removida e ele **não voltou** (sem Registration complete) ⇒ iperf3 sem resposta. Coincidiu com o 1º teste TCP
  (abortado). Gravador de métricas: `/tmp/ranlog.py` no torre2 (lê o WebSocket do gNB). Lição: rodar em segundo plano
  com `setsid nohup … < /dev/null`, senão prende a sessão SSH.

### 2026-10-21 ~18:45 UTC — investigação do BLER de UL (só leitura)
- Config do gNB (SLPIT_CORE/SLICE): **sem seção `pusch`** ⇒ padrões do OCUDU: `mcs_table qam256`, `max_ue_mcs 28`,
  OLLA `target_bler 0.01`, `snr_inc_step 0.001`, `max_snr_offset 5`, **`enable_cl_loop_pw_control 0`** (sem controle
  de potência em malha fechada), `max_rank 4`. Fronthaul: BFP 9 bits UL/DL, `iq_scaling 5.0`.
- **Slicing limita PRBs:** slice 1 min 50 / máx 70 %; slice 2 min 30 / **máx 50 %** ⇒ explica o teto de DL ~300–350
  Mbit/s do 10.46.0.2 (slice 2).
- Amostra passiva 20 s: rnti 18334 BLER UL 0,9 % (MCS 27, SNR 32 dB, RSRP −17,8 dB); rnti 18482 BLER 18,5 %
  (MCS 7–27, SNR 28,6 dB, RSRP −45,5 dB). RSRP ~27 dB diferente mas SNR só ~3 dB ⇒ **SNR teto ~30–32 dB limitado
  pela implementação** (compressão/EVM), não pelo ruído. MCS 27 (256QAM) fica no limite ⇒ BLER oscila.
  No teste de DL, o UL do 18099 era quase só UCI/ACK (poucas transmissões, % instável).
- Sessões: …0022 também ativou `internet2` 10.46.0.3 (cid3) às 18:39.
- Experimentos propostos (mudam o gNB ⇒ janela sem usuários + permissão): `pusch.max_ue_mcs` 24–25 ou `mcs_table qam64`;
  `enable_cl_loop_pw_control: true` + `target_sinr`; teste de UL com iperf3 -R antes/depois.

### 2026-10-21 ~19:10 UTC — painel "Innova Center Dashboard" pronto (não instalado)
- Nome e cores pedidos pelo usuário: **Innova Center Dashboard**, branco/azul/cinza; o verde do Centro Innova
  (#91cf50, tirado da capa da notícia da Unisinos) só como detalhe (linha da barra e ícone). LinkedIn exige login.
- **Tudo roda na máquina do core** (pedido do usuário: o OCUDU precisa da CPU). Métricas do gNB puxadas do WebSocket
  `ws://10.10.0.1:8001`; log do gNB por SSH (`tail -F /tmp/gnb.log`) por uma unit no torre2 (`open5gs-k3s-gnb-log`).
- Commit `be8c829` (branch `ocudu`, decisão 32): `dashboard/app` (server.py stdlib + página uPlot), `dashboard/agent`,
  `install.sh --dashboard-only` (só o painel; values do core idênticos ⇒ não reinicia o core), `docs/dashboard.md`.
- Testado fora do cluster no torre2 (127.0.0.1:18080 via `kubectl proxy`, só leitura) + `kubectl apply --dry-run=server`
  dos 7 objetos + pod do SMF alcança 10.10.0.1:8001 e a API. Fotos das abas conferidas com Chrome headless.
- Pendências: (1) usuário rodar `sudo ./install.sh --dashboard-only` quando puder; (2) chave SSH torre2 → innovaserver
  (o torre2 ainda não tem; mexe no innovaserver ⇒ permissão) e `GNB_LOG_SSH`; (3) `OPEN5GS_METRICS=true` numa janela
  livre (reinicia AMF/SMF/UPF/PCF).
- Observado: `/tmp/gnb.log` do OCUDU com **1,7 GB**, crescendo ~100 kB/s com o gNB parado (`all_level: info`, quase
  tudo `[PHY] [I]`) — risco de encher o disco do innovaserver.
- Observado nos logs do SMF: a cada 2 min, "No IP addresses available / All IP addresses in all subnets are occupied"
  para …0001 e …0040 (×49) — provavelmente a DNN `ims` sem pool no torre2 (pendência antiga). UEs ociosos são
  liberados pelo AMF (UE Context Release) e somem da célula — normal.
- Lição: `pkill -f`/`pgrep -f` com o padrão dentro do próprio comando mata a sessão SSH/shell ⇒ usar arquivo de PID
  ou script separado.
- **19:12 UTC: usuário rodou `sudo ./install.sh --dashboard-only`** ⇒ pod `dashboard` 1/1, http://191.4.205.17:30998,
  gNB conectado (3 UEs), core intacto (pods sem reinício). Instância de teste (127.0.0.1:18080) desligada.
- Grafana do OCUDU (innovaserver, `~/ocudu/docker/docker-compose.ui.yml`: telegraf + InfluxDB 3 + Grafana :3300):
  **parado** no momento (porta 3300 fechada; o usuário `innovaserver` não acessa o docker sem sudo). Painéis: OCUDU
  Metrics (UE: vazão, BLER, MCS, CQI, RI, SNR, RSRP, TA, PHR), CU-CP, NGAP, RRC, OFH (fronthaul) e Performance
  (CPU/memória/energia). O painel do torre2 cobre o equivalente ao "OCUDU Metrics" + logs + core; RRC/NGAP/OFH/
  Performance só se o gNB publicar essas camadas (hoje o WebSocket traz só `cells` e `du`).
- ~19:20 UTC: tráfego de demonstração (iperf2 UDP do UPF): 40+20 Mbit/s e depois 250+150 Mbit/s por 3 min ⇒ célula
  ~393 Mbit/s (…0001 = RNTI 18827 ~230 Mbit/s; modem …0022 = RNTI 18795 ~163 Mbit/s), BLER DL 2–3 %. RNTI 18821 com
  SNR ~6 dB e BLER DL ~22–33 % (UE com sinal fraco). Painel conferido pelo usuário.
- Commit `524ca57`: nomes curtos dos pods (AMF · acesso e mobilidade…), assinantes "SIM real" × "UERANSIM",
  `DASHBOARD_UE_NAMES` (…0022 = Modem Telit FN990). Aplicar com `sudo ./install.sh --dashboard-only` (só o pod do painel).
- ~19:30 UTC: usuário rodou `sudo ./install.sh --deploy-only` ⇒ painel com nomes curtos e SIM real × UERANSIM; **core
  sem reinício** (pods com 72 min). Log do gNB ainda vazio: `GNB_LOG_SSH` não definido (falta chave SSH torre2 → innovaserver).
- Commit `b60549f`: aba **Máquinas** (torre2 lido pelo pod via /proc,/sys,/ do host só leitura; innovaserver por SSH
  com o coletor enviado pela entrada padrão, nada instalado lá; unit `open5gs-k3s-hosts`). Testado com agente
  temporário do desktop: innovaserver Xeon w7-3565X, 32 núcleos, isolados 0–17, `gnb` ~220 % CPU, núcleo 9 em 100 %
  (DPDK), **CPU a 82 °C**, hugepages 1 GB 2/8 em uso, load ~7.
- Usuário: "pode parar de gerar tráfego" (parado) e **"pode parar com o open5gk3s-guia"** ⇒ não atualizar mais o
  artifact do guia (não apagar sem pedido).
- ~19:40 UTC: chave SSH torre2 → innovaserver criada pelo usuário; aba Máquinas com as duas máquinas online.
  `GNB_LOG_SSH="innovaserver@10.10.0.1"` gravado no config.env (commit `7d1ba71`); falta o usuário rodar `--dashboard-only`.
- Energia: projeto do usuário **MiEnergy_Lab_Meter** (medidor monofásico; `GET http://IP/Data` com ~70 grandezas
  `v_a`, `i_a`, `p_a`, `energia_ativa_total`…; original grava em InfluxDB 2.7 + Grafana). Integrado direto no painel
  (commit `8309993`, `ENERGY_METER_URL`/`ENERGY_METER_HOST`), testado com medidor falso. **192.168.68.105 não é
  alcançável** do desktop, torre2 nem innovaserver (todos em 191.4.204.0/23) ⇒ medidor em outra rede; aguardando o
  usuário dizer onde ele está ligado.
- Painel **v1.0** (commits `284709e` aba Configuração, `f70fb88` UEs por nome/IMSI/IP, `588ba85` versão+docs):
  - aba Configuração: senha de admin em `/opt/open5gs-k3s/state/dashboard-admin-password` (root), diff, validação
    do install, backups em `state/config-backups`; salvar não aplica (mostra o `sudo ./install.sh …`).
  - identificação RNTI → IMSI: só linhas `[RRC]/[CU-CP]/[NGAP]` do gNB (no `[SCHED]` o `ue=` é do DU!), AMF
    `[suci-…] … AMF_UE_NGAP_ID[n]`; agente busca vínculos nos últimos 300 MB quando o painel responde `ident: 0`.
  - medidor MiEnergy: 192.168.68.105 está atrás do **NAT do Wi‑Fi TP‑Link** (provável WAN 191.4.205.63, OUI
    1c:61:b4, não responde); o campus tem proxy transparente na porta 80 (qualquer IP "conecta"). Opções na doc.
  - `/tmp/gnb.log` no innovaserver chegou a **9 GB** (disco 6%).
  - Instalado pelo usuário 19:59 UTC e conferido (core intacto, gravação/validação no pod ok); tag `v1.0-dashboard`.
- Aba Core sem Prometheus (`ac51292`): `/host/proc` → processos `open5gs-*d`/`mongod` e `/proc/<upf>/net/dev`
  (ogstun* = N6 por DNN, tx = DL; net1 = N3). `UPF_DEVS` gerado de SLICES/tun_name.
- **Pegadinha**: o ConfigMap do painel passou de 256 KB ⇒ `kubectl apply` comum falha (anotação last-applied).
  Install usa `apply --server-side --force-conflicts --field-manager=open5gk3s` (`2b…` commit seguinte).
- Erros no core: celulares pedem DNN `ims` (no cadastro copiado do innovaserver) e o SMF não tem pool ⇒
  "No IP addresses available" a cada 10–15 s. Opções apresentadas ao usuário (pool ims / tirar ims / desligar VoLTE).
- Instalado e conferido (20:10 UTC); tag `v1.0-dashboard` movida para `568e542`.
- WebUI embutida (iframe) na aba Core (`35ec61a`; WebUI sem X-Frame-Options, cookie sem SameSite ⇒ funciona).
  Instalado pelo usuário e aprovado ("show de bola"). Fim do dia 2026-10-21: tudo commitado; tag `v1.0-dashboard`
  movida para `f4ccd48` (inclui WebUI embutida).
- Pendências: pool `ims` (janela), medidor (NAT Wi-Fi), temperatura innovaserver (IPMI `ipmitool sdr`), rotação gnb.log.
- Histórico persistente (`d6f54a2`, ainda NÃO instalado): `DASHBOARD_PERSIST=true` no config.env do torre2;
  SQLite `/var/lib/open5gs-k3s/dashboard/history.db` (dono 65534); séries nomeadas (`cell:`, `ue:pci:rnti` com meta
  IMSI, `du`, `host:`, `core:cpu`, `core:upf`, `prom:`, `energy`), blocos 60 s zlib; eventos com UNIQUE(ts,src,msg)
  (replay dos logs não duplica); experimentos com config mascarado. Teste: ~4 KB/s com blocos de 15 s.
  Botão "● Gravar experimento" no header (pedido do usuário: ao lado do botão de tema).
- Histórico instalado e gravando (20:52 UTC; pod sem erros, core intacto). VERSION 1.1, tag `v1.1-dashboard`.

### 2026-10-22
- Reboot geral; IPs novos: torre2 191.4.205.117, innovaserver 191.4.205.122 (chaves conferidas). Painel: http://191.4.205.117:30998.
- Corrigido: agentes do painel usam o IP fixo do nó (c4a4a50). Falta o usuário rodar `sudo ./install.sh --dashboard-only`.
- Core recuperado à mão (pods sem net1; SMF e UEs com IPs antigos). Ping do UE ok.
- Decidido: core **não** sobe no boot; painel ganha iniciar/parar/reiniciar por NF, "iniciar core inteiro" em ordem,
  e (a confirmar) controle do gNB OCUDU no innovaserver escolhendo o arquivo de config.
- RU LiteOn no switch (gestão 10.101.131.59). Painel **v1.2** (commit 8b8c9ed): aba RU só leitura (agente
  `ru-agent.py`, unit `open5gs-k3s-ru`, comandos `show`), RU no histórico/experimentos. Falta o usuário: criar
  `/opt/open5gs-k3s/state/ru-credentials` e rodar `sudo ./install.sh --dashboard-only`. Depois: tag `v1.2-dashboard`?
- Estado da RU ao conectar: SYNCHRONIZED, RF/DPD Ready, DU notReady (gNB parado), ~9,8 W, ~56 °C.
- Painel **v1.3** (commit após 8b8c9ed): aba **Controle**. Core/UERANSIM por escala 0/1 e recriação de pod (Role
  `dashboard-control`), "Iniciar tudo" em ordem com conferência (fibra, PFCP, NG Setup) e recriação de pod sem net1.
  Boot: `CORE_AUTOSTART=false` ⇒ `heal.sh --boot-off` (tudo em 0 menos o painel). gNB OCUDU: serviço
  `ocudu-gnb@<yml>` + polkit (usuário innovaserver só nessas units), agente `open5gs-k3s-gnb-ctl` no torre2.
  Usuário escolheu: serviço roda direto da home; sem preparação (interface/DPDK/XDP `xdp_ptp_pad.o`/PTP manuais).
- Roteiro do usuário no innovaserver: binário `~/ocudu/build/apps/gnb/gnb`, `sudo taskset -c 0-17 ./gnb -c
  ../../../configs/<arquivo>.yml`; arquivo para este core: `gnb_ru_liteon_SLPIT_CORE.yml` (AMF 10.10.0.10). Os
  outros LiteOn apontam para o Open5GS local do innovaserver (127.0.0.5), que ainda roda lá (amfd/upfd ativos).
- 2026-10-22: v1.3 validado em uso (parar/iniciar core, gNB pelo serviço com RU "DU conectado" + N2, UERANSIM).
  Serviço do gNB usa `-c configs/<arquivo>.yml -c configs/metricas.yml` (métricas porta 8001; metricas.yml não é
  alterado). Tags `v1.2-dashboard` (8b8c9ed) e `v1.3-dashboard`. Ordem das abas: Visão geral, Controle, Rádio,
  Core, RU, Logs, Máquinas, Histórico, Configuração.
- 2026-10-22 (noite): tag **`v1.4-dashboard`** (7369814) = estado estável de referência: logotipo oficial Innova,
  abas reordenadas, serviço do gNB com metricas.yml, Controle validado em uso. "Salve esse momento" (pedido do usuário).
- Decidido: projeto completo vira **InnovaNetwork** (repo novo, a criar pelo usuário no GitHub + deploy key);
  Open5GK3S = só core + UERANSIM. Próximo: **free5GC** (mais novo) em namespace próprio, um core por vez, painel
  com "core ativo" (protótipo em torre2:~/painel-teste, porta 18080, espelho só leitura do painel oficial).
- innovaserver: preparação do host já é automática no boot (`ocudu-setup.service` → `/usr/local/sbin/setup-ocudu.sh`:
  interface, SR-IOV/vfio, XDP `ptp-trim.o` = mesmo código do xdp_ptp_pad.c, ocudu_performance, NAT). Corrigido
  (pelo usuário, sudo): o script não acrescenta mais ip_forward ao sysctl.conf a cada boot (agora em
  /etc/sysctl.d/99-ocudu-forward.conf; backups .bak). Open5GS antigo no innovaserver fica de propósito (testes).
- 2026-10-22: **free5GC fase 1** na branch `innovanetwork` (projeto completo; commit 8f6999c, pasta free5gc/): v4.3.0, manifestos próprios
  (render.py), N2 10.10.0.30 / N3 10.10.0.31 / gNB sim 10.10.0.32, pools 10.60/16 e 10.61/16, WebConsole :31000
  (admin/<redacted> padrão — trocar). Plano de controle validado no cluster; deixado em 0 réplicas. Falta: usuário rodar
  `sudo ./free5gc/setup-gtp5g.sh` (DKMS gtp5g v0.10.3; compilação testada ok no kernel 6.8.0-147). SIMs reais do
  Open5GS (cadastrados na WebUI, não no config.env): `./free5gc/install.sh --importar-open5gs` — o usuário roda
  (o assistente não copia chaves de SIM). torre2:~/InnovaNetwork (pasta renomeada de ~/open5GK3S em 2026-10-22) está na branch innovanetwork (= ocudu + free5gc; a branch "free5gc" foi renomeada).
- 2026-10-22: **InnovaNetwork = padrão OCUDU + Open5GS; free5GC é opcional** (free5gc/install.sh à parte).
  Pasta do projeto no torre2: **~/InnovaNetwork** (o painel monta a pasta: após renomear, rodar --dashboard-only).
- 2026-10-22: **free5GC validado ponta a ponta** no torre2: gtp5g 0.10.2 (UPF 4.3.0 exige 0.9.3 <= v < 0.10.3; a 0.10.3 é recusada), PFCP, NG Setup, sessão PDU 10.60.0.1 e ping (test.sh 6/6). Lição: com `set -o pipefail`, `cmd | grep -q` dá falso negativo (SIGPIPE) — usar `grep -q ... <<<"$(cmd)"`. Imagens free5GC 4.3.0 não têm curl nem sysctl.
- 2026-10-22: **free5GC com celulares reais** (OCUDU+LiteOn, gnb_ru_liteon_FREE5GC.yml, AMF .30): slice 1 ok com internet. Lições: (1) free5GC precisa imsVoPS=1 + DNN ims, senão o celular registra e sai da célula ~1 s depois (RLF/cause 21); (2) AMF free5GC sem S-NSSAI no pedido usa allowedNssai[0] -> celular com internet2 sem slice é recusado (usuário decidiu deixar assim); (3) OCUDU e UERANSIM não reconectam se o AMF reiniciar; (4) busca manual mostra nome antigo, NITZ só após registro. Diagnóstico de dados: kubectl debug com netshoot no pod do UPF (tcpdump em net1/upfgtp). gNB ficou no free5GC ao fim do teste.
