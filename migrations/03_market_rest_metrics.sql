-- Per-minute REST-collected metrics (hypertable)
CREATE TABLE IF NOT EXISTS market_rest_metrics (
  ts TIMESTAMPTZ NOT NULL,
  symbol TEXT NOT NULL,
  open NUMERIC,
  high NUMERIC,
  low NUMERIC,
  close NUMERIC,
  volume NUMERIC,
  trades BIGINT,
  oi NUMERIC,
  funding_rate NUMERIC,
  mark_price NUMERIC,
  global_long_short_ratio NUMERIC,
  top_trader_long_short_ratio NUMERIC,
  top_trader_account_ratio NUMERIC,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT create_hypertable('market_rest_metrics', 'ts', if_not_exists => TRUE);

-- Retention policy: keep 60 days of 1-minute rows
SELECT add_retention_policy('market_rest_metrics', INTERVAL '60 days');

CREATE INDEX IF NOT EXISTS idx_market_rest_metrics_symbol_ts ON market_rest_metrics(symbol, ts DESC);
