-- Per-minute WS-derived metrics (hypertable)
CREATE TABLE IF NOT EXISTS market_ws_metrics (
  ts TIMESTAMPTZ NOT NULL,
  symbol TEXT NOT NULL,
  last_price NUMERIC,
  bid NUMERIC,
  ask NUMERIC,
  volume_24h NUMERIC,
  oi NUMERIC,               -- open interest
  oi_delta NUMERIC,         -- change in OI within minute (computed by ingest)
  taker_buy_volume NUMERIC,
  taker_buy_ratio NUMERIC,
  funding_rate NUMERIC,
  global_long_short_ratio NUMERIC,       -- from REST if available
  top_trader_long_short_ratio NUMERIC,   -- placeholder (if derivable)
  top_trader_account_ratio NUMERIC,      -- placeholder
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT create_hypertable('market_ws_metrics', 'ts', if_not_exists => TRUE);

-- Compression policy (if wanted) can be configured later.
-- Retention policy: keep 60 days of 1-minute rows
SELECT add_retention_policy('market_ws_metrics', INTERVAL '60 days');

-- Index for fast per-symbol reads
CREATE INDEX IF NOT EXISTS idx_market_ws_metrics_symbol_ts ON market_ws_metrics(symbol, ts DESC);
