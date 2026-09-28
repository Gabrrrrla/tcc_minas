"""
MINAS NWDAF — Network Data Analytics Function (analytics microservice, not an
LLM ReAct agent; see TCC I cap. 4 "Selecao do modelo LLM" and the roadmap in
HANDOVER-2026-09-04.md 3.1).

Serves predictive analytics to the CN-NSSMF (agents/cn-nssmf/nwdaf_client.py)
over a normative-flavoured HTTP surface standing in for Nnwdaf_AnalyticsInfo
(3GPP TS 23.288 / TS 29.520).

Only SLICE_LOAD_LEVEL is backed by a real model: a RandomForestRegressor
trained on-request on the slice's `core_kpis.thp_dl_mbps` history (populated
by agents/collector), predicting `horizon_seconds` ahead via a lag-window
supervised setup. NF_LOAD / USER_DATA_CONGESTION / ABNORMAL_BEHAVIOUR remain
mock — building those needs their own feature sets (NF metrics, per-user
congestion signals, anomaly baselines) that nothing in the schema captures
yet. The TCC also calls for comparing this Random Forest against Gradient
Boosting and LSTM (TCC I cap. 4) — that benchmark is evaluation work for the
dissertation, not runtime plumbing, and is not done here.

Transport : HTTP POST /analytics  (called by cn-nssmf's nwdaf_client.get_analytics)
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timezone

# make agents/ importable (shared db.py) whether run via Docker or `cd agents/nwdaf`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request
from sklearn.ensemble import RandomForestRegressor

from dataset import build_dataset
from db import get_db_conn

PORT = int(os.getenv("NWDAF_PORT", "8080"))

# Analytics IDs from TS 23.288 SS6 relevant to slice resource management
ANALYTICS_IDS = {
    "SLICE_LOAD_LEVEL",      # load level information for a network slice -- real model below
    "NF_LOAD",               # load of a specific NF / NF set -- mock
    "USER_DATA_CONGESTION",  # congestion in the user plane -- mock
    "ABNORMAL_BEHAVIOUR",    # anomaly detection -- mock
}

# Lag-window model config (overridable via env)
N_LAGS = int(os.getenv("NWDAF_N_LAGS", "5"))
HISTORY_LIMIT = int(os.getenv("NWDAF_HISTORY_LIMIT", "500"))
MIN_TRAINING_ROWS = int(os.getenv("NWDAF_MIN_TRAINING_ROWS", "5"))
DEFAULT_COLLECT_INTERVAL = float(os.getenv("COLLECT_INTERVAL", "10"))
# Capacity used to normalize predicted Mbps into a 0-1 load index. Defaults to
# the same radio model the RAN-NSSMF admits against (RAN_PRB_TOTAL x
# RAN_MBPS_PER_PRB = 51 x 0.40 = 20.4 Mbps for the whole cell) — the old flat
# 100 Mbps made 17 Mbps read as "load 0.17" on a cell that tops out at 20.4.
SLICE_CAPACITY_MBPS = float(os.getenv(
    "NWDAF_SLICE_CAPACITY_MBPS",
    int(os.getenv("RAN_PRB_TOTAL", "51")) * float(os.getenv("RAN_MBPS_PER_PRB", "0.40")),
))
# A prediction is only meaningful over a live series: if the newest sample is
# older than this (collector down / misconfigured), refuse instead of
# silently forecasting from stale history.
MAX_STALENESS_S = float(os.getenv("NWDAF_MAX_STALENESS_S", "120"))

app = Flask(__name__)


def _history(sst: int) -> list[tuple]:
    """Ascending (collected_at, thp_dl_mbps) history for a slice from core_kpis."""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT collected_at, thp_dl_mbps
              FROM core_kpis
             WHERE sst = %s AND thp_dl_mbps IS NOT NULL
          ORDER BY collected_at DESC
             LIMIT %s
            """,
            (sst, HISTORY_LIMIT),
        )
        rows = cur.fetchall()
    return list(reversed(rows))


def _predict_slice_load(sst: int, horizon_seconds: int) -> tuple[dict | None, str | None]:
    """Train a RandomForestRegressor on lag windows of thp_dl_mbps and predict
    `horizon_seconds` ahead. Returns (result, None), or (None, reason) when the
    history is too short or too old to predict from."""
    rows = _history(sst)
    if len(rows) < N_LAGS + MIN_TRAINING_ROWS:
        return None, "insufficient history in core_kpis"

    times  = [r[0] for r in rows]
    values = [float(r[1]) for r in rows]

    age_s = (datetime.now(timezone.utc) - times[-1]).total_seconds()
    if age_s > MAX_STALENESS_S:
        return None, f"stale history in core_kpis (newest sample {age_s:.0f}s old)"

    X, y, steps_ahead, _ = build_dataset(times, values, horizon_seconds, N_LAGS,
                                         DEFAULT_COLLECT_INTERVAL)
    if len(X) < MIN_TRAINING_ROWS:
        return None, "insufficient contiguous history in core_kpis"

    model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=0)
    model.fit(X, y)

    predicted_thp = float(model.predict([values[-N_LAGS:]])[0])
    load = max(0.0, predicted_thp / SLICE_CAPACITY_MBPS)

    return {
        "predicted_load": round(min(load, 2.0), 3),  # can exceed 1.0 under overload
        "predicted_thp_mbps": round(predicted_thp, 2),
        "capacity_mbps": round(SLICE_CAPACITY_MBPS, 2),
        "steps_ahead": steps_ahead,
        "samples_used": len(X),
        # heuristic: more training rows -> more confidence, capped
        "confidence": round(min(0.5 + 0.01 * len(X), 0.95), 2),
        "source": "random_forest",
    }, None


def _mock_analytics(analytics_id: str, sst: int, horizon_seconds: int, **extra) -> dict:
    return {
        "analytics_id": analytics_id,
        "sst": sst,
        "horizon_seconds": horizon_seconds,
        "predicted_load": round(random.uniform(0.40, 0.95), 3),
        "confidence": 0.5,
        "source": "mock",
        **extra,
    }


@app.post("/analytics")
def analytics_endpoint():
    """Nnwdaf_AnalyticsInfo-flavoured endpoint: one-shot analytics request."""
    body = request.get_json(force=True) or {}
    analytics_id = body.get("analytics_id")
    sst = body.get("sst")
    horizon_seconds = int(body.get("horizon_seconds", 60))

    if analytics_id not in ANALYTICS_IDS:
        return jsonify({"error": f"unknown analytics_id: {analytics_id}"}), 400
    if sst is None:
        return jsonify({"error": "sst is required"}), 400

    if analytics_id == "SLICE_LOAD_LEVEL":
        result, reason = _predict_slice_load(sst, horizon_seconds)
        if result is not None:
            return jsonify({"analytics_id": analytics_id, "sst": sst,
                             "horizon_seconds": horizon_seconds, **result})
        return jsonify(_mock_analytics(analytics_id, sst, horizon_seconds, reason=reason))

    # NF_LOAD / USER_DATA_CONGESTION / ABNORMAL_BEHAVIOUR: no model yet
    return jsonify(_mock_analytics(analytics_id, sst, horizon_seconds))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "nwdaf"})


if __name__ == "__main__":
    print(f"[nwdaf] listening on :{PORT}")
    app.run(host="0.0.0.0", port=PORT)
