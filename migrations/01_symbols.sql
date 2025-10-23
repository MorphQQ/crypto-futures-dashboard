-- Symbol master table
CREATE TABLE IF NOT EXISTS symbols (
  symbol TEXT PRIMARY KEY,
  exchange TEXT NOT NULL DEFAULT 'binance',
  active BOOLEAN NOT NULL DEFAULT TRUE,
  market_cap_rank INTEGER,
  tags JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(active);
