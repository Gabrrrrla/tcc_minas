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

# make agents/ importable (shared db.py) whether run via Docker or `cd agents/nwdaf`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request
from sklearn.ensemble import RandomForestRegressor

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
# Rough per-slice capacity used to normalize predicted Mbps into a 0-1 load
# index. TODO: derive this from get_core_kpis / provisioned Slice-AMBR instead
# of a flat env constant once real PCF/SMF enforcement exists.
SLICE_CAPACITY_MBPS = float(os.getenv("NWDAF_SLICE_CAPACITY_MBPS", "100"))

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


def _predict_slice_load(sst: int, horizon_seconds: int) -> dict | None:
    """Train a RandomForestRegressor on lag windows of thp_dl_mbps and predict
    `horizon_seconds` ahead. Returns None when there isn't enough history yet
    (collector needs to have been running for a while)."""
    rows = _history(sst)
    if len(rows) < N_LAGS + MIN_TRAINING_ROWS:
        return None

    times  = [r[0] for r in rows]
    values = [float(r[1]) for r in rows]

    deltas = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    avg_interval = (sum(deltas) / len(deltas)) if deltas else DEFAULT_COLLECT_INTERVAL
    steps_ahead = max(1, round(horizon_seconds / avg_interval)) if avg_interval > 0 else 1

    X, y = [], []
    for i in range(N_LAGS, len(values) - steps_ahead):
        X.append(values[i - N_LAGS:i])
        y.append(values[i + steps_ahead])

    if len(X) < MIN_TRAINING_ROWS:
        return None

    model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=0)
    model.fit(X, y)

    predicted_thp = float(model.predict([values[-N_LAGS:]])[0])
    load = max(0.0, predicted_thp / SLICE_CAPACITY_MBPS)

    return {
        "predicted_load": round(min(load, 2.0), 3),  # can exceed 1.0 under overload
        "predicted_thp_mbps": round(predicted_thp, 2),
        "steps_ahead": steps_ahead,
        "samples_used": len(X),
        # heuristic: more training rows -> more confidence, capped
        "confidence": round(min(0.5 + 0.01 * len(X), 0.95), 2),
        "source": "random_forest",
    }


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
        result = _predict_slice_load(sst, horizon_seconds)
        if result is not None:
            return jsonify({"analytics_id": analytics_id, "sst": sst,
                             "horizon_seconds": horizon_seconds, **result})
        return jsonify(_mock_analytics(analytics_id, sst, horizon_seconds,
                                        reason="insufficient history in core_kpis"))

    # NF_LOAD / USER_DATA_CONGESTION / ABNORMAL_BEHAVIOUR: no model yet
    return jsonify(_mock_analytics(analytics_id, sst, horizon_seconds))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "agent": "nwdaf"})


if __name__ == "__main__":
    print(f"[nwdaf] listening on :{PORT}")
    app.run(host="0.0.0.0", port=PORT)
