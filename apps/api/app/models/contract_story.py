from sqlalchemy import Column, DateTime, Integer, JSON, func

from app.database import Base


class ContractStoryLedger(Base):
    """Shared ledger for the executive contract story and monthly consumption."""

    __tablename__ = "contract_story_ledger"

    id = Column(Integer, primary_key=True, default=1)
    payload = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(Integer, nullable=True)
