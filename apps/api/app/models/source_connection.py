from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, func
from app.database import Base


class SourceConnection(Base):
    __tablename__ = "source_connection"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    connector_type = Column(String(50), nullable=False)
    cloud_name = Column(String(100), nullable=True)
    # Canonical provider slug used to match business/classification rules
    # without free-text inference (e.g. "azure", "gcp", "ibm", "aws", "oci",
    # "atlas"). Kept explicit so a new CSV source from any cloud works without
    # code changes.
    cloud_scope = Column(String(50), nullable=True, index=True)
    config_json = Column(JSON, nullable=False, default=dict)
    blob_prefixes = Column(JSON, nullable=False, default=list)
    last_test_status = Column(String(30), nullable=True)
    last_test_message = Column(String(500), nullable=True)
    last_tested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SystemIntegration(Base):
    """Serviço auxiliar (não é fonte de dados de nuvem).

    Segue o mesmo princípio das conexões: **nada de endpoint ou segredo
    chumbado**. O que é sensível fica em `secret_json`, nunca devolvido pela API;
    o que é variável fica em `config_json`.
    """

    __tablename__ = "system_integration"

    id = Column(Integer, primary_key=True)
    code = Column(String(60), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=False)
    # `jev` | `servicenow` | ...
    integration_type = Column(String(60), nullable=False)
    description = Column(Text)
    enabled = Column(Boolean, nullable=False, default=False)
    # Endpoint e parâmetros: variáveis de ambiente, não código.
    config_json = Column(JSON, nullable=False, default=dict)
    # Token/segredo. Nunca sai em resposta de API.
    secret_json = Column(JSON, nullable=False, default=dict)
    # Último teste de conectividade, para o operador ver o estado.
    last_test_at = Column(DateTime(timezone=True))
    last_test_status = Column(String(30))
    last_test_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
