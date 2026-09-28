-- Events (UC1 proactive triggers, SLA violations) and SLA samples (Section
-- 5.4 SLA compliance rate), 2026-09-28. Also in schema.sql for new volumes.
--   docker exec -i postgres psql -U minas -d minas < agents/migrations/002_events_sla_samples.sql
-- Safe to re-run.

-- Something a component reported that the orchestrator (or the operator)
-- must know about:
--   predicted_exhaustion     CN-NSSMF monitor: NWDAF predicts demand above the
--                            slice's guarantee (Use Case 1) — orchestrator acts
--   sla_violation            SLA monitor: observed throughput below target for
--                            several consecutive samples
--   sla_violation_predicted  orchestrator: a predicted exhaustion could only be
--                            met partially (RAN 'degraded') — graceful
--                            degradation, surfaced to the operator
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source      TEXT NOT NULL,
    type        TEXT NOT NULL,
    intent_id   INTEGER REFERENCES intents(id),
    sst         INTEGER,
    payload     JSONB,
    handled_at  TIMESTAMPTZ,
    outcome     TEXT            -- applied / degraded / failed / no_action / undelivered
);
CREATE INDEX IF NOT EXISTS idx_events_intent_type ON events (intent_id, type, created_at DESC);

-- One row per SLA-monitor tick per active intent.
CREATE TABLE IF NOT EXISTS sla_samples (
    id                SERIAL PRIMARY KEY,
    sampled_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    intent_id         INTEGER NOT NULL REFERENCES intents(id),
    sst               INTEGER NOT NULL,
    target_thp_mbps   REAL NOT NULL,
    observed_thp_mbps REAL NOT NULL,
    compliant         BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sla_samples_intent ON sla_samples (intent_id, sampled_at);
