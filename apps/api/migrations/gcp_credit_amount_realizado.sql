-- GCP realizado: subtract credit amount from BilledCost on credited lines
-- (total realizado = SUM(BilledCost) + SUM(credit amount), credit amount is negative)

INSERT INTO destination_column (destination_table_id, column_name, data_type, is_required, position)
SELECT 1, 'CreditAmount', 'DOUBLE PRECISION', false, COALESCE(MAX(position), 0) + 1
FROM destination_column
WHERE destination_table_id = 1
  AND NOT EXISTS (
    SELECT 1 FROM destination_column
    WHERE destination_table_id = 1 AND column_name = 'CreditAmount'
  );

-- gcp-4p, gcp-ti: map credits[0].amount -> CreditAmount
INSERT INTO source_destination_mapping (
    source_connection_id, destination_table_id,
    source_column, destination_column,
    transform_type, transform_config, priority, active
)
SELECT conn.id, 1, 'credits', 'CreditAmount', 'JSON_PATH', '{"path": "[0].amount"}'::jsonb, 0, true
FROM source_connection conn
WHERE conn.code IN ('gcp-4p', 'gcp-ti')
  AND NOT EXISTS (
    SELECT 1 FROM source_destination_mapping m
    WHERE m.source_connection_id = conn.id
      AND m.destination_table_id = 1
      AND m.destination_column = 'CreditAmount'
  );

UPDATE business_rule
SET
    name = 'Realizado GCP com desconto de credito',
    expression = 'BilledCost + CreditAmount',
    config_json = '{
      "scope_filter": {"field": "Credits", "op": "=", "value": "CREDIT"},
      "source_column": "BilledCost",
      "operation": "col+",
      "operand_column": "CreditAmount"
    }'::jsonb,
    updated_at = NOW()
WHERE id = 10
  AND target_column = 'realizado'
  AND cloud_scope = 'gcp';
