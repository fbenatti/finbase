"""Revisão humana de uma decisão de rightsizing (P9).

A decisão vive numa tabela Iceberg, que é *append-only*: **não há UPDATE linha a
linha**. Registrar revisão como alteração da decisão quebraria o snapshot imutável
que o plano exige.

Por isso a revisão vive numa tabela própria, em PostgreSQL, com o estado anterior
gravado. O histórico de quem mudou o quê, quando e por quê fica completo, e o
snapshot da decisão permanece intocado.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.database import Base


class RightsizingReview(Base):
    __tablename__ = "rightsizing_review"

    id = Column(Integer, primary_key=True)
    # Liga à decisão pelo hash da evidência — a chave estável entre execuções.
    evidence_hash = Column(String(64), nullable=False, index=True)
    review_status = Column(String(20), nullable=False)
    previous_status = Column(String(20))
    reviewer = Column(String(160))
    note = Column(Text)
    reviewed_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
