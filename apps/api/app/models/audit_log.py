"""Trilha de auditoria: um evento por mutação, com snapshots e diff pré-calculado."""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"))
    username = Column(String(120))  # denormalizado: sobrevive a exclusão do usuário
    auth_provider = Column(String(20))
    module = Column(String(80), nullable=False)
    method = Column(String(12), nullable=False)  # POST|PUT|PATCH|DELETE|LOGIN|LOGIN_FAILED|LOGOUT
    path = Column(String(500))
    status_code = Column(Integer)
    entity_type = Column(String(80))
    entity_id = Column(String(40))
    entity_label = Column(String(300))
    action_label = Column(String(20))  # create|update|delete|execute|login|logout|failed
    before_json = Column(JSONB)
    after_json = Column(JSONB)
    changes_json = Column(JSONB)  # [{field, old, new}]
    ip = Column(String(80))
    user_agent = Column(String(500))
    request_id = Column(String(40))

    __table_args__ = (
        Index("ix_audit_module_entity_time", "module", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_user_time", "user_id", "created_at"),
        Index("ix_audit_created_at", "created_at"),
    )
