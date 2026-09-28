-- Adds the 'scheduled' intent status (window_start activation, 2026-09-28).
-- schema.sql only runs when the postgres volume is first created, so an
-- existing database needs this applied once:
--   docker exec -i postgres psql -U minas -d minas < agents/migrations/001_intent_scheduled_status.sql
-- Safe to re-run.

ALTER TABLE intents DROP CONSTRAINT IF EXISTS intents_status_check;
ALTER TABLE intents ADD CONSTRAINT intents_status_check
    CHECK (status IN ('received','scheduled','decomposed','negotiating',
                      'applied','degraded','failed','reverted'));
