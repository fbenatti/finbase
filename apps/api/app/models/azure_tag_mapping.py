"""Global mapping from Azure tag keys to BILLING_CLOUD fields."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint, func

from app.database import Base


class AzureTagMapping(Base):
    __tablename__ = "azure_tag_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tag_key = Column(String(512), nullable=False)
    focus_field = Column(String(200), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("tag_key", name="uq_azure_tag_mapping_key"),)
