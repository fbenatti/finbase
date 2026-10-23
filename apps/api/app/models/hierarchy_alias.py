from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text

from app.database import Base


class HierarchyRole(Base):
    """Papel de um nível da hierarquia (Provedor, Cliente, Diretoria...).

    Os painéis falam em papéis; quem decide qual nível é qual papel são as
    regras de ``HierarchyLevelAlias``, que variam por ramo do organograma.
    """
    __tablename__ = "hierarchy_role"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(60), nullable=False, unique=True)
    label = Column(String(80), nullable=False)
    label_plural = Column(String(80), nullable=True)
    description = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HierarchyLevelAlias(Base):
    """Regra "neste ramo, a profundidade N é o papel X".

    ``scope_department_id`` nulo vale para toda a hierarquia; preenchido, vale
    só para os caminhos que passam pelo nó (inclusive o próprio nó, quando a
    profundidade é a dele). Vence a regra do escopo mais profundo.
    """
    __tablename__ = "hierarchy_level_alias"
    __table_args__ = (
        UniqueConstraint("scope_department_id", "depth", name="uq_hierarchy_level_alias_scope_depth"),
        # NULL não colide no UNIQUE acima: a regra global precisa do próprio índice.
        Index("uq_hierarchy_level_alias_global_depth", "depth", unique=True, postgresql_where=text("scope_department_id IS NULL")),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_department_id = Column(Integer, ForeignKey("department.id", ondelete="CASCADE"), nullable=True, index=True)
    depth = Column(Integer, nullable=False)
    role_id = Column(Integer, ForeignKey("hierarchy_role.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
