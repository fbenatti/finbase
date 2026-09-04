-- Speed up FIFO queue claims for multiprocess workers.
CREATE INDEX IF NOT EXISTS ix_etl_task_queued_fifo
    ON etl_task (task_type, id)
    WHERE status = 'QUEUED';
