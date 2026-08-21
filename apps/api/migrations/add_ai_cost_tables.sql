-- AI cost origin: link license products to FOCUS rows and optional per-user allocation.

CREATE TABLE IF NOT EXISTS ai_cost_link (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    product_name VARCHAR(200) NOT NULL,
    license_sku VARCHAR(200),
    valid_from VARCHAR(6),
    valid_until VARCHAR(6),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    distribution_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    distribution_basis VARCHAR(30),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ai_cost_link_provider ON ai_cost_link (provider);

CREATE TABLE IF NOT EXISTS ai_cost_link_source (
    id SERIAL PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES ai_cost_link(id) ON DELETE CASCADE,
    connection_id INTEGER REFERENCES source_connection(id) ON DELETE SET NULL,
    filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    value_column VARCHAR(50) NOT NULL DEFAULT 'BilledCost',
    percent DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ai_cost_link_source_link ON ai_cost_link_source (link_id);
CREATE INDEX IF NOT EXISTS ix_ai_cost_link_source_connection ON ai_cost_link_source (connection_id);

CREATE TABLE IF NOT EXISTS ai_cost_allocation (
    id SERIAL PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES ai_cost_link(id) ON DELETE CASCADE,
    billing_period VARCHAR(6) NOT NULL,
    user_email VARCHAR(320),
    provider VARCHAR(100) NOT NULL,
    product_name VARCHAR(200) NOT NULL,
    allocated_amount DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    allocation_basis VARCHAR(30),
    source_refs JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ai_cost_allocation_period ON ai_cost_allocation (billing_period);
CREATE INDEX IF NOT EXISTS ix_ai_cost_allocation_link_period ON ai_cost_allocation (link_id, billing_period);
