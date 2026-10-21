"""Modelo de controle de acesso (RBAC + ACL): ACLs por tela, tabela, linha e campo,
escopos (hierarquia, orçamento) e regras de negócio.

Operações: read, create, write, delete. A operação `write` substitui `update`
do RBAC legado; o mapeamento fica em `app/core/access/legacy.py`.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base

ACL_OPERATIONS = ("read", "create", "write", "delete")
ACL_EFFECTS = ("allow", "deny")
ACL_RESOURCE_TYPES = ("screen", "table", "field", "rule")
MATCH_MODES = ("exact", "subtree")
BUSINESS_RULE_EVENTS = ("before_query", "before_write", "after_write")


class AccessAcl(Base):
    """Uma regra de acesso de um perfil sobre um recurso."""

    __tablename__ = "access_acl"
    __table_args__ = (
        UniqueConstraint("role_id", "resource_type", "resource", "operation", "effect", name="uq_access_acl"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey("role.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_type = Column(String(20), nullable=False)  # screen|table|field|rule
    resource = Column(String(200), nullable=False)  # rota, tabela, tabela.campo ou código da regra
    operation = Column(String(20), nullable=False)  # read|create|write|delete
    effect = Column(String(10), nullable=False, default="allow")  # allow|deny
    active = Column(Boolean, nullable=False, default=True)
    legacy_module = Column(String(80))  # módulo RBAC de origem, quando veio do role_permission
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conditions = relationship("AccessAclCondition", back_populates="acl", cascade="all, delete-orphan")


class AccessScope(Base):
    """Um valor de escopo: um nó da hierarquia, uma unidade de orçamento, um centro de custo."""

    __tablename__ = "access_scope"
    __table_args__ = (UniqueConstraint("scope_type", "value", name="uq_access_scope"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_type = Column(String(40), nullable=False)  # hierarchy|budget|cost_center
    value = Column(String(300), nullable=False)  # caminho do nó, código da unidade etc.
    label = Column(String(200))
    node_ref = Column(String(120))  # id do nó na tabela de hierarquia, quando houver
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AccessAclCondition(Base):
    """Filtro de linha: a ACL só vale para linhas cuja `column` bate com o escopo do usuário."""

    __tablename__ = "access_acl_condition"

    id = Column(Integer, primary_key=True, autoincrement=True)
    acl_id = Column(Integer, ForeignKey("access_acl.id", ondelete="CASCADE"), nullable=False, index=True)
    column_name = Column(String(120), nullable=False)  # ex.: hierarchy_path, budget_unit
    scope_type = Column(String(40), nullable=False)  # qual tipo de escopo do usuário compara
    match_mode = Column(String(10), nullable=False, default="subtree")  # exact|subtree

    acl = relationship("AccessAcl", back_populates="conditions")


class UserScope(Base):
    """Escopo concedido a uma pessoa, com prazo e motivo (como o user_grant)."""

    __tablename__ = "user_scope"
    __table_args__ = (UniqueConstraint("user_id", "scope_id", name="uq_user_scope"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_id = Column(Integer, ForeignKey("access_scope.id", ondelete="CASCADE"), nullable=False)
    match_mode = Column(String(10), nullable=False, default="subtree")
    granted_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"))
    reason = Column(String(500), nullable=False)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GroupScope(Base):
    """Escopo concedido a um grupo; vale para todos os membros."""

    __tablename__ = "group_scope"
    __table_args__ = (UniqueConstraint("group_id", "scope_id", name="uq_group_scope"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("group_account.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_id = Column(Integer, ForeignKey("access_scope.id", ondelete="CASCADE"), nullable=False)
    match_mode = Column(String(10), nullable=False, default="subtree")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AccessBusinessRule(Base):
    """Regra de negócio: avaliada antes da consulta, antes ou depois da gravação."""

    __tablename__ = "access_business_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(120), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    event = Column(String(20), nullable=False)  # before_query|before_write|after_write
    resource = Column(String(200), nullable=False)
    condition_json = Column(JSONB, nullable=False, default=dict)
    action_json = Column(JSONB, nullable=False, default=dict)
    priority = Column(Integer, nullable=False, default=100)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
