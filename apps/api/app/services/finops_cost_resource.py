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

import time
from datetime import date, datetime

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


#: Número de buckets por competência × conexão na construção do C2.
#:
#: A agregação roda no Trino (a API não recebe linhas Silver), mas o estado do
#: `GROUP BY` por recurso ainda precisa caber num worker de 2,4 GiB. Medido: o
#: agrupamento de uma competência inteira (~800 k linhas, ~700 k recursos) estoura.
#: Com 64 buckets, a fatia mais pesada (202504, 3,76 M linhas) agrupa ~59 k linhas
#: por consulta, que cabe com folga. O número foi escolhido por medição: 16 buckets
#: ainda derrubavam o worker nessa fatia.
#:
#: O bucket é `ABS(CRC32(COALESCE(id canônico, ''))) % N`, então o **mesmo recurso cai
#: sempre no mesmo bucket**: uma chave, um bucket, nunca dois, e a soma dos buckets
#: é o total da fatia.
DEFAULT_BUCKETS = 64

#: Buckets usados por bucket. O `CRC32` do Trino opera em `VARBINARY`; passar
#: `VARCHAR` devolve vazio silenciosamente, e um predicado vazio publicaria zero
#: linhas sem erro.
#: `COALESCE(coluna, '')` é obrigatório, não cosmético: `CRC32(NULL)` é NULL, e
#: `NULL = n` é falso, então as linhas de ajuste (412.665 delas, com `resource_id`
#: não nulo mas sem conta) sumiriam da publicação e a reconciliação acusaria falta
#: de custo. Com o coalesce, toda linha cai em exatamente um bucket.
_BUCKET_EXPR = (
    "ABS(CRC32(CAST(COALESCE({column}, '') AS VARBINARY))) % {buckets}"
)


def bucket_predicate(column: str, bucket: int, *, buckets: int = DEFAULT_BUCKETS) -> str:
    """Predicado do bucket de um recurso, determinístico pela chave canônica."""
    if buckets <= 0:
        raise ValueError("buckets precisa ser positivo")
    if not 0 <= bucket < buckets:
        raise ValueError(f"bucket {bucket} fora do intervalo 0..{buckets - 1}")
    expr = _BUCKET_EXPR.format(column=column, buckets=int(buckets))
    return f"{expr} = {int(bucket)}"


def bucket_count(*, buckets: int = DEFAULT_BUCKETS) -> int:
    """Quantidade de buckets validada."""
    if buckets <= 0:
        raise ValueError("buckets precisa ser positivo")
    return int(buckets)


def _period_filter(billing_period: str | None, *, column: str | None = None) -> str:
    """`WHERE` de competência para materialização incremental por período.

    O filtro usa o literal da competência **validado** contra `YYYYMM`: a lista de
    períodos vem da própria Silver, mas um valor inesperado não pode virar SQL.
    """
    if not billing_period or not billing_period.isdigit() or len(billing_period) != 6:
        return ""
    alvo = column or "CAST(COALESCE(_billing_period, billing_period) AS VARCHAR)"
    return f"WHERE {alvo} = '{billing_period}'"


def _quote_literal(valor: Any) -> str:
    """Literal SQL a partir de valor controlado (competência, conexão).

    A competência e a conexão vêm da própria Silver, mas ainda passam por aqui: um
    valor inesperado não pode virar SQL.
    """
    return "'" + str(valor).replace("'", "''") + "'"


def _period_end_expr(period_column: str) -> str:
    """Último dia da competência, a partir de uma coluna `YYYYMM`.

    Construído com aritmética de data, sem `LAST_DAY`: o fim do mês é
    `primeiro_dia_do_mês_seguinte − 1 dia`, e a operação é determinística para
    qualquer formato `YYYYMM` válido.
    """
    ano = f"CAST(SUBSTR(CAST({period_column} AS VARCHAR), 1, 4) AS INTEGER)"
    mes = f"CAST(SUBSTR(CAST({period_column} AS VARCHAR), 5, 2) AS INTEGER)"
    return (
        f"DATE_ADD('day', -1, "
        f"DATE_ADD('month', 1, CAST(DATE(CONCAT(CAST({ano} AS VARCHAR), '-', "
        f"CAST({mes} AS VARCHAR), '-01')) AS DATE)))"
    )


def inventory_temporal_sql(*, layer: str = layers.SILVER) -> str:
    """Inventário deduplicado **por competência**, com vigência comprovada.

    O join temporal é o ponto da correção. Antes, o C2 aplicava o snapshot de
    setembro de 2026 a todas as competências: o tipo, o SKU, o estado e o SO de
    setembro eram carimbados em custo de 2025. Presente não é vigente.

    Regras:

    - aceita somente inventário com `valid_from <= fim da competência`. Um snapshot
      de setembro **não** descreve maio;
    - entre os válidos, escolhe o **mais próximo da competência** (maior
      `valid_from`), e não o mais recente em termos absolutos;
    - `inventory_status` recebe:
      - `CURRENT_SNAPSHOT` quando a competência e o snapshot são compatíveis
        (o snapshot vigente é o da própria competência);
      - `NO_TEMPORAL_SNAPSHOT` quando não existe snapshot válido para o período —
        e nesse caso os atributos históricos são `NULL`, sem retropolação.

    Como hoje só existem snapshots de 202609, todo período anterior fica
    `NO_TEMPORAL_SNAPSHOT` com atributos `NULL`. O grão é
    `provider + conta + id canônico + competência`, para que cada competência
    resolva o seu próprio snapshot.
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
        COUNT(*) OVER (
            PARTITION BY {sql_provider_key("cloud_provider")},
                         LOWER(TRIM(CAST(sub_account_id AS VARCHAR))), {rid}
        ) AS inventory_snapshots
    FROM iceberg.{layer}.resource_inventory
    WHERE {rid} IS NOT NULL
      AND NULLIF(TRIM(CAST(sub_account_id AS VARCHAR)), '') IS NOT NULL
      AND valid_from IS NOT NULL
),
competencias AS (
    SELECT DISTINCT
        CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) AS billing_period
    FROM iceberg.{layer}.resource_cost
    WHERE COALESCE(_billing_period, billing_period) IS NOT NULL
),
temporal AS (
    SELECT
        b.*,
        c.billing_period,
        ROW_NUMBER() OVER (
            PARTITION BY b.provider, b.sub_account_id, b.canonical_resource_id,
                         c.billing_period
            ORDER BY b.snapshot_valid_from DESC, b.snapshot_processed_at DESC
        ) AS rn
    FROM base b
    JOIN competencias c
      ON b.snapshot_valid_from <= CAST({_period_end_expr("c.billing_period")} AS VARCHAR)
)
SELECT
    provider,
    sub_account_id,
    canonical_resource_id,
    billing_period,
    inventory_resource_name,
    inventory_resource_type,
    inventory_resource_sku,
    inventory_resource_group_id,
    inventory_region_id,
    inventory_resource_state,
    operating_system,
    inventory_snapshot_period,
    inventory_snapshots,
    CASE
        WHEN inventory_snapshot_period = billing_period THEN 'CURRENT_SNAPSHOT'
        ELSE 'HISTORICAL_SNAPSHOT'
    END AS inventory_status
FROM temporal
WHERE rn = 1
""".strip()


#: Número de buckets por competência × conexão na construção do C2.
#:
#: A agregação roda no Trino (a API não recebe linhas Silver), mas o estado do
#: `GROUP BY` por recurso ainda precisa caber num worker de 2,4 GiB. Medido: o
#: agrupamento de uma competência inteira (~800 k linhas, ~700 k recursos) estoura.
#: Com 64 buckets, a fatia mais pesada (202504, 3,76 M linhas) agrupa ~59 k linhas
#: por consulta, que cabe com folga. O número foi escolhido por medição: 16 buckets
#: ainda derrubavam o worker nessa fatia.
#:
#: O bucket é `ABS(CRC32(COALESCE(id canônico, ''))) % N`, então o **mesmo recurso cai
#: sempre no mesmo bucket**: uma chave, um bucket, nunca dois, e a soma dos buckets
#: é o total da fatia.
DEFAULT_BUCKETS = 64

#: Buckets usados por bucket. O `CRC32` do Trino opera em `VARBINARY`; passar
#: `VARCHAR` devolve vazio silenciosamente, e um predicado vazio publicaria zero
#: linhas sem erro.
#: `COALESCE(coluna, '')` é obrigatório, não cosmético: `CRC32(NULL)` é NULL, e
#: `NULL = n` é falso, então as linhas de ajuste (412.665 delas, com `resource_id`
#: não nulo mas sem conta) sumiriam da publicação e a reconciliação acusaria falta
#: de custo. Com o coalesce, toda linha cai em exatamente um bucket.
_BUCKET_EXPR = (
    "ABS(CRC32(CAST(COALESCE({column}, '') AS VARBINARY))) % {buckets}"
)


def bucket_predicate(column: str, bucket: int, *, buckets: int = DEFAULT_BUCKETS) -> str:
    """Predicado do bucket de um recurso, determinístico pela chave canônica."""
    if buckets <= 0:
        raise ValueError("buckets precisa ser positivo")
    if not 0 <= bucket < buckets:
        raise ValueError(f"bucket {bucket} fora do intervalo 0..{buckets - 1}")
    expr = _BUCKET_EXPR.format(column=column, buckets=int(buckets))
    return f"{expr} = {int(bucket)}"


def bucket_count(*, buckets: int = DEFAULT_BUCKETS) -> int:
    """Quantidade de buckets validada."""
    if buckets <= 0:
        raise ValueError("buckets precisa ser positivo")
    return int(buckets)


def _period_filter(billing_period: str | None, *, column: str | None = None) -> str:
    """`WHERE` de competência para materialização incremental por período.

    O filtro usa o literal da competência **validado** contra `YYYYMM`: a lista de
    períodos vem da própria Silver, mas um valor inesperado não pode virar SQL.
    """
    if not billing_period or not billing_period.isdigit() or len(billing_period) != 6:
        return ""
    alvo = column or "CAST(COALESCE(_billing_period, billing_period) AS VARCHAR)"
    return f"WHERE {alvo} = '{billing_period}'"


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
    """Estágio 2 do C2: enriquece o custo por recurso com o inventário **vigente**.

    O `LEFT JOIN` casa por `provider + conta + id canônico + competência`, e o lado
    do inventário já vem com uma linha por combinação e com a vigência comprovada
    (`inventory_temporal_sql`).

    Duas consequências verificáveis:

    - o `SUM(effective_cost)` é **idêntico** ao do estágio 1 (o join não multiplica);
    - atributos de recurso só aparecem quando existe snapshot válido para a
      competência. Sem snapshot vigente, `resource_type_key` fica `UNKNOWN`,
      `resource_sku`/`resource_state`/`operating_system` ficam `NULL` e
      `inventory_status` fica `NO_TEMPORAL_SNAPSHOT`.
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
    -- Nome do recurso: o do inventário **quando vigente**; senão o do custo, que é
    -- atributo da própria linha de custo e não do snapshot.
    COALESCE(i.inventory_resource_name, c.cost_resource_name) AS resource_name,
    CASE
        WHEN i.canonical_resource_id IS NULL THEN 'UNKNOWN'
        ELSE COALESCE(i.inventory_resource_type, 'UNKNOWN')
    END AS resource_type_key,
    -- Atributos que só o inventário comprova: NULL quando não há snapshot vigente.
    i.inventory_resource_sku AS resource_sku,
    COALESCE(i.inventory_resource_group_id, c.cost_resource_group_id)
        AS resource_group_id,
    i.inventory_resource_state AS resource_state,
    i.operating_system,
    CASE
        WHEN i.canonical_resource_id IS NULL THEN 'NO_TEMPORAL_SNAPSHOT'
        ELSE i.inventory_status
    END AS inventory_status,
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
 AND i.billing_period = c.billing_period
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
    SELECT * FROM ({inventory_temporal_sql(layer=layer)}) i
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


def inventory_status_for(
    *, billing_period: str | None, snapshot_period: str | None
) -> str:
    """Status de vigência do snapshot para uma competência.

    Devolve a mesma decisão que o SQL do join temporal aplica, em Python, para que a
    regra seja testável sem depender do dado existente:

    - sem `billing_period` (nenhuma linha de competência) → não houve join;
    - sem `snapshot_period` válido → `NO_TEMPORAL_SNAPSHOT` (o caso dominante hoje:
      só existem snapshots de 202609, então todo período anterior cai aqui);
    - snapshot da mesma competência → `CURRENT_SNAPSHOT`;
    - snapshot válido e anterior → `HISTORICAL_SNAPSHOT`.
    """
    if not billing_period:
        return NO_TEMPORAL_SNAPSHOT
    if not snapshot_period:
        return NO_TEMPORAL_SNAPSHOT
    if str(snapshot_period) == str(billing_period):
        return CURRENT_SNAPSHOT
    return HISTORICAL_SNAPSHOT


def snapshot_is_valid_for_period(
    *, valid_from: Any, snapshot_period: str, billing_period: str
) -> bool:
    """``True`` quando o snapshot pode descrever a competência.

    A regra é `valid_from <= fim da competência`. Um snapshot de setembro **não**
    descreve maio, e é isso que impede a retropolação de tipo, SKU, estado e SO.
    """
    fim = _period_end_date(billing_period)
    if fim is None or not valid_from:
        return False
    try:
        if isinstance(valid_from, datetime):
            instante = valid_from.date()
        elif isinstance(valid_from, date):
            instante = valid_from
        else:
            instante = date.fromisoformat(str(valid_from)[:10])
    except ValueError:
        return False
    return instante <= fim


def _period_end_date(billing_period: str) -> date | None:
    """Último dia da competência, em Python. ``None`` quando inválida."""
    if not billing_period or not str(billing_period).isdigit() or len(str(billing_period)) != 6:
        return None
    ano, mes = int(str(billing_period)[:4]), int(str(billing_period)[4:6])
    if not 1 <= mes <= 12:
        return None
    proximo = date(ano + (1 if mes == 12 else 0), 1 if mes == 12 else mes + 1, 1)
    return date.fromordinal(proximo.toordinal() - 1)


#: Valores possíveis de `inventory_status`. Fechados e explícitos para que a API
#: não precise inferir o que cada string significa.
INVENTORY_STATUSES: tuple[str, ...] = (
    # O snapshot vigente é o da própria competência.
    "CURRENT_SNAPSHOT",
    # Existe snapshot válido (valid_from <= fim da competência) e anterior.
    "HISTORICAL_SNAPSHOT",
    # Não existe snapshot válido para a competência: atributos ficam NULL.
    "NO_TEMPORAL_SNAPSHOT",
)

CURRENT_SNAPSHOT = "CURRENT_SNAPSHOT"
HISTORICAL_SNAPSHOT = "HISTORICAL_SNAPSHOT"
NO_TEMPORAL_SNAPSHOT = "NO_TEMPORAL_SNAPSHOT"


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


#: Auxiliares do C2. Silver: detalhe derivado e reconstruível, não cubo.
C2_COST_TABLE = "finops_cost_resource_cost"
C2_INVENTORY_TABLE = "finops_cost_resource_inventory"

#: Prefixo das tabelas auxiliares de bucket. Silver: agregado intermediário,
#: removido no fim da fatia.
C2_BUCKET_PREFIX = "finops_cost_resource_bucket"

#: Colunas publicadas em `finops_cost_resource_monthly`, com o tipo **explícito**.
#:
#: O tipo é declarado, e não inferido: o C2 é construído por fatia, e a primeira
#: fatia de um período de ajuste infere tipos diferentes dos de um mês operacional
#: (`billed_cost` saiu `double` numa fatia e `bigint` na outra). Sem cast explícito,
#: o `INSERT` da fatia seguinte falha por tipo incompatível — foi o que aconteceu
#: entre 202512 e a fatia seguinte.
#:
#: `SELECT *` também não serve: um `UNION ALL` de tabelas de bucket herdaria
#: `build_bucket`, que é controle interno e não contrato.
_C2_OUTPUT_TYPES: tuple[tuple[str, str], ...] = (
    ("billing_period", "varchar"),
    ("provider", "varchar"),
    ("connection_code", "varchar"),
    ("sub_account_id", "varchar"),
    ("canonical_resource_id", "varchar"),
    ("source_connection_id", "bigint"),
    ("charge_category", "varchar"),
    ("currency", "varchar"),
    ("service_key", "varchar"),
    ("service_category", "varchar"),
    ("region_key", "varchar"),
    ("resource_name", "varchar"),
    ("resource_type_key", "varchar"),
    ("resource_sku", "varchar"),
    ("resource_group_id", "varchar"),
    ("resource_state", "varchar"),
    ("operating_system", "varchar"),
    ("inventory_status", "varchar"),
    ("inventory_snapshot_period", "varchar"),
    ("inventory_snapshots", "bigint"),
    ("effective_cost", "double"),
    ("list_cost", "double"),
    ("billed_cost", "double"),
    ("contracted_cost", "double"),
    ("pricing_quantity", "double"),
    ("rows", "bigint"),
    ("first_charge_day", "date"),
    ("last_charge_day", "date"),
    ("charge_days", "bigint"),
    ("rows_without_tag", "bigint"),
    ("resource_id_status", "varchar"),
)

#: Nomes das colunas de contrato, na ordem de publicação.
_C2_OUTPUT_COLUMNS: tuple[str, ...] = tuple(nome for nome, _ in _C2_OUTPUT_TYPES)


def c2_projection_sql(select_sql: str) -> str:
    """Projeta o resultado da montagem no contrato, com tipo explícito.

    É o que torna a publicação por fatia idempotente entre períodos de naturezas
    diferentes: mês operacional e mês de ajuste produzem o mesmo schema.
    """
    colunas = ", ".join(
        f"CAST({nome} AS {tipo}) AS {nome}" for nome, tipo in _C2_OUTPUT_TYPES
    )
    return f"SELECT {colunas} FROM ({select_sql}) c2_raw"


async def materialize_c2_inventory(
    *, run_id: int | None = None, layer: str = layers.SILVER
) -> int:
    """Publica o inventário temporal deduplicado por `(conexão, id, competência)`.

    Este é o único ponto do C2 que passa por frame, e o volume justifica: 20.160
    recursos avaliados contra 18 competências, dezenas de milhares de linhas — duas
    ordens de grandeza abaixo das 13,5 M linhas de custo que **não** passam mais
    pelo Python.
    """
    import pandas as pd

    from app.services import iceberg_service as iceberg
    from app.services.gold_governance import record_frame_publication
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(inventory_temporal_sql(layer=layer), timeout_s=1800)
    if not linhas:
        raise RuntimeError("inventario temporal nao produziu linhas")

    frame = pd.DataFrame(linhas)
    for coluna in (
        "provider", "sub_account_id", "canonical_resource_id", "billing_period",
        "inventory_resource_name", "inventory_resource_type", "inventory_resource_sku",
        "inventory_resource_group_id", "inventory_region_id", "inventory_resource_state",
        "operating_system", "inventory_snapshot_period", "inventory_status",
    ):
        if coluna in frame.columns:
            frame[coluna] = frame[coluna].astype("string")
    frame["inventory_snapshots"] = (
        pd.to_numeric(frame.get("inventory_snapshots"), errors="coerce")
        .fillna(0)
        .astype("int64")
    )
    frame["_billing_period"] = frame["billing_period"]

    staged = iceberg.write_destination_staged(
        C2_INVENTORY_TABLE, frame, partition_col="_billing_period", namespace=layer
    )
    try:
        publicado = iceberg.swap_staged_table(
            C2_INVENTORY_TABLE,
            staged,
            namespace=layer,
            known_row_count=int(len(frame)),
        )
    except Exception:
        try:
            iceberg.cleanup_staged_table(staged, namespace=layer)
        except Exception:  # noqa: BLE001
            pass
        raise

    await record_frame_publication(
        cube_code=C2_INVENTORY_TABLE,
        physical_table=C2_INVENTORY_TABLE,
        rows_written=int(len(frame)),
        namespace=layer,
        run_id=run_id,
        source_tables=[f"{layer}.resource_inventory"],
    )
    return int(publicado)


async def _publish_c2_slice(
    *,
    billing_period: str,
    connection_code: str,
    select_sql: str,
    run_id: int | None,
    layer: str,
) -> int:
    """Publica uma fatia `(competência, conexão)` de forma incremental.

    O C2 é construído por `(competência, conexão)`, então cada execução substitui
    **uma** fatia e precisa preservar as outras. `publish_gold_cube` faz swap de
    tabela inteira, o que apagaria as demais competências.

    A troca da fatia é feita em uma transação Iceberg do Trino
    (`DELETE` + `INSERT`), o mesmo mecanismo do publicador por fatia do projeto:
    leitores veem a fatia anterior ou a nova, nunca um lote parcial.

    A primeira publicação não tem fatia a substituir e cai em
    `publish_gold_cube`, que cria a tabela.
    """
    from app.services.gold_governance import (
        current_snapshot_id,
        extract_sources,
        publish_gold_cube,
        record_frame_publication,
    )
    from app.services.trino_service import execute as trino_execute

    staging = f"{C2_TABLE}_stg_{time.time_ns()}"
    existente = current_snapshot_id(layers.GOLD, C2_TABLE)
    if existente is None:
        publicacao = await publish_gold_cube(
            cube_code=C2_CUBE_CODE,
            table=C2_TABLE,
            select_sql=select_sql,
            namespace=layers.GOLD,
            partition_column="billing_period",
            run_id=run_id,
        )
        return publicacao.rows

    await trino_execute(
        f"CREATE TABLE iceberg.{layers.GOLD}.{staging} AS {select_sql}", timeout_s=3600
    )
    try:
        contagem = await trino_execute(
            f"SELECT COUNT(*) AS n FROM iceberg.{layers.GOLD}.{staging}", timeout_s=300
        )
        linhas = int((contagem[0] if contagem else {}).get("n") or 0)
        # Substituição da fatia em uma transação: o `DELETE` e o `INSERT` ficam no
        # mesmo commit, então a fatia nunca aparece pela metade.
        await trino_execute(
            f"DELETE FROM iceberg.{layers.GOLD}.{C2_TABLE} "
            f"WHERE billing_period = {_quote_literal(billing_period)} "
            f"AND connection_code = {_quote_literal(connection_code)}",
            timeout_s=600,
        )
        await trino_execute(
            f"INSERT INTO iceberg.{layers.GOLD}.{C2_TABLE} "
            f"SELECT * FROM iceberg.{layers.GOLD}.{staging}",
            timeout_s=3600,
        )
    finally:
        try:
            await trino_execute(
                f"DROP TABLE IF EXISTS iceberg.{layers.GOLD}.{staging}", timeout_s=300
            )
        except Exception:  # noqa: BLE001 — limpeza não é caminho crítico
            pass

    await record_frame_publication(
        cube_code=C2_CUBE_CODE,
        physical_table=C2_TABLE,
        rows_written=linhas,
        namespace=layers.GOLD,
        run_id=run_id,
        source_tables=extract_sources(select_sql),
    )
    return linhas


async def materialize_c2_slice(
    *,
    billing_period: str,
    connection_code: str,
    buckets: int = DEFAULT_BUCKETS,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    cleanup_buckets: bool = True,
) -> dict[str, Any]:
    """Constrói uma fatia `(competência, conexão)` bucketed e a publica.

    Cada bucket é agregado **no Trino** e materializado como tabela auxiliar; a
    fatia é montada por `UNION ALL` dessas tabelas. O processo da API transporta
    apenas o resultado agregado — nunca as 13,5 M linhas Silver.

    Buckets são removidos no `finally`, com sucesso ou falha: um bucket órfão
    sobrevivente entraria numa montagem futura e somaria custo de duas construções.
    """
    from app.services.gold_governance import materialize_gold_sql
    from app.services.trino_service import execute as trino_execute

    total_buckets = bucket_count(buckets=buckets)
    conexao_segura = "".join(ch if ch.isalnum() else "_" for ch in connection_code)
    criados: list[str] = []
    vazios = 0
    try:
        for bucket in range(total_buckets):
            tabela = f"{C2_BUCKET_PREFIX}_{conexao_segura}_{billing_period}_{bucket}"
            publicacao = await materialize_gold_sql(
                cube_code=f"{C2_CUBE_CODE}:{billing_period}:{conexao_segura}:{bucket}",
                table=tabela,
                select_sql=c2_bucket_select_sql(
                    layer=layer,
                    billing_period=billing_period,
                    bucket=bucket,
                    buckets=total_buckets,
                    connection_code=connection_code,
                ),
                namespace=layer,
                run_id=run_id,
            )
            if publicacao.rows:
                criados.append(tabela)
            else:
                vazios += 1

        if not criados:
            return {
                "billing_period": billing_period,
                "connection_code": connection_code,
                "rows": 0,
                "buckets": total_buckets,
                "empty_buckets": vazios,
            }

        uniao = "\nUNION ALL\n".join(
            f"SELECT * FROM iceberg.{layer}.{tabela}" for tabela in criados
        )
        projecao = c2_projection_sql(uniao)
        linhas_publicadas = await _publish_c2_slice(
            billing_period=billing_period,
            connection_code=connection_code,
            select_sql=projecao,
            run_id=run_id,
            layer=layer,
        )
        return {
            "billing_period": billing_period,
            "connection_code": connection_code,
            "rows": linhas_publicadas,
            "buckets": total_buckets,
            "empty_buckets": vazios,
            "source_buckets": len(criados),
        }
    finally:
        if cleanup_buckets:
            for bucket in range(total_buckets):
                tabela = f"{C2_BUCKET_PREFIX}_{conexao_segura}_{billing_period}_{bucket}"
                try:
                    await trino_execute(
                        f"DROP TABLE IF EXISTS iceberg.{layer}.{tabela}", timeout_s=300
                    )
                except Exception:  # noqa: BLE001 — limpeza não é caminho crítico
                    pass


async def materialize_c2(
    *,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    expected_total: float | None = None,
    periods: list[str] | None = None,
    buckets: int = DEFAULT_BUCKETS,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Materializa C2 por `(competência, conexão)`, com agregação no Trino.

    1. publica o inventário temporal deduplicado (único frame do C2);
    2. para cada `(competência, conexão)`, agrega por bucket no Trino;
    3. limpa os buckets, com sucesso ou falha;
    4. reconcilia o total publicado contra `resource_cost`.

    Um total maior que a origem significa join multiplicando; menor significa fatia
    faltando — os dois reprovam.
    """
    from app.services.trino_service import execute as trino_execute

    if progress:
        progress(0, 1, "C2: publicando inventario temporal")
    inventario_linhas = await materialize_c2_inventory(run_id=run_id, layer=layer)

    fatias = await list_period_connections(layer=layer)
    if periods:
        alvo = set(periods)
        fatias = [(bp, conn) for bp, conn in fatias if bp in alvo]

    detalhes: list[dict[str, Any]] = []
    for indice, (billing_period, connection_code) in enumerate(fatias, start=1):
        if progress:
            progress(indice, len(fatias), f"C2 {billing_period} {connection_code}")
        detalhes.append(
            await materialize_c2_slice(
                billing_period=billing_period,
                connection_code=connection_code,
                buckets=buckets,
                run_id=run_id,
                layer=layer,
            )
        )

    verificacao = await trino_execute(
        f"SELECT ROUND(SUM(effective_cost), 2) AS total, COUNT(*) AS linhas "
        f"FROM iceberg.{layers.GOLD}.{C2_TABLE}",
        timeout_s=1800,
    )
    total_out = float((verificacao[0] if verificacao else {}).get("total") or 0)
    delta = None if expected_total is None else round(total_out - float(expected_total), 2)
    return {
        "table": C2_TABLE,
        "slices": len(fatias),
        "inventory_rows": inventario_linhas,
        "rows": sum(int(r.get("rows") or 0) for r in detalhes),
        "financial_total_in": expected_total,
        "financial_total_out": total_out,
        "financial_delta": delta,
        "reconciliation_status": (
            "NOT_MEASURED"
            if delta is None
            else ("OK" if abs(delta) <= 0.01 else "MISMATCH")
        ),
        "details": detalhes,
    }


def c2_bucket_select_sql(
    *,
    layer: str = layers.SILVER,
    billing_period: str,
    bucket: int,
    buckets: int = DEFAULT_BUCKETS,
    connection_code: str | None = None,
) -> str:
    """Agrega **um bucket** de uma competência, no Trino, já enriquecido.

    Uma consulta por bucket, cada uma com `GROUP BY` no grão do cubo sobre ~1/16 dos
    recursos. O resultado é escrito pelo publicador por fatia; a API não recebe
    linhas Silver.

    O `bucket` entra como predicado sobre a **chave canônica** do recurso, então o
    mesmo recurso nunca aparece em dois buckets e a soma dos buckets é o total do
    período.

    Quando `connection_code` é informado, restringe a uma conexão — é o que permite
    publicar por `(competência, conexão, bucket)` e trocar cada fatia isoladamente.
    """
    conexao = ""
    if connection_code:
        conexao = (
            " AND COALESCE(_source_connection_code, _source_connection_name) = "
            f"{_quote_literal(connection_code)}"
        )
    rid = sql_canonical_resource_id("resource_id")
    return f"""
WITH custo AS (
    SELECT
        {sql_billing_period_key("COALESCE(_billing_period, billing_period)")} AS billing_period,
        {sql_provider_key("cloud_provider")} AS provider,
        COALESCE(_source_connection_code, _source_connection_name) AS connection_code,
        LOWER(TRIM(CAST(sub_account_id AS VARCHAR))) AS sub_account_id,
        {rid} AS canonical_resource_id,
        COALESCE(_source_connection_id, 0) AS source_connection_id,
        NULLIF(TRIM(CAST(charge_category AS VARCHAR)), '') AS charge_category,
        {sql_currency_code("pricing_currency")} AS currency,
        MAX(NULLIF(TRIM(CAST(resource_name AS VARCHAR)), '')) AS cost_resource_name,
        MAX(NULLIF(TRIM(CAST(resource_group_id AS VARCHAR)), '')) AS cost_resource_group_id,
        MAX({sql_service_key("service_name")}) AS service_key,
        MAX(NULLIF(TRIM(CAST(service_category AS VARCHAR)), '')) AS service_category,
        MAX({sql_region_key("region_id", charge_category_expr="charge_category", service_expr="service_name")}) AS region_key,
        SUM(TRY_CAST(effective_cost AS DOUBLE)) AS effective_cost,
        SUM(TRY_CAST(list_cost AS DOUBLE)) AS list_cost,
        SUM(TRY_CAST(pricing_quantity AS DOUBLE)) AS pricing_quantity,
        COUNT(*) AS rows,
        MIN(DATE(charge_period_start)) AS first_charge_day,
        MAX(DATE(charge_period_start)) AS last_charge_day,
        COUNT(DISTINCT DATE(charge_period_start)) AS charge_days,
        COUNT(*) FILTER (
            WHERE NULLIF(TRIM(CAST(tags_json AS VARCHAR)), '') IS NULL
               OR TRIM(CAST(tags_json AS VARCHAR)) IN ('{{}}', '[]')
        ) AS rows_without_tag
    FROM iceberg.{layer}.resource_cost
    WHERE CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) = {_quote_literal(billing_period)}{conexao}
      AND {bucket_predicate(rid, bucket, buckets=buckets)}
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
)
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
    CASE
        WHEN i.canonical_resource_id IS NULL THEN 'UNKNOWN'
        ELSE COALESCE(i.inventory_resource_type, 'UNKNOWN')
    END AS resource_type_key,
    i.inventory_resource_sku AS resource_sku,
    COALESCE(i.inventory_resource_group_id, c.cost_resource_group_id) AS resource_group_id,
    i.inventory_resource_state AS resource_state,
    i.operating_system,
    CASE
        WHEN i.canonical_resource_id IS NULL THEN 'NO_TEMPORAL_SNAPSHOT'
        ELSE i.inventory_status
    END AS inventory_status,
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
         THEN 'VALIDO' ELSE 'NAO_RECURSO' END AS resource_id_status,
    {int(bucket)} AS build_bucket
FROM custo c
LEFT JOIN iceberg.{layer}.{C2_INVENTORY_TABLE} i
  ON i.provider = c.provider
 AND i.sub_account_id = c.sub_account_id
 AND i.canonical_resource_id = c.canonical_resource_id
 AND i.billing_period = c.billing_period
""".strip()


async def list_period_connections(
    *, layer: str = layers.SILVER
) -> list[tuple[str, str]]:
    """Pares `(competência, conexão)` presentes no custo canônico.

    É a lista de fatias a construir: `(competência, conexão)` é a unidade de troca
    do publicador por fatia, e dentro dela os buckets são internos.
    """
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(
        "SELECT DISTINCT CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) AS bp, "
        "COALESCE(_source_connection_code, _source_connection_name) AS conn "
        "FROM iceberg.silver.resource_cost "
        "WHERE COALESCE(_billing_period, billing_period) IS NOT NULL "
        "ORDER BY 1, 2",
        timeout_s=900,
    )
    saida: list[tuple[str, str]] = []
    for linha in linhas or []:
        bp = str(linha.get("bp") or "")
        conn = str(linha.get("conn") or "")
        if bp.isdigit() and len(bp) == 6 and conn:
            saida.append((bp, conn))
    return saida
