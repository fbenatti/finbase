"""Product modules, customer availability and authenticated customer membership."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from app.database import Base


class NavigationModule(Base):
    __tablename__ = "navigation_module"
    code = Column(String(60), primary_key=True)
    parent_code = Column(String(60), ForeignKey("navigation_module.code"))
    label = Column(String(160), nullable=False)
    path = Column(String(240), unique=True, nullable=False)
    renderer = Column(String(80), nullable=False)
    permission_module = Column(String(80))
    view_params = Column(JSON, nullable=False, default=dict)
    legacy_path = Column(String(240))
    api_prefixes = Column(JSON, nullable=False, default=list)
    icon = Column(String(80), nullable=False)
    position = Column(Integer, nullable=False)
    required = Column(Boolean, nullable=False, default=False)
    development_only = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    visible = Column(Boolean, nullable=False, default=True)
    custom_label = Column(String(160))


class NavigationCustomer(Base):
    __tablename__ = "navigation_customer"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(160), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CustomerModule(Base):
    __tablename__ = "customer_module"
    customer_id = Column(Integer, ForeignKey("navigation_customer.id", ondelete="CASCADE"), primary_key=True)
    module_code = Column(String(60), ForeignKey("navigation_module.code", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NavigationMembership(Base):
    __tablename__ = "navigation_membership"
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True)
    customer_id = Column(Integer, ForeignKey("navigation_customer.id", ondelete="CASCADE"), nullable=False, index=True)
