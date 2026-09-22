"""Correlacionamento Bronze → Silver.

A Bronze é heterogênea por construção: cada fonte tem o seu formato e os seus
nomes. A Silver é o contrato estável — quando chega uma fonte semelhante de
outra cloud, ela é normalizada para a **mesma** tabela, e quem consome não
precisa saber de onde veio.

Esta tabela é o registro desse mapeamento: qual campo bruto vira qual coluna
canônica, com que tipo e com que unidade. Sem ela, cada transformação seria uma
regra escondida no código e não haveria como auditar a equivalência entre fontes.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, func,
)
from app.database import Base


class BronzeSilverMapping(Base):
    __tablename__ = "bronze_silver_mapping"
    __table_args__ = (
        UniqueConstraint("source_key", "bronze_field", name="uq_bronze_silver_field"),
    )

    id = Column(Integer, primary_key=True)
    # Fonte dona do campo bruto (`source_key` do catálogo Azure).
    source_key = Column(String(60), nullable=False, index=True)
    # Tabela Bronze de origem.
    bronze_table = Column(String(200), nullable=False)
    # Tabela Silver de destino — comum entre clouds equivalentes.
    silver_table = Column(String(200), nullable=False, index=True)
    # Campo bruto e a coluna canônica em que ele se torna.
    bronze_field = Column(String(200), nullable=False)
    silver_field = Column(String(200), nullable=False)
    # Tipo canônico na Silver (varchar/decimal/bigint/timestamp).
    silver_type = Column(String(40), nullable=False, default="varchar")
    # Unidade canônica quando o campo é uma medida.
    unit = Column(String(40), nullable=True)
    # Papel do campo na Silver: identidade, dimensão, medida, tempo, qualidade.
    role = Column(String(20), nullable=False, default="dimension")
    required = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
