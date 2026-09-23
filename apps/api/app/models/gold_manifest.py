"""Manifesto de materialização Gold.

Cada publicação de cubo Gold deixa uma linha aqui: código e versão do contrato,
run, fontes lidas, hash do SQL, linhas e totais financeiros, delta de
reconciliação, status, erro sanitizado e o snapshot anterior para rollback.

Vive no PostgreSQL porque é **metadata de execução**, não dado financeiro: a
regra do projeto é que linhas financeiras ficam no Iceberg e o PostgreSQL guarda
configuração, workflow e metadata.

Substitui a confiança em `gold_rule.materialization_rows`, que não é evidência de
publicação: em 23/09/2026 cinco regras estavam com status `ERROR` e contagem
preenchida, e as tabelas correspondentes viviam em `iceberg.dimon`, não em
`iceberg.gold`.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class GoldManifest(Base):
    """Uma materialização Gold registrada."""

    __tablename__ = "gold_manifest"
    __table_args__ = (
        Index("ix_gold_manifest_cube", "cube_code", "started_at"),
        Index("ix_gold_manifest_status", "status", "started_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cube_code = Column(String(120), nullable=False)
    physical_table = Column(String(140), nullable=False)
    namespace = Column(String(40), nullable=False, default="gold")
    contract_version = Column(String(20), nullable=False)
    run_id = Column(Integer, nullable=True)
    rule_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False)
    reason = Column(String(400), nullable=True)
    sql_hash = Column(String(64), nullable=True)
    source_tables = Column(JSONB, nullable=False, default=list)
    source_freshness_at = Column(DateTime(timezone=True), nullable=True)
    rows_read = Column(BigInteger, nullable=True)
    rows_written = Column(BigInteger, nullable=True)
    financial_total_in = Column(Float, nullable=True)
    financial_total_out = Column(Float, nullable=True)
    financial_delta = Column(Float, nullable=True)
    tolerance = Column(Float, nullable=False, default=0.01)
    reconciliation_status = Column(String(20), nullable=True)
    currency = Column(String(8), nullable=True)
    previous_snapshot_id = Column(String(64), nullable=True)
    rollback_table = Column(String(140), nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), server_default=func.now())
