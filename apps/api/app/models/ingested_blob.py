from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime, Text, ForeignKey, UniqueConstraint, func,
)
from app.database import Base


class IngestedBlob(Base):
    __tablename__ = "ingested_blob"
    __table_args__ = (UniqueConstraint("connection_id", "blob_path"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=False)
    blob_path = Column(Text, nullable=False)
    blob_prefix = Column(Text, nullable=False)
    billing_period = Column(String(6), nullable=True)
    last_modified = Column(DateTime(timezone=True), nullable=False)
    etag = Column(String(200), nullable=True)
    content_length = Column(BigInteger, nullable=True)
    row_count = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="INGESTED")
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
