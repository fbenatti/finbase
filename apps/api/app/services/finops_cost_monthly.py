"""C1 — `finops_cost_monthly`: custo mensal por escopo e dimensões conformadas.

Responde "quanto custou cada escopo por mês e quais dimensões explicam esse
custo", com número reconciliado e status de qualidade ao lado.

Grão: uma linha por
`billing_period + provider + connection_code + sub_account_id + service_key +
service_category + region_key + charge_category + currency + hierarquia
organizacional`.

Decisões que a construção precisou tomar, todas verificadas em 23/09/2026:

- **`billinged_cost`/`contracted_cost` são indisponíveis, não zero.** Estão NULL
  em 13.519.512/13.519.512 linhas. Publicá-los como `0` faria "desconto
  contratado" parecer medido. A coluna `financial_availability` registra o que
  não é mensurável;
- **organização vem do livro FOCUS legado, por conta.** O custo canônico não tem
  hierarquia organizacional. A ponte é `sub_account_id → subaccountid`, com o
  mapeamento deduplicado para garantir 1:1 e não multiplicar custo (74 contas
  ambíguas no legado). Cobertura medida: 95 % das contas do canônico;
- **`resource_type` não existe no custo** (NULL em 100 % das linhas). O tipo do
  recurso é atributo de C2, herdado do inventário;
- **`region_key` distingue `GLOBAL` de `UNKNOWN`** — reserva sem região não é
  dado faltante, e ausência em consumo não é globalidade.
"""

from __future__ import annotations

from typing import Any

from app.services import lakehouse_layers as layers
from app.services.gold_governance import RECONCILIATION_TOLERANCE_BRL
from app.services.semantic_normalization import (
    GLOBAL,
    UNKNOWN,
    sql_billing_period_key,
    sql_canonical_resource_id,
    sql_currency_code,
    sql_provider_key,
    sql_region_key,
    sql_service_key,
)

#: Nome físico do cubo. Prefixo `finops_` separa os cubos de decisão dos cubos
#: legados `dest_dm_cost_*`, que continuam servindo as telas atuais.
C1_TABLE = "finops_cost_monthly"
C1_CUBE_CODE = "finops_cost_monthly"

#: Níveis organizacionais publicados. São os mesmos que o livro legado expõe e que
#: a árvore de decomposição das telas navega.
ORG_LEVELS: tuple[str, ...] = tuple(f"departamento_nivel_{n}" for n in range(1, 8))

#: Medidas que **não são aditivas**. Somar `resources` pelas dimensões do cubo
#: conta o mesmo recurso em cada serviço, região e categoria em que ele aparece:
#: medido em 23/09/2026, a soma das linhas do cubo devolveu 21.229.892 enquanto o
#: total real de recursos distintos de `resource_cost` é 968.339. Um consumidor
#: que somasse essa coluna publicaria um número quase 22× maior. Mesmo problema
#: vale para `cost_missing_dimension` e `cost_without_tag`, que já são somas
#: sobrepostas por construção (um custo pode não ter serviço **e** não ter tag).
NON_ADDITIVE_MEASURES: frozenset[str] = frozenset(
    {"resources", "cost_missing_dimension", "cost_without_tag", "rows_without_tag"}
)

#: Medidas de custo aditivas — as únicas que podem ser somadas livremente.
ADDITIVE_MEASURES: frozenset[str] = frozenset(
    {"effective_cost", "list_cost", "pricing_quantity", "rows", "purchase_rows"}
)

#: Medidas de custo que a origem tem (mensuráveis).
_MEASURABLE_COSTS = ("effective_cost", "list_cost")
#: Medidas de custo que a origem **não** tem. Publicadas como NULL + declaração.
_UNAVAILABLE_COSTS = ("billed_cost", "contracted_cost")


def org_mapping_sql(
    *,
    layer: str = layers.SILVER,
    legacy_table: str = "dest_modelo_focus",
) -> str:
    """Mapeamento `conta → hierarquia organizacional`, estritamente 1:1.

    Uma conta pode aparecer com mais de uma organização no livro legado (74
    contas ambíguas em 23/09/2026). Sem deduplicar, o `LEFT JOIN` multiplicaria
    linhas de custo — exatamente o fan-out que a regra 4.1 proíbe. A linha
    escolhida por conta é a de **maior custo agregado**, que é também a forma
    determinística de escolher (empate cai na hierarquia lexicalmente menor).
    """
    niveis = ", ".join(f"MAX({n} ) AS {n}" for n in ORG_LEVELS)
    return f"""
WITH por_org AS (
    SELECT
        LOWER(TRIM(CAST(subaccountid AS VARCHAR))) AS account_key,
        {niveis},
        ROUND(SUM(TRY_CAST(effectivecost AS DOUBLE)), 2) AS org_cost
    FROM iceberg.{layer}.{legacy_table}
    WHERE NULLIF(TRIM(CAST(subaccountid AS VARCHAR)), '') IS NOT NULL
    GROUP BY 1, {", ".join(ORG_LEVELS)}
),
ranqueado AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY account_key
            ORDER BY org_cost DESC NULLS LAST,
                     COALESCE({ORG_LEVELS[0]}, '') ASC,
                     COALESCE({ORG_LEVELS[-1]}, '') ASC
        ) AS rn
    FROM por_org
)
SELECT account_key, {", ".join(ORG_LEVELS)}, org_cost
FROM ranqueado
WHERE rn = 1
""".strip()


def c1_grain_sql(
    *,
    layer: str = layers.SILVER,
    cost_table: str = "resource_cost",
) -> str:
    """Estágio 1 do C1: agrega `resource_cost` no grão do cubo, sem o join.

    A agregação é dividida em dois estágios por restrição **medida** do ambiente
    de referência, não por preferência: os workers do Trino têm 2,4 GiB cada e uma
    única CTAS que agregasse 13,5 M linhas com o `COUNT(DISTINCT resource_id)` e o
    join da hierarquia organizacional derrubava o cluster. Em duas etapas, cada
    scan processa menos ao mesmo tempo.

    Não há `COUNT(DISTINCT)` aqui: recursos distintos exigem um segundo scan no
    grão do recurso, que é o estágio 2.
    """
    region = sql_region_key(
        "region_id", charge_category_expr="charge_category", service_expr="service_name"
    )
    service = sql_service_key("service_name")
    dim_ausente = (
        f"({service} = '{UNKNOWN}' "
        f"OR {region} = '{UNKNOWN}' "
        "OR NULLIF(TRIM(CAST(sub_account_id AS VARCHAR)), '') IS NULL)"
    )
    sem_tag = (
        "NULLIF(TRIM(CAST(tags_json AS VARCHAR)), '') IS NULL "
        "OR TRIM(CAST(tags_json AS VARCHAR)) IN ('{}', '[]')"
    )
    return f"""
SELECT
    {sql_billing_period_key("COALESCE(_billing_period, billing_period)")} AS billing_period,
    {sql_provider_key("cloud_provider")} AS provider,
    COALESCE(_source_connection_code, _source_connection_name) AS connection_code,
    LOWER(TRIM(CAST(sub_account_id AS VARCHAR))) AS sub_account_id,
    {service} AS service_key,
    NULLIF(TRIM(CAST(service_category AS VARCHAR)), '') AS service_category,
    {region} AS region_key,
    NULLIF(TRIM(CAST(charge_category AS VARCHAR)), '') AS charge_category,
    {sql_currency_code("pricing_currency")} AS currency,
    SUM(TRY_CAST(effective_cost AS DOUBLE)) AS effective_cost,
    SUM(TRY_CAST(list_cost AS DOUBLE)) AS list_cost,
    SUM(TRY_CAST(pricing_quantity AS DOUBLE)) AS pricing_quantity,
    COUNT(*) AS rows,
    ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)) FILTER (WHERE {dim_ausente}), 2)
        AS cost_missing_dimension,
    ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)) FILTER (WHERE {sem_tag}), 2)
        AS cost_without_tag,
    COUNT(*) FILTER (WHERE {sem_tag}) AS rows_without_tag,
    COUNT(*) FILTER (
        WHERE LOWER(TRIM(CAST(service_name AS VARCHAR))) = 'microsoft.capacity'
           OR LOWER(TRIM(CAST(charge_category AS VARCHAR))) = 'purchase'
    ) AS purchase_rows
FROM iceberg.{layer}.{cost_table}
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
""".strip()


def resource_count_sql(
    *,
    layer: str = layers.SILVER,
    cost_table: str = "resource_cost",
) -> str:
    """Estágio 2a: recursos distintos válidos por competência, conexão e conta.

    Segundo scan, deliberadamente estreito: seleciona só as colunas da chave e
    conta. É o preço de ter `resources` no cubo sem `COUNT(DISTINCT)` sobre 13,5 M
    linhas numa única query.

    Só IDs com forma de caminho de provedor entram: as linhas de ajuste do 4P têm
    `resource_id` preenchido com `'320core'`, e contá-las infla "recursos".
    """
    rid = sql_canonical_resource_id("resource_id")
    return f"""
SELECT
    {sql_billing_period_key("COALESCE(_billing_period, billing_period)")} AS billing_period,
    COALESCE(_source_connection_code, _source_connection_name) AS connection_code,
    LOWER(TRIM(CAST(sub_account_id AS VARCHAR))) AS sub_account_id,
    COUNT(DISTINCT {rid}) AS resources
FROM iceberg.{layer}.{cost_table}
WHERE {rid} LIKE '/%' AND {rid} LIKE '%/providers/%'
GROUP BY 1, 2, 3
""".strip()


def c1_stage2_sql(
    *,
    layer: str = layers.SILVER,
    legacy_table: str = "dest_modelo_focus",
    grain_table: str,
    resource_table: str,
) -> str:
    """Estágio 2b: enriquece o grão com organização e recursos, sem multiplicar.

    Os dois joins são sobre chaves **deduplicadas**:

    - hierarquia organizacional: uma linha por conta (`ROW_NUMBER` resolve as 74
      contas ambíguas do livro legado);
    - recursos: uma linha por competência + conexão + conta.

    Por isso o `SUM(effective_cost)` daqui é idêntico ao do estágio 1 — o join não
    multiplica. Cubos de custo não podem dobrar por causa de atributo.
    """
    org_select = ", ".join(f"org.{n}" for n in ORG_LEVELS)
    return f"""
SELECT
    g.billing_period,
    g.provider,
    g.connection_code,
    g.sub_account_id,
    g.service_key,
    g.service_category,
    g.region_key,
    g.charge_category,
    g.currency,
    {org_select},
    CASE WHEN org.account_key IS NULL THEN 'UNMAPPED' ELSE 'MAPPED' END
        AS org_classification_status,
    g.effective_cost,
    g.list_cost,
    CAST(NULL AS DOUBLE) AS billed_cost,
    CAST(NULL AS DOUBLE) AS contracted_cost,
    g.pricing_quantity,
    g.rows,
    COALESCE(rc.resources, 0) AS resources,
    g.cost_missing_dimension,
    g.cost_without_tag,
    g.rows_without_tag,
    g.purchase_rows
FROM iceberg.{layer}.{grain_table} g
LEFT JOIN (
{org_mapping_sql(layer=layer, legacy_table=legacy_table)}
) org
  ON org.account_key = g.sub_account_id
LEFT JOIN iceberg.{layer}.{resource_table} rc
  ON rc.billing_period = g.billing_period
 AND rc.connection_code = g.connection_code
 AND rc.sub_account_id = g.sub_account_id
""".strip()


def c1_select_sql(
    *,
    layer: str = layers.SILVER,
    cost_table: str = "resource_cost",
    legacy_table: str = "dest_modelo_focus",
) -> str:
    """SELECT completo do C1, juntando os dois estágios numa única consulta.

    Mantido para inspeção, teste e para o caso de um ambiente com memória
    suficiente para materializar em uma etapa. `materialize_c1` usa os estágios
    separados, que é o caminho que funciona no ambiente de referência.
    """
    return f"""
WITH grao AS (
    SELECT * FROM ({c1_grain_sql(layer=layer, cost_table=cost_table)}) g
),
recursos AS (
    SELECT * FROM ({resource_count_sql(layer=layer, cost_table=cost_table)}) r
)
SELECT * FROM ({c1_stage2_sql(
        layer=layer,
        legacy_table=legacy_table,
        grain_table="grao",
        resource_table="recursos",
    )})
""".strip()


def c1_stage2_sql_from_tables(
    *,
    layer: str = layers.SILVER,
    legacy_table: str = "dest_modelo_focus",
    grain_table: str,
    resource_table: str,
) -> str:
    """Alias nomeado de :func:`c1_stage2_sql` para leitura do fluxo de materialização."""
    return c1_stage2_sql(
        layer=layer,
        legacy_table=legacy_table,
        grain_table=grain_table,
        resource_table=resource_table,
    )


def unavailable_measures() -> dict[str, str]:
    """Medidas que a origem não permite calcular, com o motivo.

    Consumidor e UI precisam saber **por que** o campo é nulo. Publicar zero
    faria o dashboard mostrar "desconto contratado = R$ 0,00", que é uma
    afirmação falsa sobre um dado ausente.
    """
    return {
        "billed_cost": "billed_cost é NULL em 13.519.512/13.519.512 linhas de resource_cost",
        "contracted_cost": (
            "contracted_cost é NULL em 13.519.512/13.519.512 linhas; "
            "list_cost é igual a effective_cost, então não há desconto a medir"
        ),
    }


def quality_columns() -> tuple[str, ...]:
    """Colunas de qualidade que acompanham o valor (seção 4.4)."""
    return (
        "cost_missing_dimension",
        "cost_without_tag",
        "rows_without_tag",
        "org_classification_status",
        "purchase_rows",
    )


#: Tabelas intermediárias do C1. Vivem na Silver porque são detalhe derivado e
#: reconstruível, não cubo publicado. O cubo é `finops_cost_monthly`.
C1_GRAIN_TABLE = "finops_cost_monthly_grain"
C1_RESOURCE_TABLE = "finops_cost_monthly_resources"


async def materialize_c1(
    *,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    expected_total: float | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Materializa C1 em duas etapas governadas, com manifesto e reconciliação.

    Ordem:

    1. estágio 1 — agrega `resource_cost` no grão do cubo (1 CTAS, sem join);
    2. estágio 2a — conta recursos distintos válidos por competência/conexão/conta;
    3. estágio 2b — publica `finops_cost_monthly`, enriquecido com organização e
       recursos, pelos joins deduplicados;
    4. verifica que o total publicado é igual ao total de `resource_cost`.

    Cada etapa registra manifesto. O total publicado é medido **na tabela
    publicada**, não no staging, e a diferença acima de R$ 0,01 reprova a
    materialização — cubo de custo que não bate com a origem não pode ser servido.
    """
    from app.services.gold_governance import (
        PARTIAL,
        SUCCESS,
        ManifestEntry,
        current_snapshot_id,
        materialize_gold_sql,
        record_manifest,
    )
    from app.services.trino_service import execute as trino_execute

    def _etapa(mensagem: str) -> None:
        if progress:
            progress(0, 4, mensagem)

    _etapa("C1 estagio 1/4: agregando resource_cost no grao do cubo")
    etapa1 = await materialize_gold_sql(
        cube_code=C1_GRAIN_TABLE,
        table=C1_GRAIN_TABLE,
        select_sql=c1_grain_sql(layer=layer),
        namespace=layer,
        run_id=run_id,
    )

    _etapa("C1 estagio 2/4: contando recursos distintos validos")
    etapa2 = await materialize_gold_sql(
        cube_code=C1_RESOURCE_TABLE,
        table=C1_RESOURCE_TABLE,
        select_sql=resource_count_sql(layer=layer),
        namespace=layer,
        run_id=run_id,
    )

    _etapa("C1 estagio 3/4: publicando finops_cost_monthly")
    publicacao = await materialize_gold_sql(
        cube_code=C1_CUBE_CODE,
        table=C1_TABLE,
        select_sql=c1_stage2_sql(
            layer=layer,
            grain_table=C1_GRAIN_TABLE,
            resource_table=C1_RESOURCE_TABLE,
        ),
        namespace=layers.GOLD,
        partition_columns=("billing_period",),
        run_id=run_id,
        financial_total_in=expected_total,
    )

    _etapa("C1 estagio 4/4: reconciliando o cubo publicado")
    verificacao = await trino_execute(
        f"SELECT ROUND(SUM(effective_cost), 2) AS total, COUNT(*) AS linhas "
        f"FROM iceberg.{layers.GOLD}.{C1_TABLE}",
        timeout_s=900,
    )
    total_out = float((verificacao[0] if verificacao else {}).get("total") or 0)
    delta = (
        None if expected_total is None else round(total_out - float(expected_total), 2)
    )
    if delta is None:
        reconciliacao = "NOT_MEASURED"
    elif abs(delta) <= RECONCILIATION_TOLERANCE_BRL:
        reconciliacao = "OK"
    else:
        reconciliacao = "MISMATCH"
    await record_manifest(
        ManifestEntry(
            cube_code=C1_CUBE_CODE,
            physical_table=C1_TABLE,
            namespace=layer,
            run_id=run_id,
            # A publicação já aconteceu; o que se registra aqui é o veredito da
            # reconciliação sobre ela.
            status=SUCCESS if reconciliacao == "OK" else PARTIAL,
            financial_total_in=expected_total,
            financial_total_out=total_out,
            financial_delta=delta,
            reconciliation_status=reconciliacao,
            rows_written=publicacao.rows,
            previous_snapshot_id=current_snapshot_id(layers.GOLD, C1_TABLE),
            reason=None if reconciliacao == "OK" else "reconciliacao divergente",
        )
    )

    if reconciliacao == "MISMATCH":
        raise RuntimeError(
            f"C1 nao reconcilia: publicado R$ {total_out:,.2f} contra "
            f"R$ {float(expected_total):,.2f} de resource_cost (delta R$ {delta:,.2f})"
        )

    return {
        "table": C1_TABLE,
        "rows": publicacao.rows,
        "grain_rows": etapa1.rows,
        "resource_rows": etapa2.rows,
        "financial_total_in": expected_total,
        "financial_total_out": total_out,
        "financial_delta": delta,
        "reconciliation_status": reconciliacao,
    }
