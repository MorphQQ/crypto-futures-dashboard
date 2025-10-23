-- Raw incoming WS events (optionally store verbose events for 30 days)
CREATE TABLE IF NOT EXISTS raw_ws_events (
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  symbol TEXT,
  event_type TEXT,
  payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT create_hypertable('raw_ws_events', 'ts', if_not_exists => TRUE);
SELECT add_retention_policy('raw_ws_events', INTERVAL '30 days');

CREATE INDEX IF NOT EXISTS idx_raw_ws_events_symbol ON raw_ws_events(symbol);
