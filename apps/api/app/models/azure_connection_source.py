"""Seleção de fontes por conexão Azure.

Uma conexão Azure representa **um ambiente e uma credencial** — não uma fonte.
As fontes que ela coleta ficam aqui: o par (conexão, fonte) é o que se liga e
desliga, e é dele que sai a carga da Bronze.

Antes cada fonte era uma conexão própria, o que multiplicava cards e repetia
segredos. A conexão continua sendo a dona da credencial e do escopo; a fonte é
uma linha nesta tabela.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func,
)
from app.database import Base


class AzureConnectionSource(Base):
    __tablename__ = "azure_connection_source"
    __table_args__ = (
        UniqueConstraint("connection_id", "source_key", name="uq_azure_conn_source"),
    )

    id = Column(Integer, primary_key=True)
    connection_id = Column(
        Integer,
        ForeignKey("source_connection.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Chave da fonte no catálogo (azure_source_registry.AZURE_SOURCE_CATALOG).
    source_key = Column(String(60), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    # Janela de coleta em dias para fontes de telemetria (1..90).
    window_days = Column(Integer, nullable=True)
    # Métricas escolhidas, quando a fonte é de métricas.
    metrics = Column(String(2000), nullable=True)
    # Assinaturas específicas desta fonte; vazio = todas as do escopo.
    subscription_ids = Column(String(4000), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
