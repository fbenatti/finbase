"""OrphanAnnotation — anotações de governança sobre recursos órfãos.

PostgreSQL guarda APENAS metadata/controle; a linha do recurso vive na Gold
(dest_cloud_orphan_resources). A anotação referencia a linha pelo par
(resource_id, orphan_rule, snapshot_date) e sobrepõe o status de governança
(ORFAO / EM_ANALISE / EXCECAO) na consulta da aba "Recursos Órfãos".
"""
from sqlalchemy import Column, Integer, String, DateTime, Index, func
from app.database import Base


class OrphanAnnotation(Base):
    __tablename__ = "orphan_annotation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_id = Column(String(500), nullable=False, index=True)
    orphan_rule = Column(String(100), nullable=False)
    snapshot_date = Column(String(10), nullable=False)
    status = Column(String(30), nullable=False, default="EM_ANALISE")
    reason = Column(String(500), nullable=True)
    updated_by = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Uma anotação por (recurso, regra, snapshot) — upsert na escrita.
        Index(
            "ix_orphan_annotation_unique",
            "resource_id", "orphan_rule", "snapshot_date",
            unique=True,
        ),
    )
