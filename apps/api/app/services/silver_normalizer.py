"""Motor de normalização Bronze → Silver.

Uma fonte **não cria tabela nem código de transformação**: ela declara para qual
modelo canônico ela contribui e como os seus campos brutos chegam lá. Este motor
lê a Bronze, aplica as regras do modelo, resolve o valor canônico, grava a Silver
de forma idempotente e registra o correlacionamento para auditoria.

Adicionar uma nuvem nova é escrever um dicionário. Adicionar um tipo de dado novo
é escrever um modelo em `silver_models`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.services.silver_models import (
    ROLE_MEASURE,
    SilverModel,
    model as get_model,
)

logger = logging.getLogger(__name__)

_NUMERIC = {"decimal", "bigint", "double", "integer"}



def _derivada_coalesce_null(frame: pd.DataFrame, nomes: list[str], resolver) -> pd.Series:
    """Primeira coluna com valor; senão NULL — sem identidade sintética."""
    reais = [c for c in (resolver(n) for n in nomes) if c is not None]
    if not reais:
        return pd.Series([None] * len(frame), index=frame.index, dtype="string")
    combinado = frame[reais[0]].astype("string")
    for extra in reais[1:]:
        combinado = combinado.where(
            combinado.notna() & (combinado != ""), frame[extra].astype("string")
        )
    # Vazio vira NULL: nada de sentinela em atributo.
    return combinado.where(combinado.notna() & (combinado != ""))


def _derivada_menos(frame: pd.DataFrame, nomes: list[str], coluna) -> pd.Series:
    """Diferença entre duas colunas numéricas: `esquerda - direita`.

    Usada no disponível da quota (limite menos consumo). Coluna ausente vira
    `None` — nunca zero, porque zero significaria "sem quota" e vetaria um
    candidato legítimo.
    """
    if len(nomes) < 2:
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")
    esquerda = coluna(nomes[0])
    direita = coluna(nomes[1])
    if esquerda is None or direita is None:
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")
    a = pd.to_numeric(frame[esquerda], errors="coerce")
    b = pd.to_numeric(frame[direita], errors="coerce")
    return a - b


def _derivada_mes(valores: pd.Series) -> pd.Series:
    """Período mensal `YYYYMM` a partir de data ISO (`2026-09-22`) ou
    americana (`09/22/2026`). O formato varia por export, então os dois são
    reconhecidos em vez de assumir um."""
    texto = valores.astype("string")
    iso = texto.str.match(r"^\d{4}-\d{2}")
    americano = texto.str.match(r"^\d{2}/\d{2}/\d{4}")
    saida = pd.Series([None] * len(texto), index=texto.index, dtype="string")
    if iso.any():
        saida[iso] = texto[iso].str.slice(0, 7).str.replace("-", "", regex=False)
    if americano.any():
        partes = texto[americano].str.split("/", expand=True)
        saida[americano] = partes[2].fillna("") + partes[0].fillna("")
    return saida


def _derivada_trecho(valores: pd.Series, tamanho: int = 60) -> pd.Series:
    """Últimos N caracteres de um identificador (nome do recurso)."""
    return valores.astype("string").str.slice(-tamanho)


def _derivada_minusculo(valores: pd.Series) -> pd.Series:
    """Identidade em minúsculas.

    Identificador de recurso na nuvem é case-insensitive, mas cada fonte o
    escreve de um jeito (o Resource Graph usa maiúsculas na assinatura, o export
    de custo minúsculas). Sem uniformizar, o JOIN entre custo e inventário não
    encontra o recurso — o dado está certo dos dois lados e a ligação falha.
    """
    return valores.astype("string").str.lower()


def _derivada_coalesce(frame: pd.DataFrame, nomes: list[str], resolver) -> pd.Series:
    """Primeira coluna com valor; senão uma chave sintética estável.

    Serve para identidade: o recurso quando o export traz, e um identificador
    marcado (`sem-recurso:...`) quando não traz — compras de reserva e taxas
    continuam rastreáveis em vez de descartadas.
    """
    reais = [c for c in (resolver(n) for n in nomes) if c is not None]
    if not reais:
        return pd.Series([None] * len(frame), index=frame.index, dtype="string")
    valores = [frame[c].astype("string") for c in reais]
    combinado = valores[0].copy()
    for extra in valores[1:]:
        combinado = combinado.where(combinado.notna() & (combinado != ""), extra)
    sem_valor = combinado.isna() | (combinado == "")
    if sem_valor.any():
        sintetica = valores[0].fillna("")
        for extra in valores[1:]:
            sintetica = sintetica + ":" + extra.fillna("")
        combinado = combinado.where(~sem_valor, "sem-recurso:" + sintetica)
    return combinado


@dataclass(frozen=True)
class SourceMapping:
    """Como uma fonte preenche um modelo canônico."""

    source_key: str
    model_name: str
    bronze_table: str
    # Coluna bruta -> coluna do modelo. Ausente = constante ou não fornecida.
    columns: dict[str, str]
    # Colunas do modelo preenchidas com valor fixo (ex.: cloud_provider).
    constants: dict[str, Any] = field(default_factory=dict)
    # Colunas do modelo calculadas a partir da Bronze. A chave é a coluna de
    # destino e o valor é a receita: `mes:<coluna>`, `ano_mes:<coluna>` ou
    # `trecho:<coluna>:<chars>` (primeiros N caracteres, para extrair nome de um
    # identificador). Existe porque formato de origem varia, mas o modelo não.
    derived: dict[str, str] = field(default_factory=dict)
    # Ordem de preferência para o valor canônico, quando o modelo tem um.
    measure_priority: tuple[str, ...] = ()
    # Campo do modelo usado como período de gravação; default `_billing_period`.
    period_field: str = "_billing_period"
    notes: str | None = None


# ── Registro de mapeamentos ───────────────────────────────────────────────────
# Cada entrada liga uma fonte do catálogo a um modelo. É a única coisa que uma
# fonte nova precisa declarar.

_SERIES_COMMON: dict[str, str] = {
    "resource_id": "resource_id",
    "resource_name": "resource_name",
    "subscription_id": "sub_account_id",
    "resource_group": "resource_group_id",
    "location": "region_id",
    "vm_size": "resource_sku",
    "resource_type": "resource_type",
    "os_type": "operating_system",
    "power_state": "resource_state",
    "metric": "metric",
    "unit": "metric_unit",
    "dimension": "dimensions_json",
    "bucket_utc": "bucket_start_utc",
    "average": "value_average",
    "minimum": "value_minimum",
    "maximum": "value_maximum",
    "total": "value_total",
    "count": "sample_count",
    "quality_status": "quality_status",
    "gap_reason": "quality_reason",
    "api_version": "source_api_version",
    "_blob_path": "_bronze_blob_path",
    "_billing_period": "_billing_period",
}

_INVENTORY_COMMON: dict[str, str] = {
    "resource_id": "resource_id",
    "resource_name": "resource_name",
    "resource_type": "resource_type",
    "subscription_id": "sub_account_id",
    "resource_group": "resource_group_id",
    "location": "region_id",
    "sku_capacity": "sku_capacity",
    "power_state": "resource_state",
    "os_type": "operating_system",
    "provisioning_state": "provisioning_state",
    "zones": "availability_zones",
    "tags_json": "tags_json",
    "snapshot_date": "valid_from",
    "_billing_period": "_billing_period",
}

SOURCE_MAPPINGS: dict[str, SourceMapping] = {
    # Custo (FOCUS/export do Cost Management). As medidas foram confirmadas
    # contra a origem: `costinbillingcurrency` é o custo efetivo (já com
    # reserva/desconto) e `paygcostinbillingcurrency` é o preço de lista — nos
    # registros de reserva a diferença aparece explicitamente.
    "cost_exports": SourceMapping(
        source_key="cost_exports",
        model_name="resource_cost",
        bronze_table="raw_azure_{env}",
        columns={
            "subscriptionid": "sub_account_id",
            "resourcegroupname": "resource_group_id",
            "resourcelocation": "region_id",
            "location": "region_id",
            "meterid": "resource_sku",
            "consumedservice": "service_name",
            "metercategory": "service_category",
            "chargetype": "charge_category",
            "productname": "charge_description",
            "unitofmeasure": "pricing_unit",
            "quantity": "pricing_quantity",
            "billingcurrency": "pricing_currency",
            "serviceperiodstartdate": "charge_period_start",
            "serviceperiodenddate": "charge_period_end",
            "paygcostinbillingcurrency": "list_cost",
            # Só `effective_cost`: `costinbillingcurrency` é a única medida de
            # custo efetivo na origem; duplicá-la em `billed_cost` seria inventar
            # uma segunda medida que a origem não traz.
            "costinbillingcurrency": "effective_cost",
            "tags": "tags_json",
            "_blob_path": "_bronze_blob_path",
        },
        constants={"cloud_provider": "azure"},
        derived={
            # O período de fatura vem da Bronze, que já o normaliza na ingestão.
            # Só quando ele falta é derivado da data de uso (`date`), para não
            # descartar linha: `coalesce` pega o primeiro com valor.
            "billing_period": "coalesce:_billing_period+periodo_uso",
            "_billing_period": "coalesce:_billing_period+periodo_uso",
            # Identidade: o recurso quando existe; senão, chave sintética
            # (assinatura + medidor) para não perder a linha de custo.
            # Período a partir da data de uso, usado só como fallback.
            "periodo_uso": "mes:date",
            # Identidade em minúsculas: o export grafa a assinatura em
            # minúsculas e o Resource Graph em maiúsculas; sem uniformizar, o
            # JOIN entre custo e inventário não encontra o recurso.
            "resource_id": "coalesce:resourceid+subscriptionid+meterid",
            "resource_id_min": "minusculo:resourceid",
            "resource_name": "trecho:resourceid:60",
        },
        measure_priority=("effective_cost", "list_cost"),
        notes="FOCUS do Cost Management: custo e uso por recurso.",
    ),
    "openai_ptu": SourceMapping(
        source_key="openai_ptu",
        model_name="ptu_capacity_series",
        bronze_table="raw_azure_{env}_ptu",
        columns={
            "account_resource_id": "resource_id",
            "deployment_name": "deployment_name",
            "model_name": "model_name",
            "model_version": "model_version",
            "region": "region_id",
            "sku_name": "sku_name",
            "metric_hour": "metric_hour",
            "provisioned_ptus": "provisioned_ptus",
            "active_tokens_p95": "active_tokens_p95",
            "utilization_avg": "utilization_avg",
            "utilization_p95": "utilization_p95",
            "utilization_max": "utilization_max",
            "input_tokens_sum": "input_tokens_sum",
            "output_tokens_sum": "output_tokens_sum",
            "total_tokens_sum": "total_tokens_sum",
            "request_count": "request_count",
            "success_count": "success_count",
            "client_error_count": "client_error_count",
            "server_error_count": "server_error_count",
            "rate_limited_count": "rate_limited_count",
            "spillover_request_count": "spillover_request_count",
            "time_to_response_p95_ms": "time_to_response_p95_ms",
            "tokens_per_second_avg": "tokens_per_second_avg",
            "source_points": "source_points",
            "expected_points": "expected_points",
            "completeness_pct": "completeness_pct",
            "project_hint": "project_hint",
            "_billing_period": "_billing_period",
        },
        constants={"cloud_provider": "azure"},
        # A conta de recurso é normalizada como qualquer outro Resource ID: é o
        # que permite cruzar a PTU com o custo do FOCUS e com o inventário.
        derived={"resource_id": "minusculo:account_resource_id"},
        notes="Capacidade PTU provisionada e utilização, por hora.",
    ),
    "resource_graph": SourceMapping(
        source_key="resource_graph",
        model_name="resource_inventory",
        bronze_table="raw_azure_{env}_resource_graph",
        columns={
            **_INVENTORY_COMMON,
            # O inventário do Resource Graph traz o tipo em `resource_type` e a
            # fotografia em `snapshot_date`; o resto é igual ao de VM.
            "resource_type": "resource_type",
        },
        constants={"cloud_provider": "azure"},
        # `sku` (Resource Graph) e `vm_size` (coletor de VM) são o mesmo dado
        # com nomes diferentes; a derivada evita que o nulo de um bloqueie o
        # outro, o que aconteceria com duas entradas no mapa de colunas.
        derived={"resource_sku": "coalesce-null:sku+vm_size"},
        notes="Inventário geral e órfãos via Resource Graph.",
    ),
}

# Tipos de recurso por fonte, quando o modelo precisa e a Bronze não traz.
RESOURCE_TYPE_BY_SOURCE: dict[str, str] = {}


def load_mapping_from_db(session, source_key: str) -> SourceMapping | None:
    """Lê o mapeamento da fonte em `etl_mapping` — a mesma tabela que o FOCUS usa.

    O de-para canônico vive no banco e é editável pela aba de relacionamento, em
    vez de só no código. `etl_mapping` não guarda receita de derivada, então as
    derivadas continuam vindo da declaração Python — mas as colunas e constantes
    passam a ser dados, não código.
    """
    from sqlalchemy import select

    from app.models.rules import EtlMapping

    declarado = SOURCE_MAPPINGS.get(source_key)
    if declarado is None:
        return None

    linhas = session.execute(
        select(EtlMapping)
        .where(EtlMapping.source_key == source_key, EtlMapping.active.is_(True))
        .order_by(EtlMapping.priority)
    ).scalars().all()
    if not linhas:
        return None

    colunas: dict[str, str] = {}
    constantes: dict[str, Any] = {}
    for linha in linhas:
        destino = str(linha.target_column or "")
        if not destino:
            continue
        if linha.transform_type == "CONSTANT":
            constantes[destino] = (linha.transform_config or {}).get("value")
        else:
            origem = str(linha.source_column or "")
            if origem:
                colunas[origem] = destino

    return SourceMapping(
        source_key=declarado.source_key,
        model_name=declarado.model_name,
        bronze_table=declarado.bronze_table,
        columns=colunas or declarado.columns,
        constants=constantes or declarado.constants,
        # Derivadas só existem na declaração — o banco não as representa ainda.
        derived=declarado.derived,
        measure_priority=declarado.measure_priority,
        period_field=declarado.period_field,
        notes=declarado.notes,
    )


def register(mapping: SourceMapping) -> None:
    """Declara (ou substitui) o mapeamento de uma fonte."""
    SOURCE_MAPPINGS[mapping.source_key] = mapping


def mapping_for(source_key: str) -> SourceMapping | None:
    return SOURCE_MAPPINGS.get(source_key)


def mapped_sources() -> list[str]:
    return sorted(SOURCE_MAPPINGS)


def bronze_table_name(mapping: SourceMapping, environment: str | None) -> str:
    """Resolve `{env}` no padrão da tabela Bronze."""
    env = (environment or "").strip() if isinstance(environment, str) else ""
    if "{env}" not in mapping.bronze_table:
        return mapping.bronze_table
    if not env:
        raise ValueError(
            f"A Bronze de '{mapping.source_key}' depende do ambiente; informe `environment`."
        )
    return mapping.bronze_table.replace("{env}", env)


def _resolve_measure(frame: pd.DataFrame, prioridade: tuple[str, ...], alvo: str) -> None:
    """Preenche o valor canônico com a primeira medida disponível."""
    frame[alvo] = None
    for candidato in prioridade:
        if candidato not in frame.columns:
            continue
        faltante = frame[alvo].isna()
        if not faltante.any():
            return
        frame.loc[faltante, alvo] = frame.loc[faltante, candidato]


def normalise(frame: pd.DataFrame, mapping: SourceMapping, silver_model: SilverModel) -> pd.DataFrame:
    """Aplica o mapeamento e devolve o quadro no esquema do modelo.

    Regras: nenhum campo obrigatório pode faltar; medida ausente continua nula
    (nunca zero); timestamp vai para microssegundos, que é o que o Iceberg aceita.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=silver_model.column_names())

    out = pd.DataFrame(index=frame.index)
    # A origem nomeia as colunas de formas diferentes conforme o caminho de
    # leitura (o PyIceberg devolve `invoiceId`, o Trino `invoiceid`). O
    # mapeamento é declarado em minúsculas e este índice resolve o nome real,
    # para o de-para não depender de capitalização.
    real: dict[str, str] = {}
    for coluna in frame.columns:
        real.setdefault(str(coluna).lower(), coluna)

    def coluna(nome: str):
        return real.get(str(nome).lower())

    for destino, valor in mapping.constants.items():
        if silver_model.field(destino) is None:
            raise ValueError(f"Constante '{destino}' não existe no modelo {silver_model.name}")
        out[destino] = valor

    # `_billing_period` é coluna de sistema (partição lógica), não do modelo:
    # aparece em todo modelo e é derivada na origem (a data é diária, o custo é
    # mensal).
    reservadas = {"_billing_period", "periodo_uso", "resource_id_min"}
    for destino, receita in mapping.derived.items():
        campo = silver_model.field(destino)
        if campo is None and destino not in reservadas:
            raise ValueError(f"Derivada '{destino}' não existe no modelo {silver_model.name}")
        # Receita: `funcao:coluna` ou `funcao:coluna:parametro`.
        partes = receita.split(":")
        funcao = partes[0]
        if funcao == "coalesce":
            # A coluna de referência é uma lista separada por `+`.
            out[destino] = _derivada_coalesce(frame, partes[1].split("+"), coluna)
            continue
        if funcao == "coalesce-null":
            # Igual ao coalesce, mas SEM identidade sintética: atributo
            # desconhecido permanece NULL, como o plano exige ("jamais vira zero
            # ou valor fake").
            out[destino] = _derivada_coalesce_null(frame, partes[1].split("+"), coluna)
            continue
        if funcao == "menos":
            # Duas colunas separadas por `+`; a segunda é subtraída da primeira.
            out[destino] = _derivada_menos(frame, partes[1].split("+"), coluna)
            continue
        real_origem = coluna(partes[1])
        if real_origem is None:
            out[destino] = None
            continue
        valores = frame[real_origem]
        if funcao == "mes":
            out[destino] = _derivada_mes(valores)
        elif funcao == "minusculo":
            out[destino] = _derivada_minusculo(valores)
        elif funcao == "trecho":
            out[destino] = _derivada_trecho(valores, int(partes[2]) if len(partes) > 2 else 60)
        else:
            raise ValueError(f"Derivada desconhecida '{funcao}' em '{mapping.source_key}'")

    for origem, destino in mapping.columns.items():
        campo = silver_model.field(destino)
        if campo is None:
            raise ValueError(
                f"Mapeamento de '{mapping.source_key}': coluna '{destino}' não existe "
                f"no modelo {silver_model.name}"
            )
        if destino in out.columns:
            continue
        real_origem = coluna(origem)
        if real_origem is None:
            out[destino] = None
            continue
        valores = frame[real_origem]
        out[destino] = pd.to_numeric(valores, errors="coerce") if campo.type in _NUMERIC else valores.astype("string")

    # Campos do modelo não cobertos ficam nulos (exceto os obrigatórios).
    for campo in silver_model.fields:
        if campo.name not in out.columns:
            if campo.required:
                raise ValueError(
                    f"Campo obrigatório '{campo.name}' do modelo {silver_model.name} "
                    f"não foi mapeado pela fonte '{mapping.source_key}'"
                )
            out[campo.name] = None

    # Uniformiza a identidade de recurso: IDs de nuvem são case-insensitive e
    # as fontes não concordam na capitalização.
    if "resource_id" in out.columns:
        out["resource_id"] = out["resource_id"].astype("string").str.lower()

    if silver_model.primary_measure and mapping.measure_priority:
        _resolve_measure(out, mapping.measure_priority, silver_model.primary_measure)

    if silver_model.time_field:
        out[silver_model.time_field] = (
            pd.to_datetime(out[silver_model.time_field], errors="coerce", utc=True)
            .dt.tz_localize(None)
            .astype("datetime64[us]")
        )

    out["_silver_ingested_at"] = datetime.now(timezone.utc).isoformat()
    # Ordem do modelo, para a tabela ficar estável entre fontes. As colunas de
    # sistema (`_billing_period`, partição lógica) são preservadas: não fazem
    # parte do modelo, mas o `write_silver` depende delas.
    colunas_finais = silver_model.column_names()
    for reservada in ("_billing_period",):
        if reservada in out.columns and reservada not in colunas_finais:
            colunas_finais = colunas_finais + [reservada]
    out = out.reindex(columns=colunas_finais).reset_index(drop=True)
    # Tipo explícito por coluna: a mesma tabela recebe várias fontes, e o tipo
    # não pode depender de qual delas escreveu primeiro. Inteiro vira Int64 (não
    # float) para o Iceberg não gravar double onde o modelo diz bigint.
    for campo in silver_model.fields:
        if campo.name not in out.columns:
            continue
        if campo.type in ("bigint", "long"):
            out[campo.name] = pd.to_numeric(out[campo.name], errors="coerce").astype("Int64")
        elif campo.type in ("decimal", "double"):
            out[campo.name] = pd.to_numeric(out[campo.name], errors="coerce").astype("float64")
        elif campo.type == "boolean":
            out[campo.name] = out[campo.name].astype("boolean")

    # Ordem final = ordem declarada no modelo. O writer apenas **anexa**
    # `_billing_period` e `_processed_at`, entao a ordem do frame determina o
    # schema fisico: divergir aqui faz a proxima gravacao falhar com
    # "Mismatch in fields". Uma convencao, aplicada num lugar so.
    declaradas = [c for c in silver_model.column_names() if c in out.columns]
    extras = [c for c in out.columns if c not in declaradas]
    return out[declaradas + extras]


def mapping_rows(mapping: SourceMapping, silver_model: SilverModel) -> list[dict[str, Any]]:
    """Correlacionamento Bronze → Silver, para a tabela de auditoria."""
    invertido = {v: k for k, v in mapping.columns.items()}
    linhas: list[dict[str, Any]] = []
    for campo in silver_model.fields:
        bruto = invertido.get(campo.name, "")
        constante = campo.name in mapping.constants
        linhas.append({
            "source_key": mapping.source_key,
            "bronze_table": mapping.bronze_table,
            "silver_table": silver_model.table,
            "bronze_field": bruto or ("<constante>" if constante else ""),
            "silver_field": campo.name,
            "silver_type": campo.type,
            "role": campo.role,
            "required": campo.required,
            "unit": campo.unit,
            "notes": campo.notes or (
                f"Constante: {mapping.constants[campo.name]}" if constante else None
            ),
        })
    return linhas


def _resolver_conexao(session, tabela_bronze: str):
    """Conexão que originou a Bronze.

    O nome da tabela carrega o código da conexão (`raw_azure_4p`), então a
    origem é resolvida a partir dele — sem parâmetro novo em cada chamada.
    """
    from sqlalchemy import select

    from app.models.source_connection import SourceConnection

    if not tabela_bronze.startswith("raw_"):
        return None
    corpo = tabela_bronze[len("raw_"):]
    try:
        conexoes = session.execute(select(SourceConnection)).scalars().all()
    except Exception:  # noqa: BLE001
        return None
    # O código mais longo que casa vence: `azure-4p` antes de `azure`.
    candidatas = [
        c for c in conexoes
        if corpo == c.code.replace("-", "_") or corpo.startswith(c.code.replace("-", "_") + "_")
    ]
    if not candidatas:
        return None
    return max(candidatas, key=lambda c: len(c.code))


def materialise(
    session,
    *,
    source_key: str,
    environment: str | None = None,
    bronze_table: str | None = None,
    periods: list[str] | None = None,
) -> dict[str, Any]:
    """Normaliza a Bronze de uma fonte para o seu modelo Silver.

    Idempotente por período: reprocessar o mesmo dia ou mês substitui a fatia.
    """
    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers
    from app.services.silver_models import model as _model

    # O de-para vem do banco (mesma tabela do FOCUS) quando existir; a
    # declaração em código é o fallback e a fonte das derivadas.
    mapping = load_mapping_from_db(session, source_key) or mapping_for(source_key)
    if mapping is None:
        raise KeyError(
            f"Fonte '{source_key}' não declara mapeamento. "
            f"Fontes com modelo: {', '.join(mapped_sources())}"
        )
    silver_model = _model(mapping.model_name)
    tabela_bronze = bronze_table or bronze_table_name(mapping, environment)

    if periods is not None and not periods:
        return {"source_key": source_key, "model": silver_model.name,
                "silver_table": silver_model.table, "rows_silver": 0, "periods": []}

    try:
        table = iceberg._get_catalog().load_table((layers.BRONZE, tabela_bronze))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Bronze {tabela_bronze} ilegível: {exc}") from exc

    # Backfills de métricas podem conter milhões de linhas. Ler/normalizar um
    # dia de cada vez limita a memória e deixa cada fatia Silver persistida.
    selected_periods = list(dict.fromkeys(map(str, periods))) if periods is not None else [None]
    _conexao = _resolver_conexao(session, tabela_bronze)
    periodo_col = mapping.period_field
    written, written_periods, with_value, without_value = 0, [], 0, 0
    for selected_period in selected_periods:
        try:
            if selected_period is None:
                bronze = table.scan().to_pandas()
            else:
                from pyiceberg.expressions import EqualTo
                bronze = table.scan(row_filter=EqualTo("_billing_period", selected_period)).to_pandas()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Bronze {tabela_bronze} ilegível em {selected_period}: {exc}") from exc
        if bronze.empty:
            continue

        frame = normalise(bronze, mapping, silver_model)
        frame["_silver_owner"] = source_key
        # A conexão faz parte da identidade, pois a Silver aceita várias origens.
        frame["_source_connection_id"] = pd.array(
            [_conexao.id if _conexao else None] * len(frame), dtype="Int64",
        )
        frame["_source_connection_code"] = _conexao.code if _conexao else None
        frame["_source_connection_name"] = tabela_bronze
        if periodo_col not in frame.columns:
            raise ValueError(f"Período '{periodo_col}' ausente no modelo {silver_model.name}")
        frame[periodo_col] = frame[periodo_col].fillna("").astype(str)
        if silver_model.primary_measure and silver_model.primary_measure in frame.columns:
            with_value += int(frame[silver_model.primary_measure].notna().sum())
            without_value += int(frame[silver_model.primary_measure].isna().sum())
        for periodo, chunk in frame.groupby(periodo_col, dropna=False):
            chave = str(periodo or "")
            if not chave:
                continue
            _delete_period(silver_model.table, chave, owner=source_key,
                           connection=tabela_bronze)
            written += int(iceberg.write_silver(
                connection_code=source_key,
                df=chunk.drop(columns=[periodo_col]),
                billing_period=chave,
                table_name_override=f"silver.{silver_model.table}",
            ) or 0)
            written_periods.append(chave)

    # O de-para vive em `etl_mapping`, editável na aba de relacionamento —
    # não há mais registro paralelo a fazer aqui.

    resultado = {
        "source_key": source_key,
        "model": silver_model.name,
        "silver_table": silver_model.table,
        "bronze_table": tabela_bronze,
        "rows_silver": written,
        "periods": sorted(set(written_periods)),
    }
    if not written_periods:
        resultado["message"] = "Bronze vazia"
    if silver_model.primary_measure:
        resultado["rows_with_value"] = with_value
        resultado["rows_without_value"] = without_value
    return resultado


def _delete_period(
    table: str, period: str, *, owner: str | None = None, connection: str | None = None
) -> None:
    """Remove a fatia **daquela fonte** antes de reescrever.

    `write_silver` faz append. Como a tabela canônica recebe várias fontes (o
    inventário vem de VM e de Resource Graph), remover só pelo período apagaria
    as linhas da outra fonte — foi o que aconteceu: o Resource Graph zerou as
    304 linhas de VM.
    """
    from pyiceberg.expressions import And, EqualTo

    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers

    try:
        tabela = iceberg._get_catalog().load_table((layers.SILVER, table))
        filtro = EqualTo("_billing_period", period)
        if owner:
            filtro = And(filtro, EqualTo("_silver_owner", owner))
        if connection:
            # A conexão faz parte do escopo: a mesma fonte coletada de duas
            # conexões não pode se apagar mutuamente.
            filtro = And(filtro, EqualTo("_source_connection_name", connection))
        tabela.delete(filtro)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Silver %s: nada a remover em %s/%s (%s)", table, period, owner, type(exc).__name__)
