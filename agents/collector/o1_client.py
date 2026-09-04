"""
Esqueleto de um cliente para a interface O1 (NETCONF/YANG) em direção ao nó RAN.
A fonte ideal de KPIs da RAN. O srsRAN do lab tem medições de desempenho por meio da interface O1, conforme as especificações 3GPP TS 28.550 / TS 28.552: é possível obter diretamente RSRP, SINR, MCS, utilização de PRBs e throughput por UE e por S-NSSAI, em vez de utilizar o Prometheus / contadores do UPF como intermediários.
Bloqueado por: qual software está sendo executado no gNB do laboratório (srsRAN / OAI / Amarisoft) e se ele disponibiliza a interface O1.
"""

from __future__ import annotations

import os

O1_HOST = os.getenv("O1_HOST", "")  # NETCONF endpoint of the gNB / RAN OAM


def get_ran_pm(sst: int) -> dict:
    """Retorna um dicionário no formato `ran_kpis` para cada slice, a partir da árvore de gerenciamento de desempenho da RAN.
    Lança `NotImplementedError` até que a interface O1 esteja configurada e conectada.
    """
    raise NotImplementedError("O1 interface ainda não implementada")