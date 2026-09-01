"""Regras de exceção para o desligue programado de VMs.

Uma exceção identifica um pool de recursos que NÃO deve ser desligado pelo
Azure Automation. O escopo pode ser por ResourceId, ResourceGroup, Subscription,
tag ou combinação de serviço/região. O custo da exceção é calculado comparando
o uptime real com a baseline de "sempre ligado".
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, JSON, func
from app.database import Base


class ShutdownExceptionRule(Base):
    __tablename__ = "shutdown_exception_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    owner_email = Column(String(200), nullable=True)

    # Escopo da exceção (pode combinar múltiplos campos).
    scope_type = Column(String(50), nullable=False, default="resource")  # resource|resource_group|subscription|tag|service
    scope_value = Column(String(500), nullable=True)
    tag_key = Column(String(200), nullable=True)
    tag_value = Column(String(500), nullable=True)

    # Filtros adicionais (JSON com lista de conditions).
    filter_json = Column(JSON, nullable=False, default=dict)

    valid_from = Column(String(6), nullable=True)   # YYYYMM
    valid_until = Column(String(6), nullable=True)  # YYYYMM
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
