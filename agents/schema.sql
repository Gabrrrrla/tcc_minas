-- MINAS — Database Schema
-- KPI nomenclature: 3GPP TS 28.552
-- Collected via O1 interface (NETCONF/YANG) + Prometheus UPF complement
--
-- Tables:
--   ran_kpis       — RAN metrics per slice per UE (RAN-NSSMF writes, RAN-NSSMF reads)
--   core_kpis      — Core metrics per slice (CN-NSSMF writes, CN-NSSMF reads)
--   slice_load     — Computed load index per slice (RAN-NSSMF writes, both agents read)
--   intents        — Operator intents received by orchestrator
--   negotiations   — CN-NSSMF <-> RAN-NSSMF negotiation rounds per intent
--   policies       — Applied policies and their lifecycle


-- -------------------------------------------------------------------------
-- RAN KPIs (source: srsRAN via O1 / NETCONF)
-- One row per UE per collection interval (~1s)
-- -------------------------------------------------------------------------
CREATE TABLE ran_kpis (
    id              SERIAL PRIMARY KEY,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- UE identification
    ue_id           TEXT NOT NULL,          -- srsRAN internal UE ID
    imsi            TEXT,                   -- mapped from AMF when available

    -- Slice
    sst             INTEGER NOT NULL,       -- S-NSSAI SST (1 or 2)
    sd              TEXT DEFAULT NULL,      -- S-NSSAI SD (optional)

    -- 3GPP TS 28.552 KPIs
    rsrp_dbm        REAL,                   -- L1M.UE-RSRP (dBm)
    sinr_db         REAL,                   -- L1M.RS-SINR (dB)
    mcs_dl          INTEGER,                -- downlink MCS index
    mcs_ul          INTEGER,                -- uplink MCS index
    prb_used_dl     INTEGER,                -- RRU.PrbUsedDl (number of PRBs)
    prb_used_ul     INTEGER,                -- RRU.PrbUsedUl
    thp_dl_mbps     REAL,                   -- DRB.UEThpDl (Mbps)
    thp_ul_mbps     REAL                    -- DRB.UEThpUl (Mbps)
);

CREATE INDEX idx_ran_kpis_slice_time ON ran_kpis (sst, collected_at DESC);
CREATE INDEX idx_ran_kpis_ue_time    ON ran_kpis (ue_id, collected_at DESC);


-- -------------------------------------------------------------------------
-- Core KPIs (source: Open5GS AMF + UPF via Prometheus :9090)
-- One row per slice per collection interval (~1s)
-- -------------------------------------------------------------------------
CREATE TABLE core_kpis (
    id              SERIAL PRIMARY KEY,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Slice
    sst             INTEGER NOT NULL,
    sd              TEXT DEFAULT NULL,

    -- AMF (via O1 / REST)
    ues_registered  INTEGER,                -- UEs registered in this slice
    pdu_sessions    INTEGER,                -- active PDU sessions in this slice

    -- UPF (via Prometheus)
    thp_dl_mbps     REAL,                   -- aggregate DL throughput for slice (Mbps)
    thp_ul_mbps     REAL,                   -- aggregate UL throughput for slice (Mbps)
    bytes_dl        BIGINT,                 -- total DL bytes since last reset
    bytes_ul        BIGINT                  -- total UL bytes since last reset
);

CREATE INDEX idx_core_kpis_slice_time ON core_kpis (sst, collected_at DESC);


-- -------------------------------------------------------------------------
-- Slice load index (computed by RAN-NSSMF, read by both agents)
-- carga_slice = (ues_ativos_slice / ues_total) * throughput_atual_slice
-- -------------------------------------------------------------------------
CREATE TABLE slice_load (
    id              SERIAL PRIMARY KEY,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    sst             INTEGER NOT NULL,
    sd              TEXT DEFAULT NULL,

    ues_ativos      INTEGER NOT NULL,
    ues_total       INTEGER NOT NULL,
    thp_dl_mbps     REAL NOT NULL,
    load_index      REAL NOT NULL,          -- the computed index (0.0 – 1.0+)

    prb_used_dl     INTEGER,                -- aggregate PRBs used by slice
    sinr_avg_db     REAL                    -- average SINR across UEs in slice
);

CREATE INDEX idx_slice_load_slice_time ON slice_load (sst, computed_at DESC);


-- -------------------------------------------------------------------------
-- Intents (received by orchestrator from operator)
-- -------------------------------------------------------------------------
CREATE TABLE intents (
    id              SERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    raw_text        TEXT NOT NULL,          -- original natural-language intent
    sst             INTEGER,                -- target slice (extracted by LLM)
    target_thp_mbps REAL,                   -- requested throughput guarantee
    window_start    TIMESTAMPTZ,            -- requested enforcement window
    window_end      TIMESTAMPTZ,

    status          TEXT NOT NULL DEFAULT 'received'
                    CHECK (status IN ('received','decomposed','negotiating',
                                      'applied','degraded','failed','reverted'))
);


-- -------------------------------------------------------------------------
-- Negotiations (CN-NSSMF <-> RAN-NSSMF rounds)
-- One row per round; multiple rounds per intent possible
-- -------------------------------------------------------------------------
CREATE TABLE negotiations (
    id              SERIAL PRIMARY KEY,
    intent_id       INTEGER NOT NULL REFERENCES intents(id),
    round           INTEGER NOT NULL DEFAULT 1,
    negotiated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- What core proposed
    proposed_thp_mbps   REAL NOT NULL,

    -- What RAN reported
    ran_load_index      REAL,
    ran_prb_available   INTEGER,
    ran_can_fulfill     BOOLEAN NOT NULL,

    -- Outcome of this round
    agreed_thp_mbps     REAL,              -- NULL if no agreement yet
    outcome             TEXT NOT NULL
                        CHECK (outcome IN ('agreed','degraded','failed','pending')),
    llm_justification   TEXT               -- LLM-generated reasoning (ReAct trace)
);

CREATE INDEX idx_negotiations_intent ON negotiations (intent_id);


-- -------------------------------------------------------------------------
-- Policies (applied to core after successful negotiation)
-- -------------------------------------------------------------------------
CREATE TABLE policies (
    id              SERIAL PRIMARY KEY,
    intent_id       INTEGER NOT NULL REFERENCES intents(id),
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reverted_at     TIMESTAMPTZ,

    sst             INTEGER NOT NULL,
    enforced_thp_mbps   REAL NOT NULL,     -- what was actually applied

    -- 5QI/GBR/MBR values sent to PCF/SMF
    qos_5qi         INTEGER,
    gbr_dl_mbps     REAL,
    mbr_dl_mbps     REAL,

    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','reverted','expired'))
);

CREATE INDEX idx_policies_intent    ON policies (intent_id);
CREATE INDEX idx_policies_status    ON policies (status);
CREATE INDEX idx_policies_sst_time  ON policies (sst, applied_at DESC);


-- -------------------------------------------------------------------------
-- RAN allocations (PRB state per intent, written by RAN-NSSMF)
-- Stores the pre-intent allocation so revert_prb can restore exact values.
-- -------------------------------------------------------------------------
CREATE TABLE ran_allocations (
    id              SERIAL PRIMARY KEY,
    intent_id       INTEGER NOT NULL REFERENCES intents(id),
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reverted_at     TIMESTAMPTZ,

    sst             INTEGER NOT NULL,
    prb_allocated   INTEGER NOT NULL,           -- PRBs set by this intent
    supported_thp_mbps  REAL NOT NULL,          -- throughput the allocation supports
    prb_previous    INTEGER,                    -- PRBs before this intent (NULL = unknown)

    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','reverted'))
);

CREATE INDEX idx_ran_alloc_intent ON ran_allocations (intent_id);
CREATE INDEX idx_ran_alloc_status ON ran_allocations (sst, status);
