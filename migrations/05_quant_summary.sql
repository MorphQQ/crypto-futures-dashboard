-- Fast-access quant summary table (single row per symbol+timeframe)
CREATE TABLE IF NOT EXISTS quant_summary (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL DEFAULT '1m',
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  oi_z NUMERIC,
  ls_delta_pct NUMERIC,
  imbalance NUMERIC,
  funding NUMERIC,
  confluence_score NUMERIC,
  bias TEXT,
  top_levels JSONB DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_quant_summary_updated_at ON quant_summary(updated_at DESC);
