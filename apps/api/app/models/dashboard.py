"""Saved dashboard compositions and their visualization widgets."""
from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, func, Index
from app.database import Base


class Dashboard(Base):
    __tablename__ = "dashboard"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(160), nullable=False, unique=True)
    pivot_view_id = Column(Integer, ForeignKey("pivot_view.id", ondelete="SET NULL"), nullable=True)
    global_filters = Column(JSON, nullable=False, default=dict)
    layout_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DashboardWidget(Base):
    __tablename__ = "dashboard_widget"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dashboard_id = Column(Integer, ForeignKey("dashboard.id", ondelete="CASCADE"), nullable=False, index=True)
    pivot_view_id = Column(Integer, ForeignKey("pivot_view.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=False)
    chart_type = Column(String(30), nullable=False, default="line")
    config_json = Column(JSON, nullable=False, default=dict)
    position_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_dashboard_widget_dashboard_position", "dashboard_id", "id"),)
