-- The business-rule engine uses first-match-wins for the same target field.
-- Marketplace must run before the generic Azure tax rule, otherwise its
-- specific 13.83% calculation can never populate ``realizado``.
UPDATE business_rule
SET priority = CASE name
    WHEN 'Taxa Marketplace Azure (13.83%)' THEN 10
    WHEN 'Demais impostos Azure (18.61%)' THEN 20
    ELSE priority
END
WHERE destination_table_id = (
    SELECT id FROM destination_table WHERE upper(name) = 'MODELO FOCUS' LIMIT 1
)
  AND cloud_scope = 'azure'
  AND name IN (
      'Taxa Marketplace Azure (13.83%)',
      'Demais impostos Azure (18.61%)'
  );
