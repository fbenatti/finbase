from sqlalchemy import (
    Column, Integer, String, DateTime, Float, JSON, ForeignKey, func, Index,
)
from app.database import Base


class FactCost(Base):
    """Fact table with normalized cost data, classifications and business rules applied."""

    __tablename__ = "fact_cost"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=False, index=True)

    # Chaves de negócio
    billing_period = Column(String(6), nullable=False, index=True)
    cost_type = Column(String(20), nullable=False, default="actual")  # actual | amortized

    # Dimensões comuns de custo cloud
    date = Column(String(10), nullable=True)
    subscription_id = Column(String(100), nullable=True)
    subscription_name = Column(String(200), nullable=True)
    resource_group = Column(String(200), nullable=True)
    service_name = Column(String(200), nullable=True)
    service_category = Column(String(200), nullable=True)
    meter_category = Column(String(200), nullable=True)
    meter_subcategory = Column(String(200), nullable=True)
    meter_name = Column(String(300), nullable=True)
    resource_type = Column(String(200), nullable=True)
    resource_name = Column(String(300), nullable=True)
    resource_id = Column(String(500), nullable=True)
    region = Column(String(100), nullable=True)
    currency = Column(String(10), nullable=True)
    unit_of_measure = Column(String(50), nullable=True)

    # Métricas de custo
    quantity = Column(Float, nullable=True)
    effective_price = Column(Float, nullable=True)
    cost_usd = Column(Float, nullable=True)
    payg_cost_usd = Column(Float, nullable=True)

    # Regras de negócio aplicadas (calculadas)
    cost_brl = Column(Float, nullable=True)
    tax_base = Column(Float, nullable=True)
    tax_marketplace = Column(Float, nullable=True)
    final_cost_brl = Column(Float, nullable=True)

    # Classificações aplicadas — departamento
    diretoria = Column(String(100), nullable=True)
    department_name = Column(String(200), nullable=True)
    department_path = Column(String(500), nullable=True)
    centro_custo = Column(String(50), nullable=True)
    ambiente = Column(String(50), nullable=True)
    departamento_nivel_1 = Column(String(200), nullable=True)
    departamento_nivel_2 = Column(String(200), nullable=True)
    departamento_nivel_3 = Column(String(200), nullable=True)
    departamento_nivel_4 = Column(String(200), nullable=True)
    departamento_nivel_5 = Column(String(200), nullable=True)
    modelo_ai = Column(String(100), nullable=True)

    # Tags e campos extras (datasource-specific)
    tags = Column(JSON, nullable=True)
    extra = Column(JSON, nullable=True)

    # Metadados
    blob_path = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_fact_cost_period", "billing_period", "cost_type"),
        Index("ix_fact_cost_conn_period", "connection_id", "billing_period"),
    )
