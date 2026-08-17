from sqlalchemy import Column, Integer, String, DateTime, JSON, func
from app.database import Base


class SourceConnection(Base):
    __tablename__ = "source_connection"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    connector_type = Column(String(50), nullable=False)
    cloud_name = Column(String(100), nullable=True)
    # Canonical provider slug used to match business/classification rules
    # without free-text inference (e.g. "azure", "gcp", "ibm", "aws", "oci",
    # "atlas"). Kept explicit so a new CSV source from any cloud works without
    # code changes.
    cloud_scope = Column(String(50), nullable=True, index=True)
    config_json = Column(JSON, nullable=False, default=dict)
    blob_prefixes = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
