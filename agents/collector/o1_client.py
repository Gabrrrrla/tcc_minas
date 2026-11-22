"""
Skeleton of a client for the O1 interface (NETCONF/YANG) towards the RAN node.
The ideal source of RAN KPIs. Through O1, following 3GPP TS 28.550 / TS 28.552, a gNB can report RSRP, SINR, MCS, PRB usage, and throughput per UE and per S-NSSAI directly, instead of going through Prometheus / UPF counters.
Not implemented: the laboratory gNB did not expose O1 to MINAS.
"""

from __future__ import annotations

import os

O1_HOST = os.getenv("O1_HOST", "")  # NETCONF endpoint of the gNB / RAN OAM


def get_ran_pm(sst: int) -> dict:
    """Returns a dict in the `ran_kpis` format for a slice, from the RAN performance management tree.
    Raises `NotImplementedError` until the O1 interface is configured and connected.
    """
    raise NotImplementedError("O1 interface not implemented yet")