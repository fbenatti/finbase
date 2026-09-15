from sqlalchemy import Column, Integer, BigInteger, String, DateTime, JSON, ForeignKey, func, Text
from app.database import Base


class EtlTask(Base):
    __tablename__ = "etl_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=True)  # nullable for reprocess tasks
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=True)  # reprocess target
    task_type = Column(String(40), nullable=False, default="extraction")
    # COMPLETED_WITH_ERRORS is longer than the old VARCHAR(20) limit.
    status = Column(String(40), nullable=False, default="RUNNING")
    total_blobs = Column(Integer, nullable=False, default=0)
    processed_blobs = Column(Integer, nullable=False, default=0)
    rows_raw = Column(Integer, nullable=False, default=0)
    rows_normalized = Column(Integer, nullable=False, default=0)
    rows_destination = Column(Integer, nullable=False, default=0)
    current_blob = Column(String(500), nullable=True)
    total_download_bytes = Column(BigInteger, nullable=False, default=0)
    downloaded_bytes_total = Column(BigInteger, nullable=False, default=0)
    current_blob_size = Column(BigInteger, nullable=False, default=0)
    download_bytes = Column(BigInteger, nullable=False, default=0)
    active_downloads = Column(Integer, nullable=False, default=0)
    download_blobs = Column(JSON, nullable=False, default=list)
    phase = Column(String(200), nullable=True)  # human-readable phase description
    connection_ids = Column(JSON, nullable=True, default=list)  # for reprocess: list of connection IDs
    billing_periods = Column(JSON, nullable=True, default=list)  # for reprocess: list of billing periods
    selected_blobs = Column(JSON, nullable=True, default=list)  # for extraction: specific blob paths
    errors = Column(JSON, nullable=False, default=list)
    result_json = Column(JSON, nullable=True)  # extra result data
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # Worker defers tasks that cannot acquire locks so FIFO claim does not spin on the head.
    available_at = Column(DateTime(timezone=True), nullable=True)
