"""
Lag-window dataset construction shared by the NWDAF service (main.py) and the
offline RQ4 benchmark (benchmark_rq4.py), so both train on exactly the same
(X, y) pairs.

X[k] = the N_LAGS consecutive samples ending at sample j
y[k] = the sample `steps_ahead` samples after j   (j + steps_ahead)

Two things the old inline versions got wrong:
  - the sampling interval was the MEAN gap between rows, so hours/days
    between collector sessions inflated it and steps_ahead collapsed to 1
    (a 60 s horizon became 10 s). The median is robust to those gaps.
  - the target was values[i + steps_ahead] with the last lag at i - 1, i.e.
    one step further than the horizon asked for (70 s instead of 60 s).
Windows that straddle a collector gap are also dropped now: their "previous"
samples are hours old, not the recent past the model is meant to learn from.
"""

from __future__ import annotations

import statistics
from datetime import datetime

# a gap longer than this many sampling intervals breaks a window
GAP_FACTOR = 3.0


def sampling_interval(times: list[datetime], default: float) -> float:
    deltas = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    return statistics.median(deltas) if deltas else default


def build_dataset(
    times: list[datetime],
    values: list[float],
    horizon_seconds: float,
    n_lags: int,
    default_interval: float,
) -> tuple[list[list[float]], list[float], int, float]:
    """Returns (X, y, steps_ahead, interval_seconds)."""
    interval = sampling_interval(times, default_interval)
    steps_ahead = max(1, round(horizon_seconds / interval)) if interval > 0 else 1
    max_gap = GAP_FACTOR * interval

    # contiguous[k] is True when sample k follows sample k-1 without a gap
    contiguous = [False] + [
        (times[k] - times[k - 1]).total_seconds() <= max_gap for k in range(1, len(times))
    ]

    X, y = [], []
    for j in range(n_lags - 1, len(values) - steps_ahead):
        start, target = j - n_lags + 1, j + steps_ahead
        if all(contiguous[start + 1: target + 1]):
            X.append(values[start: j + 1])
            y.append(values[target])
    return X, y, steps_ahead, interval
