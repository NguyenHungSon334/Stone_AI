-- Schema v2: structured dimensions + search_text for hybrid search

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Dimension columns (mm) — for precise SQL filtering
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS chieu_dai_mm   int,
  ADD COLUMN IF NOT EXISTS chieu_rong_mm  int,
  ADD COLUMN IF NOT EXISTS chieu_cao_mm   int,
  ADD COLUMN IF NOT EXISTS kich_thuoc_json jsonb;

-- search_text: regular column (populated by import script, avoids GENERATED memory overhead)
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS search_text text;

-- NOTE: GIN trigram index skipped (Supabase free tier 32MB limit)
-- 233 products → sequential scan on search_text is sub-millisecond, no index needed

-- B-tree indexes for dimension range queries
CREATE INDEX IF NOT EXISTS products_chieu_dai_idx  ON products(chieu_dai_mm);
CREATE INDEX IF NOT EXISTS products_chieu_cao_idx  ON products(chieu_cao_mm);
CREATE INDEX IF NOT EXISTS products_chieu_rong_idx ON products(chieu_rong_mm);

-- Ensure price indexes exist
CREATE INDEX IF NOT EXISTS products_gia_xanh_den_idx ON products(gia_da_xanh_den);
CREATE INDEX IF NOT EXISTS products_gia_xanh_reu_idx ON products(gia_da_xanh_reu);
