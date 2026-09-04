"""Identity and RBAC models: users, groups, roles, permissions, refresh tokens."""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Table, UniqueConstraint, func,
)
from sqlalchemy.orm import relationship
from app.database import Base

from app.core.modules import ALL_MODULES, ALL_ACTIONS  # noqa: F401 (re-export)


class User(Base):
    __tablename__ = "user_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(120), nullable=False, unique=True)
    email = Column(String(320))
    full_name = Column(String(200))
    password_hash = Column(String(200))  # nullable: usuário SSO não tem senha
    auth_provider = Column(String(20), nullable=False, default="local")  # local|oidc|saml
    external_id = Column(String(320))  # id do usuário no IdP
    is_active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    roles = relationship("Role", secondary="user_role", back_populates="users")
    groups = relationship("Group", secondary="user_group", back_populates="users")


class Role(Base):
    __tablename__ = "role"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(String(500))
    is_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", secondary="user_role", back_populates="roles")
    groups = relationship("Group", secondary="group_role", back_populates="roles")
    permissions = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")
    parents = relationship(
        "Role", secondary="role_role",
        primaryjoin="Role.id == role_role.c.role_id",
        secondaryjoin="Role.id == role_role.c.parent_role_id",
        backref="children",
    )


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = (UniqueConstraint("role_id", "module", "action", name="uq_role_permission"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey("role.id", ondelete="CASCADE"), nullable=False)
    module = Column(String(80), nullable=False)
    action = Column(String(20), nullable=False)  # read|create|update|delete

    role = relationship("Role", back_populates="permissions")


class Group(Base):
    __tablename__ = "group_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(String(500))
    external_id = Column(String(320))  # id do grupo no IdP (Fase 4)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", secondary="user_group", back_populates="groups")
    roles = relationship("Role", secondary="group_role", back_populates="groups")


user_role = Table(
    "user_role", Base.metadata,
    Column("user_id", Integer, ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
)

user_group = Table(
    "user_group", Base.metadata,
    Column("user_id", Integer, ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("group_account.id", ondelete="CASCADE"), primary_key=True),
)

group_role = Table(
    "group_role", Base.metadata,
    Column("group_id", Integer, ForeignKey("group_account.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
)

role_role = Table(
    "role_role", Base.metadata,
    Column("role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
    Column("parent_role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
)


class UserGrant(Base):
    """Grant direto nominal: permissão fina concedida a um usuário sem role."""
    __tablename__ = "user_grant"
    __table_args__ = (UniqueConstraint("user_id", "module", "action", name="uq_user_grant"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False)
    module = Column(String(80), nullable=False)
    action = Column(String(20), nullable=False)  # read|create|update|delete
    granted_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"))
    reason = Column(String(500), nullable=False)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(128), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    replaced_by_id = Column(Integer, ForeignKey("refresh_token.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_agent = Column(String(500))
    ip = Column(String(80))
