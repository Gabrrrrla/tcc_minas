"""
Synthetic per-slice traffic with temporal structure.

The collector's plain `mock` source draws i.i.d. uniform noise, which has
nothing a predictor could learn: on it RandomForest, GradientBoosting and LSTM
all tie with "predict the mean" (RQ4 run of 2026-09-28). This generator gives
each slice a series that a short-horizon predictor CAN exploit, while staying
honest about being synthetic:

  thp(t) = diurnal(t) + ar1(t) + burst(t)          (Mbps, floored at 0.1)

  diurnal : raised cosine over 24 h in operator-local time (MINAS_TZ), peaking
            at the slice's busy hour — eMBB/streaming in the evening, URLLC in
            business hours
  ar1     : AR(1) noise, phi = 0.9 per 10 s (autocorrelated, like real load)
  burst   : Poisson-arriving plateaus (flash crowds / incidents), which is what
            Use Case 1 is meant to anticipate

Deterministic for a given seed and time grid, so backfills are reproducible.
The same instance feeds core_kpis and ran_kpis in one collector tick, and PRB
usage is derived from throughput with the RAN-NSSMF's own radio model
(RAN_MBPS_PER_PRB / RAN_PRB_TOTAL), so the two domains stay consistent.
"""

from __future__ import annotations

import math
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from timeutil import TZ  # noqa: E402

PRB_TOTAL = int(os.getenv("RAN_PRB_TOTAL", "51"))
MBPS_PER_PRB = float(os.getenv("RAN_MBPS_PER_PRB", "0.40"))

# per-slice shape; together the two peaks stay near the 20.4 Mbps cell, and
# bursts on top push it past — the contention UC1/graceful degradation is for
PROFILES = {
    1: {"base": 6.0, "amp": 8.0, "peak_hour": 20.5, "sigma": 0.8,
        "burst_per_hour": 1.0, "burst_mbps": 6.0, "burst_seconds": 300},
    2: {"base": 2.0, "amp": 2.5, "peak_hour": 14.0, "sigma": 0.3,
        "burst_per_hour": 2.0, "burst_mbps": 5.0, "burst_seconds": 180},
}
AR_PHI_PER_10S = 0.9


class SliceLoad:
    def __init__(self, sst: int, seed: int = 0):
        self.p = PROFILES.get(sst, PROFILES[1])
        self.rng = random.Random(seed * 1000 + sst)
        self.noise = 0.0
        self.burst_until: datetime | None = None
        self.last_t: datetime | None = None

    def diurnal(self, t: datetime) -> float:
        lt = t.astimezone(TZ)
        hour = lt.hour + lt.minute / 60 + lt.second / 3600
        shape = (1 + math.cos(2 * math.pi * (hour - self.p["peak_hour"]) / 24)) / 2
        return self.p["base"] + self.p["amp"] * shape

    def sample(self, t: datetime) -> float:
        dt = 10.0 if self.last_t is None else max((t - self.last_t).total_seconds(), 1e-3)
        self.last_t = t

        phi = AR_PHI_PER_10S ** (dt / 10.0)
        self.noise = phi * self.noise + self.p["sigma"] * math.sqrt(1 - phi * phi) * self.rng.gauss(0, 1)

        if self.burst_until is not None and t >= self.burst_until:
            self.burst_until = None
        if self.burst_until is None:
            p_start = 1 - math.exp(-self.p["burst_per_hour"] * dt / 3600)
            if self.rng.random() < p_start:
                self.burst_until = t + timedelta(seconds=self.p["burst_seconds"])
        burst = self.p["burst_mbps"] if self.burst_until is not None else 0.0

        return round(max(0.1, self.diurnal(t) + self.noise + burst), 3)


def prb_for(thp_mbps: float) -> int:
    return min(PRB_TOTAL, math.ceil(thp_mbps / MBPS_PER_PRB))


def core_row(sst: int, thp: float, rng: random.Random) -> dict:
    ues = max(1, round(thp / 4))  # ~4 Mbps per active UE
    return {"ues_registered": ues, "pdu_sessions": ues,
            "thp_dl_mbps": thp, "thp_ul_mbps": round(thp * 0.3, 3)}


def ran_row(sst: int, thp: float, rng: random.Random) -> dict:
    return {
        "ue_id": f"synthetic-sst{sst}",
        "rsrp_dbm": round(rng.uniform(-100, -80), 1),
        "sinr_db": round(rng.uniform(10, 22), 1),
        "mcs_dl": rng.randint(12, 22),
        "mcs_ul": rng.randint(8, 16),
        "prb_used_dl": prb_for(thp),
        "prb_used_ul": prb_for(thp * 0.3),
        "thp_dl_mbps": thp,
        "thp_ul_mbps": round(thp * 0.3, 3),
    }
