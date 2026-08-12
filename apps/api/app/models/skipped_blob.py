from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, func
from app.database import Base


class SkippedBlob(Base):
    __tablename__ = "skipped_blob"
    __table_args__ = (UniqueConstraint("connection_id", "blob_path"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=False)
    blob_path = Column(Text, nullable=False)
    reason = Column(String(200), nullable=True)
    skipped_at = Column(DateTime(timezone=True), server_default=func.now())
