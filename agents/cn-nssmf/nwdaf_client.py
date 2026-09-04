"""
Minimal client for the NWDAF normative interfaces used by the CN-NSSMF.

Reference: 3GPP TS 23.288 (architecture) and TS 29.520 (stage 3).
Two service operations matter to MINAS:
  Nnwdaf_AnalyticsInfo       — synchronous request for an analytics report
  Nnwdaf_EventsSubscription  — subscribe to periodic analytics notifications

This is a skeleton: the functions return mock analytics so the ReAct loop can be
exercised end to end. Replace the bodies with real HTTP calls to the NWDAF (or to
the Random Forest service that stands in for the MTLF in Use Case 1).
"""

from __future__ import annotations

import os
import random

NWDAF_URL = os.getenv("NWDAF_URL", "http://nwdaf:8080")

# Analytics IDs from TS 23.288 §6 relevant to slice resource management
ANALYTICS_IDS = {
    "SLICE_LOAD_LEVEL",      # load level information for a network slice
    "NF_LOAD",               # load of a specific NF / NF set
    "USER_DATA_CONGESTION",  # congestion in the user plane
    "ABNORMAL_BEHAVIOUR",    # anomaly detection
}


def get_analytics(analytics_id: str, sst: int, horizon_seconds: int = 60) -> dict:
    """Nnwdaf_AnalyticsInfo — one-shot predictive analytics request.

    TODO: replace with a real GET to
    {NWDAF_URL}/nnwdaf-analyticsinfo/v1/analytics?event-id=<analytics_id>&...
    and map the response to the dict below.
    """
    if analytics_id not in ANALYTICS_IDS:
        return {"error": f"unknown analytics_id: {analytics_id}"}

    # --- skeleton mock -----------------------------------------------------
    predicted = round(random.uniform(0.40, 0.95), 3)
    return {
        "analytics_id": analytics_id,
        "sst": sst,
        "horizon_seconds": horizon_seconds,
        "predicted_load": predicted,   # normalized 0.0 – 1.0
        "confidence": 0.8,
        "source": "mock",              # -> "nwdaf" once wired
    }


def subscribe_events(analytics_id: str, sst: int, period_seconds: int = 10) -> dict:
    """Nnwdaf_EventsSubscription — register for periodic notifications.

    TODO: POST a subscription to
    {NWDAF_URL}/nnwdaf-eventssubscription/v1/subscriptions, keep the returned id,
    and expose a callback endpoint for the notifications.
    """
    return {
        "subscription_id": f"mock-sub-{analytics_id.lower()}-{sst}",
        "analytics_id": analytics_id,
        "sst": sst,
        "period_seconds": period_seconds,
        "status": "active",
        "source": "mock",
    }
