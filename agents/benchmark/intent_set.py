"""
agents/benchmark/intent_set.py
-------------------------------
Ground-truth workload for the systematic benchmark (TCC-II-DRAFT.tex Section
5.3, "Workload: Intent Set").

This module only defines the workload. It does not run anything: no HTTP
calls, no LLM, no DB access. agents/benchmark/run_benchmark.py POSTs each
entry's `text` to the orchestrator's /intent endpoint n times per
experimental condition and compares the outcome against the fields below to
compute the metrics of Section 5.4.

Stratification (Section 5.3) — three axes, 2x2x2, two intents per cell:
  use_case  : "windowed_qos"                  — SST=1 (eMBB),  Use Case 2
              "predictive_resource_adjustment" — SST=2 (URLLC), Use Case 1
  phrasing  : "explicit"   — values and window stated directly
              "colloquial" — same information, relative/informal wording
  validity  : "valid"   — well-formed
              "invalid" — deliberately violates a guardrail rule in
                          agents/guardrails.py; correct handling is refusal
                          or self-correction, never a silent 'applied' with
                          the offending value in force

Dates and time zone (revised 2026-09-28)
  Window dates are RELATIVE to the day the benchmark runs (today + 3..6 days,
  in MINAS_TZ), not fixed: fixed dates would pass, and the record_intent
  guardrail now rejects windows that already ended. Expected windows are
  operator-local wall-clock times with their UTC offset (the orchestrator is
  told the current date/time/zone and naive timestamps are read in MINAS_TZ),
  not UTC as before.

Windowed intents are SCHEDULED, not applied (revised 2026-09-28)
  A window that starts in the future is recorded as 'scheduled' and the
  scheduler applies it at window_start. So a correct valid windowed_qos run
  now calls record_intent ONLY; calling cn_nssmf_apply_qos /
  ran_nssmf_apply_resources for it is a protocol error (blocked
  deterministically by the orchestrator, but still counted against
  tool-selection accuracy). The activation/reversion path itself is measured
  separately by run_window_probes.py (short windows starting minutes ahead).

Only two invalid archetypes are used, both directly triggerable from intent
text against the SAME guardrail that record_intent (agents/guardrails.py,
_validate_record_intent) actually checks:
  - unsupported SST (must be in {1, 2})
  - non-positive target throughput, or a reversed enforcement window
    (window_end before window_start, stated as the same day so it cannot be
    read as an overnight window)
MBR below GBR is NOT representable here: record_intent has no MBR/GBR fields;
that constraint is only checked on CN-NSSMF's configure_qos, whose values
CN-NSSMF's own ReAct loop decides.

Fields per entry, and which Section 5.4 metric each feeds:
  id                        unique slug
  text                      the exact string to POST as {"intent": text}
  use_case, phrasing, validity   the three stratification axes above
  expected_sst              ground truth for extraction accuracy (RQ1). For
                             an unsupported-SST case, the OFFENDING value.
  expected_target_thp_mbps  ground truth for extraction accuracy; may be None
  expected_window           {"start": iso, "end": iso} (with offset) or None.
                             windowed_qos entries always carry one;
                             predictive_resource_adjustment never does.
  expected_tools_required   tool names as the orchestrator's trace names them
                             that must appear. Always includes "record_intent".
  expected_tools_forbidden  tool names that must NOT appear.
  expected_outcome          "scheduled" (valid windowed_qos), "applied" (valid
                             predictive_resource_adjustment — run_benchmark
                             accepts 'degraded' when the RAN reports the target
                             infeasible), or "refused_or_corrected" (invalid).
                             MINAS has no dedicated "refused" state, so an
                             invalid run is correct unless an intent reaches
                             'applied'/'degraded'/'scheduled' with the
                             offending value (threat to validity, Section 5.6).
  invalid_reason            short string, only set for invalid entries.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from timeutil import TZ, now  # noqa: E402

_TODAY = now().date()
# four distinct future days, far enough ahead to stay 'scheduled' for the
# whole run (a run takes hours, not days)
D1, D2, D3, D4 = (_TODAY + timedelta(days=k) for k in (3, 4, 5, 6))


def _w(day, start: str, end: str) -> dict:
    """Operator-local window on `day` as ISO-8601 with offset."""
    def iso(hhmm: str) -> str:
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).isoformat()
    return {"start": iso(start), "end": iso(end)}


_APPLY = ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"]

INTENTS: list[dict] = [
    # ------------------------------------------------------------------
    # windowed_qos (SST=1, eMBB) x explicit x valid
    # ------------------------------------------------------------------
    {
        "id": "wq-explicit-valid-1",
        "text": f"Guarantee 25 Mbps downlink on the eMBB slice (SST=1) from 18:00 to 22:00 on {D1}.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 25.0,
        "expected_window": _w(D1, "18:00", "22:00"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "scheduled",
        "invalid_reason": None,
    },
    {
        "id": "wq-explicit-valid-2",
        "text": f"Set a 40 Mbps throughput floor for SST=1 between 08:00 and 09:30 on {D2}.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 40.0,
        "expected_window": _w(D2, "08:00", "09:30"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "scheduled",
        "invalid_reason": None,
    },

    # ------------------------------------------------------------------
    # windowed_qos x colloquial x valid
    # ------------------------------------------------------------------
    {
        "id": "wq-colloquial-valid-1",
        "text": f"The streaming slice needs about 20 Mbps for the evening rush, from 18:00 to 22:00 on {D3}.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 20.0,
        "expected_window": _w(D3, "18:00", "22:00"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "scheduled",
        "invalid_reason": None,
    },
    {
        "id": "wq-colloquial-valid-2",
        "text": f"Streaming's been laggy in the mornings — keep it at a comfortable 35 Mbps between 8 and 9:30 on {D4}.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 35.0,
        "expected_window": _w(D4, "08:00", "09:30"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "scheduled",
        "invalid_reason": None,
    },

    # ------------------------------------------------------------------
    # windowed_qos x explicit x invalid
    # ------------------------------------------------------------------
    {
        "id": "wq-explicit-invalid-1",
        "text": f"Guarantee 20 Mbps on SST=1 from 22:00 to 18:00 on {D1}, both times on that same day.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "invalid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 20.0,
        "expected_window": _w(D1, "22:00", "18:00"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "window_end before window_start (explicitly same day)",
    },
    {
        "id": "wq-explicit-invalid-2",
        "text": f"Guarantee 20 Mbps on slice SST=5 from 18:00 to 22:00 on {D1}.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "invalid",
        "expected_sst": 5,
        "expected_target_thp_mbps": 20.0,
        "expected_window": _w(D1, "18:00", "22:00"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "unsupported SST (only 1 and 2 are provisioned)",
    },

    # ------------------------------------------------------------------
    # windowed_qos x colloquial x invalid
    # ------------------------------------------------------------------
    {
        "id": "wq-colloquial-invalid-1",
        "text": f"Just kill the guarantee on the streaming slice, set it to 0 Mbps, from 18:00 to 22:00 on {D1}.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "invalid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 0.0,
        "expected_window": _w(D1, "18:00", "22:00"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "non-positive target_thp_mbps",
    },
    {
        "id": "wq-colloquial-invalid-2",
        "text": f"Keep the streaming slice boosted to about 20 Mbps from 9 PM to 6 PM on {D1}, same day.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "invalid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 20.0,
        "expected_window": _w(D1, "21:00", "18:00"),
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "window_end before window_start (explicitly same day)",
    },

    # ------------------------------------------------------------------
    # predictive_resource_adjustment (SST=2, URLLC) x explicit x valid
    # ------------------------------------------------------------------
    {
        "id": "pra-explicit-valid-1",
        "text": "Proactively adjust resources for the URLLC slice (SST=2) to sustain 10 Mbps based on predicted load.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 2,
        "expected_target_thp_mbps": 10.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent", *_APPLY],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },
    {
        # was "... peak in the next hour": that reads as a 1-hour window, which
        # the ground truth (no window) then scored as an extraction error
        "id": "pra-explicit-valid-2",
        "text": "Reserve capacity on SST=2 for a predicted 15 Mbps peak in demand.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 2,
        "expected_target_thp_mbps": 15.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent", *_APPLY],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },

    # ------------------------------------------------------------------
    # predictive_resource_adjustment x colloquial x valid
    # ------------------------------------------------------------------
    {
        "id": "pra-colloquial-valid-1",
        "text": "The mission-critical slice might spike soon — make sure it can handle around 12 Mbps.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "colloquial",
        "validity": "valid",
        "expected_sst": 2,
        "expected_target_thp_mbps": 12.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent", *_APPLY],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },
    {
        "id": "pra-colloquial-valid-2",
        "text": "Keep the low-latency slice ready for a bump to roughly 18 Mbps if the load prediction calls for it.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "colloquial",
        "validity": "valid",
        "expected_sst": 2,
        "expected_target_thp_mbps": 18.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent", *_APPLY],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },

    # ------------------------------------------------------------------
    # predictive_resource_adjustment x explicit x invalid
    # ------------------------------------------------------------------
    {
        "id": "pra-explicit-invalid-1",
        "text": "Proactively adjust resources for slice SST=6 to sustain 10 Mbps based on predicted load.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "explicit",
        "validity": "invalid",
        "expected_sst": 6,
        "expected_target_thp_mbps": 10.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "unsupported SST (only 1 and 2 are provisioned)",
    },
    {
        "id": "pra-explicit-invalid-2",
        "text": "Reserve capacity on SST=2 for a predicted -5 Mbps peak in demand.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "explicit",
        "validity": "invalid",
        "expected_sst": 2,
        "expected_target_thp_mbps": -5.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "non-positive target_thp_mbps",
    },

    # ------------------------------------------------------------------
    # predictive_resource_adjustment x colloquial x invalid
    # ------------------------------------------------------------------
    {
        "id": "pra-colloquial-invalid-1",
        "text": "The IoT slice might need more headroom soon — get it ready.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "colloquial",
        "validity": "invalid",
        "expected_sst": 3,
        "expected_target_thp_mbps": None,
        "expected_window": None,
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "unsupported SST (MIoT=3 is a standardized SST but not provisioned in MINAS)",
    },
    {
        "id": "pra-colloquial-invalid-2",
        "text": "Basically turn off any guarantee on the critical slice — set it to 0 Mbps proactively.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "colloquial",
        "validity": "invalid",
        "expected_sst": 2,
        "expected_target_thp_mbps": 0.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": _APPLY,
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "non-positive target_thp_mbps",
    },
]


def _self_check() -> None:
    """Basic shape/consistency check, run at import time — catches typos in
    this table before a benchmark run wastes LLM calls on a bad entry."""
    seen_ids: set[str] = set()
    required_keys = {
        "id", "text", "use_case", "phrasing", "validity",
        "expected_sst", "expected_target_thp_mbps", "expected_window",
        "expected_tools_required", "expected_tools_forbidden",
        "expected_outcome", "invalid_reason",
    }
    for entry in INTENTS:
        missing = required_keys - entry.keys()
        assert not missing, f"{entry.get('id')}: missing keys {missing}"
        assert entry["id"] not in seen_ids, f"duplicate id: {entry['id']}"
        seen_ids.add(entry["id"])
        assert entry["use_case"] in ("windowed_qos", "predictive_resource_adjustment")
        assert entry["phrasing"] in ("explicit", "colloquial")
        assert entry["validity"] in ("valid", "invalid")
        assert entry["expected_outcome"] in ("applied", "scheduled", "refused_or_corrected")
        assert (entry["validity"] == "invalid") == (entry["expected_outcome"] == "refused_or_corrected")
        assert (entry["invalid_reason"] is not None) == (entry["validity"] == "invalid")
        if entry["use_case"] == "predictive_resource_adjustment":
            assert entry["expected_window"] is None, f"{entry['id']}: Use Case 1 never carries a window"
        if entry["expected_outcome"] == "scheduled":
            assert entry["use_case"] == "windowed_qos" and entry["expected_window"] is not None
            assert not set(entry["expected_tools_required"]) & set(_APPLY), entry["id"]
        assert "record_intent" in entry["expected_tools_required"]


_self_check()


if __name__ == "__main__":
    from collections import Counter

    print(f"{len(INTENTS)} intents (dates relative to {_TODAY}: {D1}..{D4})")
    for axis in ("use_case", "phrasing", "validity", "expected_outcome"):
        print(axis, dict(Counter(e[axis] for e in INTENTS)))
