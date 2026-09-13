"""
agents/rag/corpus.py
---------------------
Curated grounding corpus for the orchestrator's RAG layer (P3).

This is a small, hand-written set of paraphrased summaries of 3GPP concepts
the MINAS agents already rely on (TS 23.501, TS 23.288, TS 28.312/28.533) —
NOT verbatim spec text, and not the full GSMA/telco-retrieve-chunks corpus
the orientador pointed at (see HANDOVER-2026-09-12.md: that dataset is a
1.05TB grid of 120 pre-built ChromaDB/BM25 indexes requiring the
`open-telco-rag` tool — out of scope for a first RAG pass). Treat these
chunks as a starting scaffold to unblock retrieval, not an authoritative
citation source; expand/replace with real spec excerpts (or swap in a slice
of the GSMA index) before relying on them for the TCC II write-up.

Where a chunk mirrors a citation already used elsewhere in the codebase
(agents/guardrails.py, agents/mcp_common.py), the section reference matches
on purpose, so the RAG context and the deterministic guardrails agree.
"""

from __future__ import annotations

CHUNKS: list[dict[str, str]] = [
    {
        "id": "snssai-structure",
        "source": "TS 23.501 §5.15",
        "text": (
            "A network slice is identified by an S-NSSAI (Single Network Slice "
            "Selection Assistance Information), composed of a mandatory SST "
            "(Slice/Service Type) and an optional SD (Slice Differentiator) "
            "used to distinguish multiple slices of the same SST."
        ),
    },
    {
        "id": "sst-standard-values",
        "source": "TS 23.501 §5.15.2 / Table 5.15.2.2-1",
        "text": (
            "Standardized SST values: 1 = eMBB (enhanced Mobile Broadband), "
            "2 = URLLC (Ultra-Reliable Low-Latency Communication), "
            "3 = MIoT (Massive IoT), 4 = V2X. MINAS currently operates SST=1 "
            "(streaming / eMBB) and SST=2 (mission-critical / URLLC)."
        ),
    },
    {
        "id": "network-slicing-concept",
        "source": "TS 23.501 §5.15",
        "text": (
            "Network slicing lets an operator run multiple logical networks "
            "as virtually independent business operations on a common "
            "physical infrastructure, each network slice instance (NSI) "
            "tuned to a specific set of QoS and isolation requirements."
        ),
    },
    {
        "id": "5qi-concept",
        "source": "TS 23.501 §5.7.4",
        "text": (
            "The 5QI (5G QoS Identifier) is a scalar that references a set of "
            "standardized QoS characteristics (priority, packet delay budget, "
            "packet error rate) applied to a QoS flow, analogous to the QCI "
            "used in 4G."
        ),
    },
    {
        "id": "5qi-range",
        "source": "TS 23.501 Table 5.7.4-1",
        "text": (
            "Standardized 5QI values run from 1 to 86. Low values are "
            "reserved for latency/priority-sensitive GBR traffic (e.g. 5QI=1 "
            "for conversational voice); 5QI=9 is the common Non-GBR "
            "best-effort default."
        ),
    },
    {
        "id": "gbr-vs-nongbr",
        "source": "TS 23.501 §5.7.3",
        "text": (
            "QoS flows are either GBR (Guaranteed Bit Rate — the network "
            "commits to a minimum bit rate) or Non-GBR (best-effort, no "
            "committed floor). GBR flows also carry an MBR (Maximum Bit "
            "Rate), which upper-bounds the flow above its guaranteed rate."
        ),
    },
    {
        "id": "gbr-mbr-relationship",
        "source": "TS 23.501 §5.7.2",
        "text": (
            "For a GBR QoS flow, the Maximum Bit Rate (MBR) must be greater "
            "than or equal to the Guaranteed Bit Rate (GBR) — the network "
            "never guarantees more than it is allowed to deliver at peak."
        ),
    },
    {
        "id": "pdu-session-concept",
        "source": "TS 23.501 §5.6.1",
        "text": (
            "A PDU Session provides PDU (Protocol Data Unit) connectivity "
            "between a UE and a data network, associated with a single "
            "S-NSSAI and DNN, and carrying one or more QoS flows."
        ),
    },
    {
        "id": "5gc-network-functions",
        "source": "TS 23.501 §6.2",
        "text": (
            "Core 5G network functions relevant to slice QoS control: AMF "
            "(Access and Mobility Management), SMF (Session Management, "
            "owns PDU session QoS rules), PCF (Policy Control, decides QoS "
            "policy), UPF (User Plane, enforces traffic handling)."
        ),
    },
    {
        "id": "npcf-nsmf-interfaces",
        "source": "TS 23.501 §4.2 / TS 29.512",
        "text": (
            "QoS is configured through the Npcf_SMPolicyControl service "
            "(PCF derives and updates PCC rules) and Nsmf_PDUSession service "
            "(SMF applies the resulting QoS rules to the PDU session) — the "
            "normative interfaces CN-NSSMF's configure_qos is meant to call."
        ),
    },
    {
        "id": "nssmf-hierarchy",
        "source": "TS 28.533 / TS 28.531",
        "text": (
            "3GPP's slice management framework layers a CSMF (Communication "
            "Service Management Function), an NSMF (Network Slice "
            "Management Function), and one NSSMF (Network Slice Subnet "
            "Management Function) per network subnet — MINAS's CN-NSSMF and "
            "RAN-NSSMF play the NSSMF role for the core and RAN subnets."
        ),
    },
    {
        "id": "nwdaf-role",
        "source": "TS 23.288 §4",
        "text": (
            "The NWDAF (Network Data Analytics Function) collects data from "
            "network functions and OAM, and exposes analytics and "
            "predictions to consumers (like CN-NSSMF) through standardized "
            "Analytics IDs."
        ),
    },
    {
        "id": "nnwdaf-analyticsinfo",
        "source": "TS 23.288 §7.2 / TS 29.520",
        "text": (
            "Nnwdaf_AnalyticsInfo is the 3GPP service-based interface a "
            "consumer NF uses to request analytics from the NWDAF — either a "
            "one-off fetch or a subscription to periodic updates for a given "
            "Analytics ID and target (e.g. a network slice)."
        ),
    },
    {
        "id": "slice-load-level-analytics",
        "source": "TS 23.288 §6.4",
        "text": (
            "Slice Load Level Analytics is a standardized NWDAF Analytics ID "
            "that reports current and predicted load (resource usage, "
            "spare capacity) for a target network slice — the analytics_id "
            "MINAS's NWDAF implements with a real RandomForest predictor."
        ),
    },
    {
        "id": "other-analytics-ids",
        "source": "TS 23.288 §6",
        "text": (
            "Other standardized NWDAF Analytics IDs include NF Load "
            "(per-network-function load), User Data Congestion (per-area "
            "congestion), and Abnormal Behaviour (anomaly detection on UE or "
            "NF behaviour) — MINAS mocks these while SLICE_LOAD_LEVEL is real."
        ),
    },
    {
        "id": "ibn-concept",
        "source": "TS 28.312 §4",
        "text": (
            "Intent-Based Network Management (IBN) lets an operator declare "
            "a desired outcome ('intent') without specifying how to achieve "
            "it; an intent handler translates the intent into concrete "
            "network configuration and continuously monitors fulfilment."
        ),
    },
    {
        "id": "intent-expression-model",
        "source": "TS 28.312 §6 / §7",
        "text": (
            "TS 28.312 models an intent as a set of expectations (targets, "
            "context, and fulfilment thresholds) plus reporting requirements "
            "— the intent owner is notified when an expectation is met, "
            "degraded, or no longer feasible, similar to MINAS's applied / "
            "degraded / failed intent statuses."
        ),
    },
    {
        "id": "graceful-degradation",
        "source": "TS 28.312 §6.3 (fulfilment states)",
        "text": (
            "When a network cannot fully satisfy an intent's target (e.g. "
            "insufficient radio resources for the requested throughput), the "
            "expected behaviour is graceful degradation: apply the best "
            "feasible configuration, report the shortfall, and flag the "
            "fulfilment state as degraded rather than silently failing."
        ),
    },
    {
        "id": "qos-flow-concept",
        "source": "TS 23.501 §5.7.1",
        "text": (
            "A QoS Flow is the finest granularity of QoS differentiation in "
            "a PDU Session, identified by a QFI (QoS Flow Identifier) and "
            "characterized by its associated 5QI, GBR/MBR (if GBR), and "
            "priority level."
        ),
    },
    {
        "id": "ran-prb-concept",
        "source": "TS 38.300 (O-RAN resource scheduling)",
        "text": (
            "Physical Resource Blocks (PRBs) are the smallest unit of radio "
            "resource a gNB scheduler allocates in time and frequency; the "
            "throughput a slice can sustain is bounded by how many PRBs the "
            "scheduler assigns it relative to the cell's total PRB budget."
        ),
    },
    {
        "id": "oran-ric-e2",
        "source": "O-RAN Alliance — RIC / E2 interface",
        "text": (
            "In the O-RAN architecture, the RAN Intelligent Controller (RIC) "
            "steers gNB scheduling policy over the E2 interface — this is "
            "the normative path RAN-NSSMF's allocate_prb is meant to use for "
            "real PRB enforcement, once integrated with a live RIC."
        ),
    },
]
