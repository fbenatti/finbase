-- Remove a coluna department.cloud_filter ("Cloud filter" em /governanca/organizacao).
-- A cloud de um nó passa a ser derivada apenas do ramo do provedor na hierarquia.
-- Seguro e idempotente: guarda um backup dos valores antes de remover.
BEGIN;
CREATE TABLE IF NOT EXISTS department_cloud_filter_backup AS
  SELECT id, cloud_filter, NOW() AS backed_up_at FROM department WHERE false;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'department' AND column_name = 'cloud_filter') THEN
    EXECUTE 'INSERT INTO department_cloud_filter_backup (id, cloud_filter, backed_up_at)
             SELECT id, cloud_filter, NOW() FROM department WHERE cloud_filter IS NOT NULL';
    EXECUTE 'ALTER TABLE department DROP COLUMN cloud_filter';
  END IF;
END $$;
COMMIT;
-- Rollback: ALTER TABLE department ADD COLUMN cloud_filter VARCHAR(50);
--           UPDATE department d SET cloud_filter = b.cloud_filter FROM department_cloud_filter_backup b WHERE b.id = d.id;
