from sqlalchemy import Column, DateTime, String, func

from app.database import Base


class ConnectorHidden(Base):
    """Tipo de conector oculto da página Fontes de dados.

    Cada linha é um tipo desligado pelo administrador. Ausência de linha = visível (padrão).
    """

    __tablename__ = "connector_hidden"

    connector_type = Column(String(64), primary_key=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
