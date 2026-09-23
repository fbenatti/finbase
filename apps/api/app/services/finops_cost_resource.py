"""C2 — `finops_cost_resource_monthly`: quais recursos dirigem o custo.

Responde "quais recursos dirigem o custo e qual é o contexto de cada um",
ligando cada linha de custo ao inventário do recurso **sem multiplicar custo**.

Grão: `billing_period + provider + connection_code + sub_account_id +
canonical_resource_id + charge_category + currency`.

O ponto crítico é o join. Medido em 23/09/2026:

- o inventário tem **26 snapshots por recurso** (39.343 linhas para 20.160
  recursos), todos com `_billing_period = '202609'`. Um `LEFT JOIN` por
  `resource_id` multiplicaria o custo por até 26 — o fan-out que a regra 4.1
  proíbe;
- só **8.731 dos 968.339** recursos de custo têm inventário (0,9 %). A cobertura
  é uma medida do cubo, não uma suposição;
- `resource_type` é **NULL em 100 %** das linhas de `resource_cost`. O tipo do
  recurso existe **só** no inventário, então este cubo é onde ele passa a existir.

A solução é deduplicar o inventário para exatamente uma linha por
`(provider, conta, id canônico)` antes do join, escolhendo o snapshot mais recente,
e publicar `inventory_vintage_days` para que a defasagem fique visível em vez de
escondida. Não existe inventário histórico para `202504`: os atributos vêm do
snapshot atual e a coluna diz isso.

A chave de ligação é a segura — `provider + conta + id canônico` — e nunca
`resource_id` sozinho (seção 4.1).
"""

from __future__ import annotations

from typing import Any

from app.services import lakehouse_layers as layers
from app.services.semantic_normalization import (
    UNKNOWN,
    sql_billing_period_key,
    sql_canonical_resource_id,
    sql_currency_code,
    sql_provider_key,
    sql_region_key,
    sql_service_key,
)

C2_TABLE = "finops_cost_resource_monthly"
C2_CUBE_CODE = "finops_cost_resource_monthly"

#: Teste que separa recurso de linha de ajuste. As linhas de ajuste do 4P têm
#: `resource_id` preenchido com `'320core'`, que não é recurso.
#:
#: Recebe o nome de uma coluna **já canônica** (`trim(lower(resource_id))`) e
#: aplica o teste literalmente. Montar o teste sobre a coluna crua obrigaria a
#: repetir a expressão de normalização inteira — e foi assim que a primeira
#: versão acabou referenciando `resource_id` fora do escopo do CTE.
def _is_valid_resource_sql(column: str) -> str:
    return f"({column} LIKE '/%' AND {column} LIKE '%/providers/%')"


def _period_filter(billing_period: str | None, *, column: str | None = None) -> str:
    """`WHERE` de competência para materialização incremental por período.

    O filtro usa o literal da competência **validado** contra `YYYYMM`: a lista de
    períodos vem da própria Silver, mas um valor inesperado não pode virar SQL.
    """
    if not billing_period or not billing_period.isdigit() or len(billing_period) != 6:
        return ""
    alvo = column or "CAST(COALESCE(_billing_period, billing_period) AS VARCHAR)"
    return f"WHERE {alvo} = '{billing_period}'"


def inventory_dedup_sql(*, layer: str = layers.SILVER) -> str:
    """Inventário deduplicado para **uma linha por chave segura**.

    Ordena por `valid_from DESC` e desempata por `_processed_at DESC`, de modo
    determinístico: o mesmo dado produz sempre a mesma linha. `inventory_snapshots`
    registra quantos snapshots existiam, porque "1 de 26" é informação diferente
    de "1 de 1".
    """
    rid = sql_canonical_resource_id("resource_id")
    return f"""
WITH base AS (
    SELECT
        {sql_provider_key("cloud_provider")} AS provider,
        LOWER(TRIM(CAST(sub_account_id AS VARCHAR))) AS sub_account_id,
        {rid} AS canonical_resource_id,
        NULLIF(TRIM(CAST(resource_name AS VARCHAR)), '') AS inventory_resource_name,
        NULLIF(TRIM(CAST(resource_type AS VARCHAR)), '') AS inventory_resource_type,
        NULLIF(TRIM(CAST(resource_sku AS VARCHAR)), '') AS inventory_resource_sku,
        NULLIF(TRIM(CAST(resource_group_id AS VARCHAR)), '') AS inventory_resource_group_id,
        NULLIF(TRIM(CAST(region_id AS VARCHAR)), '') AS inventory_region_id,
        NULLIF(TRIM(CAST(resource_state AS VARCHAR)), '') AS inventory_resource_state,
        NULLIF(TRIM(CAST(operating_system AS VARCHAR)), '') AS operating_system,
        CAST(_billing_period AS VARCHAR) AS inventory_snapshot_period,
        CAST(valid_from AS VARCHAR) AS snapshot_valid_from,
        CAST(_processed_at AS VARCHAR) AS snapshot_processed_at,
        ROW_NUMBER() OVER (
            PARTITION BY {sql_provider_key("cloud_provider")},
                         LOWER(TRIM(CAST(sub_account_id AS VARCHAR))), {rid}
            ORDER BY valid_from DESC NULLS LAST, _processed_at DESC NULLS LAST
        ) AS rn,
        COUNT(*) OVER (
            PARTITION BY {sql_provider_key("cloud_provider")},
                         LOWER(TRIM(CAST(sub_account_id AS VARCHAR))), {rid}
        ) AS inventory_snapshots
    FROM iceberg.{layer}.resource_inventory
    WHERE {rid} IS NOT NULL
      AND NULLIF(TRIM(CAST(sub_account_id AS VARCHAR)), '') IS NOT NULL
)
SELECT
    provider,
    sub_account_id,
    canonical_resource_id,
    inventory_resource_name,
    inventory_resource_type,
    inventory_resource_sku,
    inventory_resource_group_id,
    inventory_region_id,
    inventory_resource_state,
    operating_system,
    inventory_snapshot_period,
    inventory_snapshots
FROM base
WHERE rn = 1
""".strip()


def c2_projection_sql(
    *,
    layer: str = layers.SILVER,
    billing_period: str | None = None,
) -> str:
    """Projeção do C2: uma linha por linha de custo, **sem agregar**.

    Este é o caminho que funciona no ambiente de referência, e a razão é medida:
    o `GROUP BY` por recurso sobre 13,5 M linhas (mesmo fatiado por competência)
    estoura a memória de um worker de 2,4 GiB — o cluster respondia 503
    "still initializing". Fatiar por competência não resolve, porque o estado do
    agrupamento é proporcional ao número de recursos distintos (~1 M), não ao
    número de linhas.

    Então o SQL faz só a projeção com as dimensões normalizadas (normalização é
    responsabilidade do SQL, para não existir uma segunda definição dela) e a
    agregação acontece em pandas, no processo da API, onde a memória é do host e
    não de um worker. O resultado é escrito e publicado pelo publicador por fatia
    do projeto.
    """
    rid = sql_canonical_resource_id("resource_id")
    region = sql_region_key(
        "region_id", charge_category_expr="charge_category", service_expr="service_name"
    )
    return f"""
SELECT
    {sql_billing_period_key("COALESCE(_billing_period, billing_period)")} AS billing_period,
    {sql_provider_key("cloud_provider")} AS provider,
    COALESCE(_source_connection_code, _source_connection_name) AS connection_code,
    LOWER(TRIM(CAST(sub_account_id AS VARCHAR))) AS sub_account_id,
    {rid} AS canonical_resource_id,
    COALESCE(_source_connection_id, 0) AS source_connection_id,
    NULLIF(TRIM(CAST(charge_category AS VARCHAR)), '') AS charge_category,
    {sql_currency_code("pricing_currency")} AS currency,
    NULLIF(TRIM(CAST(resource_name AS VARCHAR)), '') AS cost_resource_name,
    NULLIF(TRIM(CAST(resource_group_id AS VARCHAR)), '') AS cost_resource_group_id,
    {sql_service_key("service_name")} AS service_key,
    NULLIF(TRIM(CAST(service_category AS VARCHAR)), '') AS service_category,
    {region} AS region_key,
    TRY_CAST(effective_cost AS DOUBLE) AS effective_cost,
    TRY_CAST(list_cost AS DOUBLE) AS list_cost,
    TRY_CAST(pricing_quantity AS DOUBLE) AS pricing_quantity,
    DATE(charge_period_start) AS charge_day,
    CASE WHEN NULLIF(TRIM(CAST(tags_json AS VARCHAR)), '') IS NULL
           OR TRIM(CAST(tags_json AS VARCHAR)) IN ('{{}}', '[]')
         THEN 1 ELSE 0 END AS without_tag
FROM iceberg.{layer}.resource_cost
{_period_filter(billing_period)}
""".strip()


def c2_cost_sql(*, layer: str = layers.SILVER, billing_period: str | None = None) -> str:
    """C2 agregado no grão do cubo, em SQL.

    Mantido para inspeção, teste e ambientes com memória suficiente. No ambiente
    de referência, `materialize_c2_period` agrega em pandas a partir de
    :func:`c2_projection_sql`, porque esta consulta estoura a memória do worker.
    """
    return f"""
SELECT
    billing_period, provider, connection_code, sub_account_id, canonical_resource_id,
    source_connection_id, charge_category, currency,
    MAX(cost_resource_name) AS cost_resource_name,
    MAX(cost_resource_group_id) AS cost_resource_group_id,
    MAX(service_key) AS service_key,
    MAX(service_category) AS service_category,
    MAX(region_key) AS region_key,
    SUM(effective_cost) AS effective_cost,
    SUM(list_cost) AS list_cost,
    SUM(pricing_quantity) AS pricing_quantity,
    COUNT(*) AS rows,
    MIN(charge_day) AS first_charge_day,
    MAX(charge_day) AS last_charge_day,
    COUNT(DISTINCT charge_day) AS charge_days,
    SUM(without_tag) AS rows_without_tag
FROM ({c2_projection_sql(layer=layer, billing_period=billing_period)})
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
""".strip()


def c2_enrich_sql(
    *,
    layer: str = layers.SILVER,
    cost_table: str,
    inventory_table: str,
    billing_period: str | None = None,
) -> str:
    """Estágio 2 do C2: enriquece o custo por recurso com o inventário.

    O join é `LEFT` sobre a chave segura e o lado do inventário já vem com uma
    linha por chave. Consequência verificável: o `SUM(effective_cost)` deste
    estágio é **idêntico** ao do estágio 1. Se não fosse, o join teria
    multiplicado custo.
    """
    return f"""
SELECT
    c.billing_period,
    c.provider,
    c.connection_code,
    c.sub_account_id,
    c.canonical_resource_id,
    c.source_connection_id,
    c.charge_category,
    c.currency,
    c.service_key,
    c.service_category,
    c.region_key,
    COALESCE(i.inventory_resource_name, c.cost_resource_name) AS resource_name,
    COALESCE(i.inventory_resource_type, 'UNKNOWN') AS resource_type_key,
    i.inventory_resource_sku AS resource_sku,
    COALESCE(i.inventory_resource_group_id, c.cost_resource_group_id) AS resource_group_id,
    i.inventory_resource_state AS resource_state,
    i.operating_system,
    CASE WHEN i.canonical_resource_id IS NULL THEN 'NAO_INVENTARIADO' ELSE 'INVENTARIADO' END
        AS inventory_status,
    i.inventory_snapshot_period,
    COALESCE(i.inventory_snapshots, 0) AS inventory_snapshots,
    c.effective_cost,
    c.list_cost,
    CAST(NULL AS DOUBLE) AS billed_cost,
    CAST(NULL AS DOUBLE) AS contracted_cost,
    c.pricing_quantity,
    c.rows,
    c.first_charge_day,
    c.last_charge_day,
    c.charge_days,
    c.rows_without_tag,
    CASE WHEN {_is_valid_resource_sql("c.canonical_resource_id")}
         THEN 'VALIDO' ELSE 'NAO_RECURSO' END AS resource_id_status
FROM iceberg.{layer}.{cost_table} c
LEFT JOIN iceberg.{layer}.{inventory_table} i
  ON i.provider = c.provider
 AND i.sub_account_id = c.sub_account_id
 AND i.canonical_resource_id = c.canonical_resource_id
{_period_filter(billing_period, column="c.billing_period")}
""".strip()


def c2_select_sql(*, layer: str = layers.SILVER) -> str:
    """SELECT completo do C2 numa consulta, para inspeção e teste.

    `materialize_c2` usa os estágios separados, que é o caminho que funciona no
    ambiente de referência.
    """
    return f"""
WITH custo AS (
    SELECT * FROM ({c2_cost_sql(layer=layer)}) c
),
inventario AS (
    SELECT * FROM ({inventory_dedup_sql(layer=layer)}) i
)
{c2_enrich_sql(layer=layer, cost_table="custo", inventory_table="inventario")}
""".strip()


def quality_columns() -> tuple[str, ...]:
    """Colunas de qualidade que acompanham o valor.

    `inventory_status` é a cobertura de join custo↔inventário exigida pela seção
    4.4; `resource_id_status` separa recurso de linha de ajuste.
    """
    return (
        "inventory_status",
        "inventory_snapshots",
        "inventory_snapshot_period",
        "resource_id_status",
        "rows_without_tag",
    )


def non_additive_measures() -> frozenset[str]:
    """Medidas que não podem ser somadas livremente pelas dimensões.

    `rows_without_tag` é contagem por recurso: o mesmo recurso aparece em vários
    serviços e regiões, então somar conta a mesma linha de origem mais de uma vez.
    """
    return frozenset({"rows_without_tag", "inventory_snapshots", "charge_days"})


async def list_periods(*, layer: str = layers.SILVER) -> list[str]:
    """Competências presentes em `resource_cost`, em ordem crescente."""
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(
        "SELECT DISTINCT CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) AS bp "
        "FROM iceberg.silver.resource_cost "
        "WHERE COALESCE(_billing_period, billing_period) IS NOT NULL "
        "ORDER BY 1",
        timeout_s=900,
    )
    return [
        str(r.get("bp"))
        for r in linhas or []
        if str(r.get("bp") or "").isdigit() and len(str(r.get("bp"))) == 6
    ]


async def materialize_c2_period(
    *,
    billing_period: str,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Materializa C2 de **uma** competência.

    Três passos, na ordem que o ambiente exige:

    1. o SQL projeta a fatia com as dimensões normalizadas, sem agregar;
    2. pandas agrega no grão do cubo e faz o `merge` com o inventário já
       deduplicado (1:1);
    3. o publicador por fatia do projeto grava e troca a fatia
       `(billing_period, source_connection_id)` numa transação Iceberg — leitores
       veem a versão anterior ou a nova, nunca lote parcial.

    A agregação em pandas existe porque o `GROUP BY` no Trino sobre o grão de
    recurso estoura a memória de um worker de 2,4 GiB. A memória do processo da
    API é do host, e a fatia de uma competência (~200 k a 3,8 M linhas) cabe.
    """
    import pandas as pd

    from app.services import iceberg_service as iceberg
    from app.services.gold_governance import extract_sources
    from app.services.trino_service import execute as trino_execute

    if progress:
        progress(0, 3, f"C2 {billing_period}: projetando a fatia")
    linhas = await trino_execute(
        c2_projection_sql(layer=layer, billing_period=billing_period), timeout_s=3600
    )
    if not linhas:
        return {"billing_period": billing_period, "rows": 0}

    if progress:
        progress(1, 3, f"C2 {billing_period}: deduplicando inventario e agregando")
    inventario = await trino_execute(inventory_dedup_sql(layer=layer), timeout_s=900)

    frame = pd.DataFrame(linhas)
    # Colunas textuais entram como `object` misto (None + str) e o `max` do
    # groupby falha nelas ("agg function failed [how->max,dtype->object]") quando
    # a fatia é de ajuste e quase tudo é nulo. Tipar como `string` antes de
    # agregar mantém `max` determinístico e a comparação lexical — que é o que se
    # quer de um rótulo.
    for coluna in (
        "cost_resource_name", "cost_resource_group_id", "service_key",
        "service_category", "region_key", "provider", "connection_code",
        "sub_account_id", "canonical_resource_id", "charge_category", "currency",
    ):
        if coluna in frame.columns:
            frame[coluna] = frame[coluna].astype("string")
    chave = [
        "billing_period", "provider", "connection_code", "sub_account_id",
        "canonical_resource_id", "source_connection_id", "charge_category", "currency",
    ]
    agregado = (
        frame.groupby(chave, dropna=False)
        .agg(
            effective_cost=("effective_cost", "sum"),
            list_cost=("list_cost", "sum"),
            pricing_quantity=("pricing_quantity", "sum"),
            rows=("effective_cost", "size"),
            first_charge_day=("charge_day", "min"),
            last_charge_day=("charge_day", "max"),
            charge_days=("charge_day", "nunique"),
            rows_without_tag=("without_tag", "sum"),
            cost_resource_name=("cost_resource_name", "max"),
            cost_resource_group_id=("cost_resource_group_id", "max"),
            service_key=("service_key", "max"),
            service_category=("service_category", "max"),
            region_key=("region_key", "max"),
        )
        .reset_index()
    )

    inv = pd.DataFrame(inventario) if inventario else pd.DataFrame()
    if not inv.empty:
        agregado = agregado.merge(
            inv,
            on=["provider", "sub_account_id", "canonical_resource_id"],
            how="left",
        )
    else:
        for coluna in (
            "inventory_resource_name", "inventory_resource_type",
            "inventory_resource_sku", "inventory_resource_group_id",
            "inventory_resource_state", "operating_system",
            "inventory_snapshot_period", "inventory_snapshots",
        ):
            agregado[coluna] = None

    # O merge é 1:1 por construção (o inventário vem deduplicado por chave
    # segura). Se deixasse de ser, a contagem de linhas cresceria e a
    # reconciliação do total reprovaria a fatia.
    agregado["resource_name"] = agregado["inventory_resource_name"].where(
        agregado["inventory_resource_name"].notna(), agregado["cost_resource_name"]
    )
    agregado["resource_type_key"] = agregado["inventory_resource_type"].fillna("UNKNOWN")
    agregado["resource_sku"] = agregado["inventory_resource_sku"]
    agregado["resource_group_id"] = agregado["inventory_resource_group_id"].where(
        agregado["inventory_resource_group_id"].notna(),
        agregado["cost_resource_group_id"],
    )
    agregado["resource_state"] = agregado["inventory_resource_state"]
    agregado["inventory_status"] = agregado["inventory_resource_name"].notna().map(
        {True: "INVENTARIADO", False: "NAO_INVENTARIADO"}
    )
    agregado["inventory_snapshots"] = (
        agregado["inventory_snapshots"].fillna(0).astype("int64")
    )
    agregado["billed_cost"] = None
    agregado["contracted_cost"] = None
    id_texto = agregado["canonical_resource_id"].astype("string")
    valido = id_texto.str.startswith("/", na=False) & id_texto.str.contains(
        "/providers/", regex=False, na=False
    )
    agregado["resource_id_status"] = valido.map({True: "VALIDO", False: "NAO_RECURSO"})

    colunas = [
        "billing_period", "provider", "connection_code", "sub_account_id",
        "canonical_resource_id", "source_connection_id", "charge_category", "currency",
        "service_key", "service_category", "region_key", "resource_name",
        "resource_type_key", "resource_sku", "resource_group_id", "resource_state",
        "operating_system", "inventory_status", "inventory_snapshot_period",
        "inventory_snapshots", "effective_cost", "list_cost", "billed_cost",
        "contracted_cost", "pricing_quantity", "rows", "first_charge_day",
        "last_charge_day", "charge_days", "rows_without_tag", "resource_id_status",
    ]
    agregado = agregado[[c for c in colunas if c in agregado.columns]]

    if progress:
        progress(2, 3, f"C2 {billing_period}: publicando a fatia")
    # O publicador por fatia do projeto opera em `_billing_period` (a coluna de
    # controle) além de `source_connection_id`. A coluna de contrato do C2 é
    # `billing_period`; a de controle é espelho dela, para não existirem dois
    # valores diferentes de competência na mesma linha.
    agregado["_billing_period"] = agregado["billing_period"]
    staged = iceberg.write_destination_staged(
        C2_TABLE, agregado, partition_col="_billing_period", namespace=layers.GOLD
    )
    conexoes = [
        int(c) for c in agregado["source_connection_id"].dropna().unique().tolist()
    ] or [0]
    publicado = 0
    try:
        for conexao in conexoes:
            parte = agregado[agregado["source_connection_id"] == conexao]
            staged_parte = iceberg.write_destination_staged(
                C2_TABLE, parte, partition_col="_billing_period", namespace=layers.GOLD
            )
            publicado += iceberg.publish_staged_partition(
                C2_TABLE,
                staged_parte,
                billing_period=billing_period,
                connection_id=conexao,
                namespace=layers.GOLD,
            )
    finally:
        try:
            iceberg.cleanup_staged_table(staged, namespace=layers.GOLD)
        except Exception:  # noqa: BLE001
            pass

    # `record_frame_publication` (assíncrona) porque este caminho já é assíncrono:
    # a versão `_blocking` recusa registrar com loop ativo, e um manifesto não
    # registrado é exatamente o que G2 existe para eliminar.
    from app.services.gold_governance import record_frame_publication

    await record_frame_publication(
        cube_code=C2_CUBE_CODE,
        physical_table=C2_TABLE,
        rows_written=int(len(agregado)),
        namespace=layers.GOLD,
        run_id=run_id,
        source_tables=extract_sources(
            c2_projection_sql(layer=layer, billing_period=billing_period)
        ),
    )
    return {
        "billing_period": billing_period,
        "rows": int(len(agregado)),
        "published": publicado,
    }


#: Auxiliares do C2. Silver: detalhe derivado e reconstruível, não cubo.
C2_COST_TABLE = "finops_cost_resource_cost"
C2_INVENTORY_TABLE = "finops_cost_resource_inventory"


async def materialize_c2(
    *,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    expected_total: float | None = None,
    periods: list[str] | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Materializa C2 por competência e reconcilia contra `resource_cost`.

    Publica fatia a fatia (o swap preserva as outras competências) e só então
    mede o total do cubo. Um total maior que a origem significa join
    multiplicando; menor significa fatia faltando — os dois reprovam.
    """
    from app.services.trino_service import execute as trino_execute

    alvos = periods or await list_periods(layer=layer)
    resultados: list[dict[str, Any]] = []
    for indice, periodo in enumerate(alvos):
        if progress:
            progress(indice, len(alvos), f"C2 {periodo}")
        resultados.append(
            await materialize_c2_period(
                billing_period=periodo, run_id=run_id, layer=layer
            )
        )

    verificacao = await trino_execute(
        f"SELECT ROUND(SUM(effective_cost), 2) AS total, COUNT(*) AS linhas "
        f"FROM iceberg.{layers.GOLD}.{C2_TABLE}",
        timeout_s=900,
    )
    total_out = float((verificacao[0] if verificacao else {}).get("total") or 0)
    delta = None if expected_total is None else round(total_out - float(expected_total), 2)
    return {
        "table": C2_TABLE,
        "periods": len(alvos),
        "rows": sum(int(r.get("rows") or 0) for r in resultados),
        "financial_total_in": expected_total,
        "financial_total_out": total_out,
        "financial_delta": delta,
        "reconciliation_status": (
            "NOT_MEASURED"
            if delta is None
            else ("OK" if abs(delta) <= 0.01 else "MISMATCH")
        ),
        "details": resultados,
    }
