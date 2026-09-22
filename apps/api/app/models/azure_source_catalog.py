"""Catálogo de fontes Azure — metadata que permite selecionar por ambiente.

O pipeline atual vincula Bronze a `source_connection.code`, então cada fonte
lógica continua tendo seu próprio registro. O que este catálogo acrescenta é a
camada que o plano exige: o operador vê **um grupo por ambiente** e liga/desliga
as fontes que quer coletar, sem configurar dezenas de cards nem repetir segredo.

O catálogo é declarativo e fica no PostgreSQL (metadata, não dado volumoso):

- `source_key` identifica a semântica da fonte (`resource_graph`, `monitor_vm_metrics`, …);
- `environment_slug` identifica o ambiente/tenant (`4p`, `ti`, …);
- a linha do catálogo descreve a fonte **possível** (o que ela coleta, qual API,
  qual permissão, qual Bronze); a conexão descreve a fonte **configurada** para
  um ambiente.

Separação deliberada: o catálogo não guarda segredo nem estado de execução. Isso
vive em `source_connection.config_json` e nas tabelas de job/blob.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint, func

from app.database import Base


class AzureSourceCatalogEntry(Base):
    """Uma fonte lógica de Azure que pode ser ligada por ambiente."""

    __tablename__ = "azure_source_catalog"
    __table_args__ = (UniqueConstraint("source_key", name="azure_source_catalog_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: Identificador estável da semântica da fonte. Nunca muda.
    source_key = Column(String(60), unique=True, nullable=False)
    display_name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)

    #: Ordem de exibição dentro do grupo do ambiente.
    position = Column(Integer, nullable=False, default=100)

    #: `connector_type` usado pela conexão desta fonte no pipeline.
    connector_type = Column(String(50), nullable=False)

    #: `code` sugerido para um ambiente novo (`{code_pattern}` recebe o slug).
    code_pattern = Column(String(80), nullable=False)
    #: Tabela Bronze prevista (`{code_sanitizado}` = hífens/pontos viram `_`).
    bronze_pattern = Column(String(120), nullable=False)
    #: Camada de destino do dado detalhado desta fonte.
    silver_target = Column(String(160), nullable=True)
    #: Cubo/materialização final, quando houver.
    gold_target = Column(String(160), nullable=True)

    #: Fonte de custo? Decide se pode alimentar o destino FOCUS.
    is_cost_source = Column(Boolean, nullable=False, default=False)

    #: Endpoint/API e permissão exigida — documentação operacional da fonte.
    api_reference = Column(String(200), nullable=True)
    required_permission = Column(String(200), nullable=True)

    #: Quando a fonte é de telemetria, os parâmetros que a conexão carrega.
    default_window_days = Column(Integer, nullable=True)
    default_metrics = Column(JSON, nullable=False, default=list)

    #: `implemented` | `planned` — deixa explícito o que já existe no código.
    implementation_status = Column(String(20), nullable=False, default="implemented")
    #: `scope` que a fonte cobre: `subscription`, `tenant`, `resource`, `catalog`.
    azure_scope = Column(String(30), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
