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


#: Status de classificação organizacional publicado no C1.
#:
#: `UNMAPPED` é o único valor possível hoje. O custo canônico não carrega
#: organização, e **não existe chave reconciliável** entre `resource_cost` e a
#: classificação organizacional: a única ponte disponível seria
#: `sub_account_id → subaccountid` do livro FOCUS legado, e ela é **1:N**. Medido
#: em 23/09/2026, contas do canônico aparecem no legado com dezenas de hierarquias
#: distintas — uma delas com 52 valores diferentes em `departamento_nivel_5`.
#:
#: A versão anterior desta materialização escolhia a hierarquia de maior custo por
#: conta (`ROW_NUMBER` sobre `SUM(effectivecost)`) e atribuía essa hierarquia a
#: todo o custo da assinatura. Isso preservava o total, mas produzia **classificação
#: organizacional incorreta**: a assinatura inteira era carimbada com a área que
#: gastou mais, e as demais áreas desapareciam. Custo certo, decisão errada.
#:
#: Enquanto não existir ponte que preserve as divisões reais **e** reconcilie o
#: total, os níveis organizacionais são publicados como `NULL`. O custo
#: organizacional continua sendo servido pelo dashboard legado
#: (`dest_dm_cost_visao_completa`) até o cutover.
ORG_MAPPED = "MAPPED"
ORG_UNMAPPED = "UNMAPPED"

#: Sentinela usada quando a competência não tem classificação organizacional
#: disponível por nenhum caminho. É o mesmo `UNKNOWN` do vocabulário de dimensões.
_ORG_NO_VALUE = "NULL"


def org_hierarchy_audit_sql(
    *,
    layer: str = layers.SILVER,
    legacy_table: str = "dest_modelo_focus",
    cost_table: str = "resource_cost",
) -> str:
    """Mede quantas contas têm múltiplas hierarquias e o custo afetado.

    A auditoria exige o número, não a impressão: sem ele a decisão de não mapear
    pareceria conservadorismo. O resultado alimenta a regra de qualidade de C11.

    Devolve, por conta do **livro canônico**:

    - quantas hierarquias distintas o legado associa a ela;
    - quanto custa essa conta no canônico;
    - se ela é ambígua (`hierarquias > 1`), que é o caso que impede o mapeamento.
    """
    return f"""
WITH legado AS (
    SELECT
        LOWER(TRIM(CAST(subaccountid AS VARCHAR))) AS account_key,
        COUNT(DISTINCT COALESCE(CAST(departamento_nivel_1 AS VARCHAR), '')) AS n1,
        COUNT(DISTINCT COALESCE(CAST(departamento_nivel_2 AS VARCHAR), '')) AS n2,
        COUNT(DISTINCT COALESCE(CAST(departamento_nivel_5 AS VARCHAR), '')) AS n5,
        COUNT(DISTINCT COALESCE(CAST(departamento_nivel_6 AS VARCHAR), '')) AS n6,
        COUNT(DISTINCT CONCAT_WS('|',
            COALESCE(CAST(departamento_nivel_1 AS VARCHAR), ''),
            COALESCE(CAST(departamento_nivel_2 AS VARCHAR), ''),
            COALESCE(CAST(departamento_nivel_3 AS VARCHAR), ''),
            COALESCE(CAST(departamento_nivel_4 AS VARCHAR), ''),
            COALESCE(CAST(departamento_nivel_5 AS VARCHAR), ''),
            COALESCE(CAST(departamento_nivel_6 AS VARCHAR), ''),
            COALESCE(CAST(departamento_nivel_7 AS VARCHAR), '')
        )) AS hierarquias
    FROM iceberg.{layer}.{legacy_table}
    WHERE NULLIF(TRIM(CAST(subaccountid AS VARCHAR)), '') IS NOT NULL
    GROUP BY 1
),
canonico AS (
    SELECT
        LOWER(TRIM(CAST(sub_account_id AS VARCHAR))) AS account_key,
        COUNT(*) AS rows,
        ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)), 2) AS cost
    FROM iceberg.{layer}.{cost_table}
    WHERE NULLIF(TRIM(CAST(sub_account_id AS VARCHAR)), '') IS NOT NULL
    GROUP BY 1
)
SELECT
    c.account_key,
    COALESCE(l.hierarquias, 0) AS hierarquias,
    COALESCE(l.n1, 0) AS niveis_1,
    COALESCE(l.n5, 0) AS niveis_5,
    c.rows,
    c.cost,
    CASE WHEN COALESCE(l.hierarquias, 0) > 1 THEN 1 ELSE 0 END AS ambigua,
    CASE
        WHEN l.account_key IS NULL THEN 'SEM_CLASSIFICACAO_LEGADA'
        WHEN l.hierarquias > 1 THEN 'MULTIPLAS_HIERARQUIAS'
        ELSE 'HIERARQUIA_UNICA'
    END AS diagnostico
FROM canonico c
LEFT JOIN legado l ON l.account_key = c.account_key
""".strip()


async def measure_org_hierarchies(
    *, layer: str = layers.SILVER, timeout_s: float = 1200.0
) -> dict[str, Any]:
    """Executa a auditoria de hierarquias e resume o custo afetado.

    É a medida que sustenta a correção B: quantas contas do canônico têm mais de
    uma hierarquia e quanto custa esse conjunto.
    """
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(
        org_hierarchy_audit_sql(layer=layer), timeout_s=timeout_s
    )
    linhas = list(linhas or [])
    total_contas = len(linhas)
    total_custo = sum(float(r.get("cost") or 0) for r in linhas)
    ambiguas = [r for r in linhas if int(r.get("ambigua") or 0) == 1]
    sem_classificacao = [r for r in linhas if r.get("diagnostico") == "SEM_CLASSIFICACAO_LEGADA"]
    custo_ambiguo = sum(float(r.get("cost") or 0) for r in ambiguas)
    custo_sem = sum(float(r.get("cost") or 0) for r in sem_classificacao)
    return {
        "accounts": total_contas,
        "accounts_multiple_hierarchies": len(ambiguas),
        "accounts_without_legacy_classification": len(sem_classificacao),
        "cost_total": round(total_custo, 2),
        "cost_multiple_hierarchies": round(custo_ambiguo, 2),
        "cost_without_legacy_classification": round(custo_sem, 2),
        "max_hierarchies": max((int(r.get("hierarquias") or 0) for r in linhas), default=0),
        "accounts": [
            {
                "account_key": r.get("account_key"),
                "hierarchies": int(r.get("hierarquias") or 0),
                "cost": float(r.get("cost") or 0),
                "diagnostico": r.get("diagnostico"),
            }
            for r in sorted(linhas, key=lambda x: -int(x.get("hierarquias") or 0))[:50]
        ],
    }


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
    """Estágio 2 do C1: enriquece com recursos e declara a organização indisponível.

    Não há join organizacional. A organização é publicada como `NULL` com
    `org_classification_status = 'UNMAPPED'`, porque **não existe chave
    reconciliável** entre `resource_cost` e a classificação organizacional (ver
    :data:`ORG_UNMAPPED`). Escolher a hierarquia dominante por conta preservava o
    total e corrompia a leitura por área.

    O único join que sobra é sobre chave **deduplicada** (recursos por competência
    + conexão + conta), e a prova de que não multiplica é o total publicado ser
    idêntico ao do estágio 1.
    """
    niveis = ", ".join(f"CAST(NULL AS VARCHAR) AS {n}" for n in ORG_LEVELS)
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
    {niveis},
    '{ORG_UNMAPPED}' AS org_classification_status,
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
        "departamento_nivel_1..7": (
            "sem chave reconciliável entre resource_cost e a classificação "
            "organizacional: a ponte conta→hierarquia do livro legado é 1:N. "
            "Publicado como NULL com org_classification_status='UNMAPPED'; a "
            "classificação organizacional continua no dashboard legado"
        ),
        "org_classification_status": (
            "sempre 'UNMAPPED' enquanto não existir ponte que preserve as divisões "
            "reais e reconcilie o total"
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
    baseline: Any | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Materializa C1 em três etapas governadas, com reconciliação obrigatória.

    R1 da auditoria: `expected_total` **não é opcional** para cubo financeiro. A
    fila mede o baseline uma vez por run e o passa aqui; sem ele não há contra o
    quê reconciliar, e publicar sem reconciliação é o defeito que a auditoria
    apontou.

    O validador roda **contra o staging, antes do swap**: total, linhas de origem,
    moeda e fatias esperadas. Um problema reprova a publicação sem tocar a tabela
    anterior — o swap só acontece quando o staging fechou.
    """
    from app.services.gold_governance import (
        BaselineMismatchError,
        assert_baseline,
        materialize_gold_sql,
        publish_gold_cube,
        RECONCILIATION_TOLERANCE_BRL,
    )
    from app.services.trino_service import execute as trino_execute

    if expected_total is None:
        raise RuntimeError(
            "C1 exige expected_total: a reconciliação obrigatória não é opcional. "
            "A fila deve medir o baseline de silver.resource_cost e passá-lo."
        )
    if baseline is None:
        raise RuntimeError(
            "C1 exige baseline (CostBaseline): total, linhas, moedas, períodos e "
            "conexões esperados vêm da mesma medição única do run."
        )

    async def _validador(namespace: str, staging: str) -> list[str]:
        """Verifica linhas de origem, moeda e fatias no staging, antes do swap.

        Devolve problemas em vez de levantar, porque o publicador agrega o motivo
        ao erro de publicação.
        """
        resumo = await trino_execute(
            f"SELECT SUM(rows) AS linhas_origem, COUNT(DISTINCT currency) AS moedas "
            f"FROM iceberg.{namespace}.{staging}",
            timeout_s=900,
        )
        moedas = await trino_execute(
            f"SELECT DISTINCT CAST(currency AS VARCHAR) AS currency "
            f"FROM iceberg.{namespace}.{staging}",
            timeout_s=900,
        )
        fatias = await trino_execute(
            f"SELECT DISTINCT billing_period, connection_code "
            f"FROM iceberg.{namespace}.{staging}",
            timeout_s=900,
        )
        try:
            assert_baseline(
                baseline,
                total_out=float(expected_total),
                rows_out=int((resumo[0] if resumo else {}).get("linhas_origem") or 0),
                currencies_out=[
                    str(r["currency"]) for r in moedas or [] if r.get("currency")
                ],
                slices_out=[
                    (str(r["billing_period"]), str(r["connection_code"]))
                    for r in fatias or []
                ],
                table_label="C1 staging",
            )
            return []
        except BaselineMismatchError as exc:
            return [str(exc).split(": ", 1)[1]]

    etapa1 = await materialize_gold_sql(
        cube_code=C1_GRAIN_TABLE,
        table=C1_GRAIN_TABLE,
        select_sql=c1_grain_sql(layer=layer),
        namespace=layer,
        run_id=run_id,
    )

    etapa2 = await materialize_gold_sql(
        cube_code=C1_RESOURCE_TABLE,
        table=C1_RESOURCE_TABLE,
        select_sql=resource_count_sql(layer=layer),
        namespace=layer,
        run_id=run_id,
    )

    publicacao = await publish_gold_cube(
        cube_code=C1_CUBE_CODE,
        table=C1_TABLE,
        select_sql=c1_stage2_sql(
            layer=layer,
            grain_table=C1_GRAIN_TABLE,
            resource_table=C1_RESOURCE_TABLE,
        ),
        namespace=layers.GOLD,
        partition_column="billing_period",
        run_id=run_id,
        financial_total_in=round(float(expected_total), 2),
        rows_read=int(baseline.rows),
        validate_staging=_validador,
    )

    total_out = float(publicacao.financial_delta or 0) + round(float(expected_total), 2)
    return {
        "table": C1_TABLE,
        "rows": publicacao.rows,
        "grain_rows": etapa1.rows,
        "resource_rows": etapa2.rows,
        "financial_total_in": round(float(expected_total), 2),
        "financial_total_out": round(total_out, 2),
        "financial_delta": publicacao.financial_delta,
        "reconciliation_status": publicacao.reconciliation_status,
    }
