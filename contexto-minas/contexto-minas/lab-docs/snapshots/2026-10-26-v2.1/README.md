# Snapshot 2026-10-26 — InnovaNetwork v2.1 (tudo funcionando)

Estado de referência: Open5GS **e** free5GC no k3s do torre2, OCUDU + LiteOn no innovaserver, celulares reais
com internet nos dois cores, troca de core pelo painel.

- Projeto: https://github.com/Miguelclunardi/InnovaNetwork, tag **`v2.1-innovanetwork`** (commit f79dbc5).
  O painel no ar estava um commit atrás (sem a correção do apagar experimento) até o próximo `--dashboard-only`.
- `innovaserver/`: configuração da máquina do gNB que **não** está no git do projeto (cópia fiel, só leitura):
  - `home/innovaserver/ocudu/configs/`: `.yml` do gNB por core + `metricas.yml`;
  - `etc/linuxptp/`, units `ts2phc`, `ptp4l-gm` (+ drop-ins `override.conf`, `xdp.conf`), `phc2sys`;
  - `usr/local/sbin/setup-ocudu.sh` + `ocudu-setup.service` (DPDK, VF, XDP no boot);
  - `ocudu-gnb@.service` (o serviço que o painel usa);
  - `xdp/`: fontes do XDP (`ptptrim.bpf.c` → `/usr/local/lib/ocudu/ptp-trim.o`; `xdp_ptp_pad.c` é a versão antiga).
- OCUDU: commit `6fe9ad0335` (ver `ocudu-commit.txt`), código sem alterações locais (só configs/docker).
- Ajustes feitos neste dia na máquina: `tx_timestamp_timeout 50` no `ptp4l.cfg` e o drop-in `xdp.conf`.

Para restaurar a máquina: copiar os arquivos de volta para os mesmos caminhos, `systemctl daemon-reload`.
