CREATE TABLE IF NOT EXISTS skipped_blob (
    id SERIAL PRIMARY KEY,
    connection_id INTEGER NOT NULL REFERENCES source_connection(id),
    blob_path TEXT NOT NULL,
    reason VARCHAR(200),
    skipped_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(connection_id, blob_path)
);
