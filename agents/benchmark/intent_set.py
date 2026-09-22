"""
agents/benchmark/intent_set.py
-------------------------------
Ground-truth workload for the systematic benchmark (TCC-II-DRAFT.tex Section
5.3, "Workload: Intent Set") — item 2 of the benchmark critical path, after
the RQ3 ablation switches (RAG_ENABLED / GUARDRAILS_ENABLED, already in
agents/guardrails.py and agents/orchestrator/main.py).

This module only defines the workload. It does not run anything: no HTTP
calls, no LLM, no DB access. The runner script (critical-path item 3, not
built yet) is expected to POST each entry's `text` to the orchestrator's
/intent endpoint n times per experimental condition and compare the outcome
against the fields below to compute the metrics of Section 5.4.

Stratification (Section 5.3) — three axes, 2x2x2, two intents per cell:
  use_case  : "windowed_qos"                  — SST=1 (eMBB),  Use Case 2
              "predictive_resource_adjustment" — SST=2 (URLLC), Use Case 1
  phrasing  : "explicit"   — values and window stated directly
              "colloquial" — same information, relative/informal wording
  validity  : "valid"   — well-formed, should reach 'applied'
              "invalid" — deliberately violates a guardrail rule in
                          agents/guardrails.py; correct handling is refusal
                          or self-correction, never a silent 'applied' with
                          the offending value in force

Only two invalid archetypes are used, both directly triggerable from intent
text against the SAME guardrail that record_intent (agents/guardrails.py,
_validate_record_intent) actually checks:
  - unsupported SST (must be in {1, 2})
  - non-positive target throughput, or a reversed enforcement window
    (window_end before window_start)
A third documented invalid case from the TCC II text, MBR below GBR, is NOT
representable as a record_intent-level entry: record_intent has no MBR/GBR
fields, that constraint is only checked later on CN-NSSMF's own
configure_qos call (agents/guardrails.py, _validate_configure_qos), whose
gbr/mbr values are decided by CN-NSSMF's own ReAct loop, not read verbatim
from the operator's text. That path was already exercised as a direct,
non-LLM unit check in the 22/09/2026 session (see memory), not as an
intent-set entry here.

Fields per entry, and which Section 5.4 metric each feeds:
  id                        unique slug
  text                      the exact string to POST as {"intent": text}
  use_case, phrasing, validity   the three stratification axes above
  expected_sst              ground truth for extraction accuracy (RQ1).
                             For an unsupported-SST case, this is the
                             OFFENDING value stated in the text — the value
                             a correct system must refuse, not apply.
  expected_target_thp_mbps  ground truth for extraction accuracy; may be
                             None (not every valid intent states a number)
  expected_window           {"start": iso, "end": iso} or None; ground truth
                             for extraction accuracy. windowed_qos entries
                             always carry one; predictive_resource_adjustment
                             entries never do (Use Case 1 has no window).
  expected_tools_required   tool names, as the orchestrator's own trace
                             names them (cn_nssmf_ / ran_nssmf_ prefix), that
                             must appear for a valid intent to count as
                             correctly handled — tool-selection accuracy
                             (RQ1/RQ2). Always includes "record_intent".
  expected_tools_forbidden  tool names that must NOT appear with the
                             offending parameter value committed — used only
                             for invalid entries, feeds guardrail rejection
                             rate (RQ3) together with the guardrail-logged
                             ablation output.
  expected_outcome          "applied" for valid entries; "refused_or_corrected"
                             for invalid ones. MINAS's intent lifecycle
                             (schema.sql / guardrails._VALID_INTENT_STATUS)
                             has no dedicated "refused" state, so a correct
                             run may EITHER never call record_intent with the
                             offending value (self-correction, most likely
                             with guardrails on) OR call it, have the
                             guardrail reject it (visible in the trace as
                             {"guardrail": true}), and then either retry with
                             a corrected value or leave the request unfulfilled
                             — both count as correct; only an intent that
                             reaches 'applied'/'degraded' with the offending
                             value in force counts as a failure. This
                             ambiguity is a known threat to validity (Section
                             5.6, "authored by the same person").
  invalid_reason            short string, only set for invalid entries.

Because LLM generation is stochastic (Section 5.3, "repetitions and
non-determinism"), the runner is expected to POST each entry n times per
condition — n is a runner-script concern, not part of this workload.
"""

from __future__ import annotations

INTENTS: list[dict] = [
    # ------------------------------------------------------------------
    # windowed_qos (SST=1, eMBB) x explicit x valid
    # ------------------------------------------------------------------
    {
        "id": "wq-explicit-valid-1",
        "text": "Guarantee 25 Mbps downlink on the eMBB slice (SST=1) from 18:00 to 22:00 on 2026-10-01.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 25.0,
        "expected_window": {"start": "2026-10-01T18:00:00Z", "end": "2026-10-01T22:00:00Z"},
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },
    {
        "id": "wq-explicit-valid-2",
        "text": "Set a 40 Mbps throughput floor for SST=1 between 08:00 and 09:30 on 2026-10-02.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 40.0,
        "expected_window": {"start": "2026-10-02T08:00:00Z", "end": "2026-10-02T09:30:00Z"},
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },

    # ------------------------------------------------------------------
    # windowed_qos x colloquial x valid
    # ------------------------------------------------------------------
    {
        "id": "wq-colloquial-valid-1",
        "text": "The streaming slice needs about 20 Mbps for the evening rush, from 18:00 to 22:00 on 2026-10-03.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 20.0,
        "expected_window": {"start": "2026-10-03T18:00:00Z", "end": "2026-10-03T22:00:00Z"},
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },
    {
        "id": "wq-colloquial-valid-2",
        "text": "Streaming's been laggy in the mornings — keep it at a comfortable 35 Mbps between 8 and 9:30 on 2026-10-04.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "valid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 35.0,
        "expected_window": {"start": "2026-10-04T08:00:00Z", "end": "2026-10-04T09:30:00Z"},
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },

    # ------------------------------------------------------------------
    # windowed_qos x explicit x invalid
    # ------------------------------------------------------------------
    {
        "id": "wq-explicit-invalid-1",
        "text": "Guarantee 20 Mbps on SST=1 from 22:00 to 18:00 on 2026-10-01.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "invalid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 20.0,
        "expected_window": {"start": "2026-10-01T22:00:00Z", "end": "2026-10-01T18:00:00Z"},
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "window_end before window_start",
    },
    {
        "id": "wq-explicit-invalid-2",
        "text": "Guarantee 20 Mbps on slice SST=5 from 18:00 to 22:00 on 2026-10-01.",
        "use_case": "windowed_qos",
        "phrasing": "explicit",
        "validity": "invalid",
        "expected_sst": 5,
        "expected_target_thp_mbps": 20.0,
        "expected_window": {"start": "2026-10-01T18:00:00Z", "end": "2026-10-01T22:00:00Z"},
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "unsupported SST (only 1 and 2 are provisioned)",
    },

    # ------------------------------------------------------------------
    # windowed_qos x colloquial x invalid
    # ------------------------------------------------------------------
    {
        "id": "wq-colloquial-invalid-1",
        "text": "Just kill the guarantee on the streaming slice, set it to 0 Mbps, from 18:00 to 22:00 on 2026-10-01.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "invalid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 0.0,
        "expected_window": {"start": "2026-10-01T18:00:00Z", "end": "2026-10-01T22:00:00Z"},
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "non-positive target_thp_mbps",
    },
    {
        "id": "wq-colloquial-invalid-2",
        "text": "Keep the streaming slice boosted to about 20 Mbps from 9 PM to 6 PM on 2026-10-01, same day.",
        "use_case": "windowed_qos",
        "phrasing": "colloquial",
        "validity": "invalid",
        "expected_sst": 1,
        "expected_target_thp_mbps": 20.0,
        "expected_window": {"start": "2026-10-01T21:00:00Z", "end": "2026-10-01T18:00:00Z"},
        "expected_tools_required": ["record_intent"],
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_tools_forbidden": [],
        "expected_outcome": "applied",
        "invalid_reason": None,
    },
    {
        "id": "pra-explicit-valid-2",
        "text": "Reserve capacity on SST=2 for a predicted 15 Mbps peak in the next hour.",
        "use_case": "predictive_resource_adjustment",
        "phrasing": "explicit",
        "validity": "valid",
        "expected_sst": 2,
        "expected_target_thp_mbps": 15.0,
        "expected_window": None,
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_required": ["record_intent", "cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
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
        "expected_tools_forbidden": ["cn_nssmf_apply_qos", "ran_nssmf_apply_resources"],
        "expected_outcome": "refused_or_corrected",
        "invalid_reason": "non-positive target_thp_mbps",
    },
]


def _self_check() -> None:
    """Basic shape/consistency check, run at import time — catches typos in
    this static table before a benchmark run wastes LLM calls on a bad entry."""
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
        assert entry["expected_outcome"] in ("applied", "refused_or_corrected")
        assert (entry["validity"] == "invalid") == (entry["expected_outcome"] == "refused_or_corrected")
        assert (entry["invalid_reason"] is not None) == (entry["validity"] == "invalid")
        if entry["use_case"] == "predictive_resource_adjustment":
            assert entry["expected_window"] is None, f"{entry['id']}: Use Case 1 never carries a window"
        assert "record_intent" in entry["expected_tools_required"]


_self_check()


if __name__ == "__main__":
    from collections import Counter

    print(f"{len(INTENTS)} intents")
    for axis in ("use_case", "phrasing", "validity"):
        print(axis, dict(Counter(e[axis] for e in INTENTS)))
