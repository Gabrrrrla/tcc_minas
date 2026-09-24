"""
RQ4 benchmark — RandomForest vs. GradientBoosting vs. LSTM on core_kpis.

Promised in TCC I cap. 4 ("Algoritmo de predicao"):
  "Como parte da avaliacao experimental, sera realizada uma comparacao com
   Gradient Boosting e redes LSTM, a fim de validar a escolha a posteriori."

Runs offline against a live Postgres instance (no Flask, no Ollama).
Reuses the exact same lag-window dataset construction as nwdaf/main.py so
the RF result here is directly comparable to the production model.

Usage (from repo root, with Postgres reachable):
    POSTGRES_HOST=localhost python agents/nwdaf/benchmark_rq4.py

Optional env overrides (same as nwdaf/main.py):
    NWDAF_N_LAGS          window size for lag features   (default 5)
    NWDAF_HISTORY_LIMIT   rows fetched per slice         (default 500)
    NWDAF_MIN_TRAINING_ROWS  minimum X/y pairs needed    (default 20)
    COLLECT_INTERVAL      nominal seconds between rows   (default 10)
    BENCHMARK_HORIZON_S   prediction horizon in seconds  (default 60)
    BENCHMARK_LSTM_EPOCHS LSTM training epochs           (default 30)
    BENCHMARK_LSTM_HIDDEN LSTM hidden units               (default 32)
    BENCHMARK_LSTM_LR     LSTM learning rate             (default 0.001)
    BENCHMARK_LSTM_BATCH  LSTM batch size                (default 16)
    BENCHMARK_OUT_DIR     output directory for JSONL     (default agents/benchmark/results)
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone

# make agents/ importable (shared db.py) whether run via Docker or directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
import torch.nn as nn
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from db import get_db_conn

# ---------------------------------------------------------------------------
# Config (mirrors nwdaf/main.py env vars where applicable)
# ---------------------------------------------------------------------------
N_LAGS        = int(os.getenv("NWDAF_N_LAGS", "5"))
HISTORY_LIMIT = int(os.getenv("NWDAF_HISTORY_LIMIT", "500"))
MIN_ROWS      = int(os.getenv("NWDAF_MIN_TRAINING_ROWS", "20"))
COLLECT_INTERVAL = float(os.getenv("COLLECT_INTERVAL", "10"))
HORIZON_S     = int(os.getenv("BENCHMARK_HORIZON_S", "60"))

LSTM_EPOCHS   = int(os.getenv("BENCHMARK_LSTM_EPOCHS", "30"))
LSTM_HIDDEN   = int(os.getenv("BENCHMARK_LSTM_HIDDEN", "32"))
LSTM_LR       = float(os.getenv("BENCHMARK_LSTM_LR", "0.001"))
LSTM_BATCH    = int(os.getenv("BENCHMARK_LSTM_BATCH", "16"))

_script_dir   = os.path.dirname(os.path.abspath(__file__))
_default_out  = os.path.join(_script_dir, "..", "benchmark", "results")
OUT_DIR       = os.getenv("BENCHMARK_OUT_DIR", _default_out)

SLICES = [1, 2]
TRAIN_RATIO = 0.80


# ---------------------------------------------------------------------------
# Data loading — same query + lag-window as nwdaf/main.py
# ---------------------------------------------------------------------------

def _fetch_history(sst: int) -> list[tuple]:
    """Ascending (collected_at, thp_dl_mbps) for a slice."""
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


def _build_dataset(sst: int) -> tuple[list, list, int] | None:
    """Returns (X, y, steps_ahead) or None if not enough data."""
    rows = _fetch_history(sst)
    if len(rows) < N_LAGS + MIN_ROWS:
        return None

    times  = [r[0] for r in rows]
    values = [float(r[1]) for r in rows]

    # same steps_ahead calculation as nwdaf/main.py
    deltas = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    avg_interval = (sum(deltas) / len(deltas)) if deltas else COLLECT_INTERVAL
    steps_ahead = max(1, round(HORIZON_S / avg_interval)) if avg_interval > 0 else 1

    X, y = [], []
    for i in range(N_LAGS, len(values) - steps_ahead):
        X.append(values[i - N_LAGS:i])
        y.append(values[i + steps_ahead])

    if len(X) < MIN_ROWS:
        return None

    return X, y, steps_ahead


def _chronological_split(X: list, y: list) -> tuple:
    """80/20 split preserving temporal order — no shuffle."""
    n = len(X)
    cut = int(n * TRAIN_RATIO)
    return X[:cut], y[:cut], X[cut:], y[cut:]


# ---------------------------------------------------------------------------
# LSTM model (PyTorch)
# ---------------------------------------------------------------------------

class _LSTMModel(nn.Module):
    def __init__(self, input_size: int, hidden_size: int):
        super().__init__()
        self.lstm   = nn.LSTM(input_size=1, hidden_size=hidden_size, batch_first=True)
        self.linear = nn.Linear(hidden_size, 1)

    def forward(self, x):
        # x: (batch, seq_len, 1)
        out, _ = self.lstm(x)
        return self.linear(out[:, -1, :]).squeeze(-1)


def _train_lstm(X_train: list, y_train: list) -> _LSTMModel:
    model = _LSTMModel(input_size=1, hidden_size=LSTM_HIDDEN)
    optimizer = torch.optim.Adam(model.parameters(), lr=LSTM_LR)
    loss_fn   = nn.MSELoss()

    X_t = torch.tensor(X_train, dtype=torch.float32).unsqueeze(-1)  # (n, seq, 1)
    y_t = torch.tensor(y_train, dtype=torch.float32)

    model.train()
    n = len(X_t)
    for _ in range(LSTM_EPOCHS):
        perm = torch.randperm(n)
        for start in range(0, n, LSTM_BATCH):
            idx   = perm[start:start + LSTM_BATCH]
            xb, yb = X_t[idx], y_t[idx]
            optimizer.zero_grad()
            loss_fn(model(xb), yb).backward()
            optimizer.step()

    return model


def _predict_lstm(model: _LSTMModel, X_test: list) -> list[float]:
    model.eval()
    with torch.no_grad():
        X_t = torch.tensor(X_test, dtype=torch.float32).unsqueeze(-1)
        return model(X_t).tolist()


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _rmse(y_true, y_pred) -> float:
    return math.sqrt(mean_squared_error(y_true, y_pred))


def _inference_time_ms(predict_fn) -> float:
    """Wall-clock time for a single predict call, in milliseconds."""
    t0 = time.perf_counter()
    predict_fn()
    return (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------
# Per-slice benchmark
# ---------------------------------------------------------------------------

def _run_slice(sst: int) -> list[dict] | None:
    dataset = _build_dataset(sst)
    if dataset is None:
        return None

    X, y, steps_ahead = dataset
    X_tr, y_tr, X_te, y_te = _chronological_split(X, y)

    results = []

    # --- RandomForest (same hyperparams as nwdaf/main.py) ---
    rf = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=0)
    rf.fit(X_tr, y_tr)
    rf_pred = rf.predict(X_te).tolist()
    rf_inf  = _inference_time_ms(lambda: rf.predict([X_te[0]]))
    results.append({
        "sst": sst,
        "model": "random_forest",
        "mae":  round(mean_absolute_error(y_te, rf_pred), 4),
        "rmse": round(_rmse(y_te, rf_pred), 4),
        "inference_time_ms": round(rf_inf, 3),
        "n_train": len(X_tr),
        "n_test":  len(X_te),
        "steps_ahead": steps_ahead,
    })

    # --- GradientBoosting ---
    gb = GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=0)
    gb.fit(X_tr, y_tr)
    gb_pred = gb.predict(X_te).tolist()
    gb_inf  = _inference_time_ms(lambda: gb.predict([X_te[0]]))
    results.append({
        "sst": sst,
        "model": "gradient_boosting",
        "mae":  round(mean_absolute_error(y_te, gb_pred), 4),
        "rmse": round(_rmse(y_te, gb_pred), 4),
        "inference_time_ms": round(gb_inf, 3),
        "n_train": len(X_tr),
        "n_test":  len(X_te),
        "steps_ahead": steps_ahead,
    })

    # --- LSTM ---
    lstm_model = _train_lstm(X_tr, y_tr)
    lstm_pred  = _predict_lstm(lstm_model, X_te)
    lstm_inf   = _inference_time_ms(
        lambda: _predict_lstm(lstm_model, [X_te[0]])
    )
    results.append({
        "sst": sst,
        "model": "lstm",
        "mae":  round(mean_absolute_error(y_te, lstm_pred), 4),
        "rmse": round(_rmse(y_te, lstm_pred), 4),
        "inference_time_ms": round(lstm_inf, 3),
        "n_train": len(X_tr),
        "n_test":  len(X_te),
        "steps_ahead": steps_ahead,
        "lstm_epochs": LSTM_EPOCHS,
        "lstm_hidden": LSTM_HIDDEN,
    })

    return results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_table(all_results: list[dict]) -> None:
    header = f"{'Model':<22} {'MAE':>8} {'RMSE':>8} {'Inf (ms)':>12} {'Train':>7} {'Test':>6}"
    sep    = "-" * len(header)
    for sst in SLICES:
        rows = [r for r in all_results if r["sst"] == sst]
        if not rows:
            print(f"\nSST={sst}: insufficient data\n")
            continue
        steps = rows[0]["steps_ahead"]
        print(f"\nSST={sst}  (horizon={HORIZON_S}s, steps_ahead={steps}, lags={N_LAGS})")
        print(sep)
        print(header)
        print(sep)
        for r in rows:
            print(
                f"{r['model']:<22} "
                f"{r['mae']:>8.4f} "
                f"{r['rmse']:>8.4f} "
                f"{r['inference_time_ms']:>12.3f} "
                f"{r['n_train']:>7} "
                f"{r['n_test']:>6}"
            )
        print(sep)


def _write_jsonl(all_results: list[dict], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, f"rq4_{ts}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[rq4] N_LAGS={N_LAGS}, HISTORY_LIMIT={HISTORY_LIMIT}, "
          f"HORIZON_S={HORIZON_S}s, TRAIN_RATIO={TRAIN_RATIO}")

    all_results: list[dict] = []
    for sst in SLICES:
        print(f"[rq4] training SST={sst} ...")
        rows = _run_slice(sst)
        if rows is None:
            print(f"[rq4] SST={sst}: not enough data in core_kpis "
                  f"(need at least {N_LAGS + MIN_ROWS} rows). "
                  "Start the collector and wait for it to populate the table.")
        else:
            all_results.extend(rows)
            print(f"[rq4] SST={sst}: done ({len(rows)} models trained)")

    if not all_results:
        print("[rq4] No results — exiting with error.")
        sys.exit(1)

    _print_table(all_results)

    path = _write_jsonl(all_results, OUT_DIR)
    print(f"\n[rq4] JSONL written to: {path}")


if __name__ == "__main__":
    main()
