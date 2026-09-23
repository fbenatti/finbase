"""C3 — `finops_cost_change_driver`: o que explica a mudança entre competências.

Responde "o que explica a mudança entre duas competências comparáveis" e fecha no
delta total: a soma das contribuições dos drivers é o delta entre os períodos.

Grão: `period_current + period_previous + connection_code + driver_type +
driver_key`.

Regras que a construção aplica:

- **só pares comparáveis.** O par de competências vem de
  `period_coverage.comparable_pairs`, que exige `COMPLETE` nos dois lados. Sem
  isso, `202504 → 202505` (ajuste de 1 dia contra mês operacional) viraria
  "tendência" com variação de 1.400 %, e `202603 → 202604` compararia um mês cheio
  com 51 linhas de compra de reserva;
- **mesma conexão e mesma moeda.** Comparar 4P com TI, ou BRL com USD, produz um
  delta sem significado;
- **drivers separados por `driver_type`.** Serviço, conta, região e organização
  são materializações distintas no espaço de `driver_type` — a soma de um tipo
  fecha o delta; somar tipos diferentes contaria a mesma variação várias vezes;
- **recursos novos e removidos são explícitos.** `new` e `removed` não têm,
  respectivamente, custo anterior ou atual; tratar ausência como zero está correto
  **aqui**, porque a pergunta é sobre a variação de um recurso que passou a existir
  — diferente de publicar ausência como zero numa medida de custo.
"""

from __future__ import annotations

import time
from typing import Any

from app.services import lakehouse_layers as layers
from app.services.finops_cost_monthly import C1_TABLE
from app.services.period_coverage import comparable_pairs

C3_TABLE = "finops_cost_change_driver"
C3_CUBE_CODE = "finops_cost_change_driver"

#: Tipos de driver materializados. Cada um é uma leitura do mesmo delta por uma
#: dimensão diferente. Somar tipos diferentes não faz sentido; cada tipo fecha o
#: delta por si.
DRIVER_TYPES: tuple[str, ...] = (
    "service",
    "sub_account",
    "region",
    "organization",
)

#: Tolerância de fechamento do delta, em reais, **por driver**.
#:
#: O fechamento compara `SUM(ROUND(delta, 2))` com a diferença dos totais de C1.
#: Cada driver carrega até R$ 0,005 de erro de arredondamento, então o erro
#: acumulado cresce com o número de drivers: medido no par 202601→202602, 44
#: drivers divergiram R$ 0,02. Uma tolerância fixa de R$ 0,01 reprovaria um cubo
#: correto. O limite é R$ 0,01 por driver, com piso de R$ 0,05.
RECONCILIATION_TOLERANCE_PER_DRIVER = 0.01
RECONCILIATION_TOLERANCE_FLOOR = 0.05


def reconciliation_tolerance(driver_count: int) -> float:
    """Tolerância de fechamento para um conjunto de drivers."""
    return max(
        RECONCILIATION_TOLERANCE_FLOOR,
        RECONCILIATION_TOLERANCE_PER_DRIVER * max(0, int(driver_count or 0)),
    )


#: Coluna de chave por tipo de driver, no cubo C1.
_DRIVER_KEY_COLUMN: dict[str, str] = {
    "service": "service_key",
    "sub_account": "sub_account_id",
    "region": "region_key",
    "organization": "departamento_nivel_5",
}


def driver_key_column(driver_type: str) -> str:
    """Coluna de C1 que identifica a chave de um tipo de driver.

    Levanta para tipo desconhecido em vez de devolver um default silencioso: um
    `driver_type` novo precisa ser declarado aqui e em `DRIVER_TYPES`, senão o
    cubo publicaria uma leitura que ninguém pediu.
    """
    try:
        return _DRIVER_KEY_COLUMN[driver_type]
    except KeyError as exc:
        raise KeyError(
            f"driver_type desconhecido: {driver_type!r}. "
            f"Declarados: {', '.join(sorted(_DRIVER_KEY_COLUMN))}"
        ) from exc


def c3_select_sql(
    *,
    layer: str = layers.SILVER,
    period_previous: str,
    period_current: str,
    driver_type: str,
    c1_table: str = C1_TABLE,
    c1_namespace: str = layers.GOLD,
) -> str:
    """SELECT do C3 para um par de competências e um tipo de driver.

    O `delta` é `atual − anterior`. A `contribution_pct` é a participação do
    driver no delta total do par: somada, dá 100 % (quando o delta total não é
    zero). `flag` distingue `new`, `removed`, `increased` e `decreased` — sem ela,
    R$ 0 → R$ 100 mil e R$ 100 mil → R$ 200 mil ficam indistinguíveis.

    O delta total é calculado no mesmo SELECT a partir das mesmas linhas, então o
    fechamento é aritmético, não uma segunda medição que pode divergir.
    """
    chave = driver_key_column(driver_type)
    return f"""
WITH atual AS (
    SELECT {chave} AS driver_key, SUM(effective_cost) AS cost
    FROM iceberg.{c1_namespace}.{c1_table}
    WHERE billing_period = '{period_current}'
    GROUP BY 1
),
anterior AS (
    SELECT {chave} AS driver_key, SUM(effective_cost) AS cost
    FROM iceberg.{c1_namespace}.{c1_table}
    WHERE billing_period = '{period_previous}'
    GROUP BY 1
),
unificado AS (
    SELECT
        COALESCE(a.driver_key, p.driver_key) AS driver_key,
        COALESCE(a.cost, 0.0) AS cost_current,
        COALESCE(p.cost, 0.0) AS cost_previous,
        CASE WHEN p.driver_key IS NULL THEN 1 ELSE 0 END AS is_new,
        CASE WHEN a.driver_key IS NULL THEN 1 ELSE 0 END AS is_removed
    FROM atual a
    FULL OUTER JOIN anterior p ON p.driver_key = a.driver_key
),
total AS (
    SELECT SUM(cost_current - cost_previous) AS total_delta FROM unificado
)
SELECT
    '{period_current}' AS period_current,
    '{period_previous}' AS period_previous,
    '{driver_type}' AS driver_type,
    CAST(u.driver_key AS VARCHAR) AS driver_key,
    ROUND(u.cost_current, 2) AS cost_current,
    ROUND(u.cost_previous, 2) AS cost_previous,
    ROUND(u.cost_current - u.cost_previous, 2) AS delta,
    CASE
        WHEN u.cost_previous = 0 THEN NULL
        ELSE ROUND(100.0 * (u.cost_current - u.cost_previous) / ABS(u.cost_previous), 4)
    END AS delta_pct,
    CASE
        WHEN t.total_delta = 0 THEN NULL
        ELSE ROUND(100.0 * (u.cost_current - u.cost_previous) / t.total_delta, 6)
    END AS contribution_pct,
    CASE
        WHEN u.is_new = 1 THEN 'new'
        WHEN u.is_removed = 1 THEN 'removed'
        WHEN u.cost_current > u.cost_previous THEN 'increased'
        WHEN u.cost_current < u.cost_previous THEN 'decreased'
        ELSE 'unchanged'
    END AS flag,
    ROUND(t.total_delta, 2) AS total_delta
FROM unificado u
CROSS JOIN total t
""".strip()


def c3_pairs_sql(*, layer: str = layers.SILVER) -> str:
    """Pares de competências por conexão que podem alimentar comparação.

    Deriva do próprio C1 (a classificação de cobertura é a mesma) e é o que a
    materialização usa: se o par não é comparável, ele não é materializado.
    """
    return f"""
SELECT DISTINCT billing_period, connection_code
FROM iceberg.{layers.GOLD}.{C1_TABLE}
WHERE billing_period IS NOT NULL
""".strip()


def comparable_period_pairs(
    periods_by_connection: dict[str, list[str]],
    *,
    statuses: dict[tuple[str, str], str] | None = None,
) -> list[tuple[str, str, str]]:
    """Pares consecutivos comparáveis por conexão, a partir do C1.

    Reusa `period_coverage.comparable_pairs`, então a regra de comparabilidade tem
    **uma** definição no projeto. Um par que G1 recusa não chega a C3.

    `statuses` é obrigatório para o resultado ser verdadeiro: o classificador
    precisa do status real de cada competência. Quando ele não vem, a competência
    entra como `MISSING` — **não** como `COMPLETE`.

    A primeira versão desta função montava `PeriodCoverage(status="COMPLETE")` para
    toda competência, o que fazia o filtro de comparabilidade nunca reprovar nada:
    `202504 → 202505` (ajuste de 1 dia contra mês operacional) apareceu em C3 como
    se fosse tendência. É a mesma classe de defeito que G1 existe para eliminar —
    dar a um período um status que ninguém mediu.
    """
    from app.services.period_coverage import MISSING, PeriodCoverage

    mapa = statuses or {}
    saida: list[tuple[str, str, str]] = []
    for conexao, periodos in periods_by_connection.items():
        coberturas = [
            PeriodCoverage(
                connection_code=conexao,
                billing_period=p,
                dataset="finops_cost_monthly",
                # Sem status medido, a competência é MISSING: o default é não
                # comparável, não comparável.
                status=mapa.get((conexao, p), MISSING),
                reason="OK" if (conexao, p) in mapa else "status nao medido",
            )
            for p in sorted(periodos)
        ]
        for anterior, atual in comparable_pairs(coberturas):
            saida.append((conexao, anterior.billing_period, atual.billing_period))
    return saida


#: Prefixo das fatias de C3. Silver: cada par × driver é uma leitura incremental
#: e reconstruível; o cubo publicado é `finops_cost_change_driver`.
C3_SLICE_PREFIX = "finops_cost_driver_slice"


def slice_table_name(
    connection_code: str, period_current: str, driver_type: str, *, build: str | None = None
) -> str:
    """Nome físico da fatia de um par × driver.

    Sanitiza a conexão para nome de tabela Iceberg (um código com hífen viraria
    erro de parser) e **versiona por execução**.

    O versionamento não é cosmético: sem ele, uma fatia de um par que deixou de ser
    comparável sobrevive na Silver e entra na montagem seguinte. Foi exatamente o
    que aconteceu na primeira execução — o `SUM(delta)` das quatro leituras fechou
    em −R$ 209.564,67, que é o custo de **202504**, competência que não é
    comparável e não deveria estar no cubo. Fatia órfã não pode virar número.
    """
    conexao = "".join(ch if ch.isalnum() else "_" for ch in str(connection_code))
    driver = "".join(ch if ch.isalnum() else "_" for ch in str(driver_type))
    sufixo = f"_{build}" if build else ""
    return f"{C3_SLICE_PREFIX}_{conexao}_{period_current}_{driver}{sufixo}"


def c3_union_sql(*, slice_tables: list[str], layer: str = layers.SILVER) -> str:
    """`UNION ALL` das fatias já materializadas.

    A montagem lê fatias em vez de repetir as CTEs. Motivo medido: 40 partes
    (10 pares × 4 drivers) numa consulta só estourou o limite de estágios do
    Trino — "Number of stages in the query (363) exceeds the allowed maximum
    (150)". Com uma fatia por consulta e a montagem lendo tabelas, o plano tem um
    scan por fatia.
    """
    if not slice_tables:
        vazio = c3_select_sql(
            layer=layer,
            period_previous="000000",
            period_current="000000",
            driver_type="service",
        )
        return f"SELECT * FROM (\n{vazio}\n) WHERE 1 = 0"
    return "\nUNION ALL\n".join(
        f"SELECT * FROM iceberg.{layer}.{tabela}" for tabela in slice_tables
    )


async def materialize_c3_slice(
    *,
    connection_code: str,
    period_previous: str,
    period_current: str,
    driver_type: str,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    build: str | None = None,
) -> int:
    """Materializa uma fatia de C3 (um par × um driver)."""
    from app.services.gold_governance import materialize_gold_sql

    publicacao = await materialize_gold_sql(
        cube_code=f"{C3_CUBE_CODE}:{period_current}:{driver_type}",
        table=slice_table_name(
            connection_code, period_current, driver_type, build=build
        ),
        select_sql=c3_select_sql(
            layer=layer,
            period_previous=period_previous,
            period_current=period_current,
            driver_type=driver_type,
        ),
        namespace=layer,
        run_id=run_id,
    )
    return publicacao.rows


async def period_statuses() -> dict[tuple[str, str], str]:
    """Status de cobertura de `(conexão, competência)` **lido da tabela publicada**.

    Antes esta função reexecutava o classificador sobre a Silver inteira a cada
    chamada — segundos por execução e uma segunda definição de comparabilidade
    dentro do request. Agora lê `gold.finops_period_coverage`, que é a fonte de
    verdade publicada.

    Par ausente da tabela **não** recebe status: C3 aplica
    `DEFAULT_UNKNOWN_STATUS` (`NOT_COMPARABLE`), nunca `COMPLETE`.
    """
    from app.services.period_coverage import load_coverage_map

    return await load_coverage_map()


async def materialize_c3(
    *,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    periods_by_connection: dict[str, list[str]] | None = None,
    driver_types: tuple[str, ...] = DRIVER_TYPES,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Materializa C3: uma fatia por par × driver, depois monta o cubo.

    Um par não comparável é **registrado como pulado** no retorno, não silenciado:
    a ausência precisa ser distinguível de "materializado e vazio".

    A montagem final lê as fatias, em vez de repetir as CTEs, porque 40 partes numa
    consulta só estoura o limite de estágios do Trino (363 > 150).
    """
    from app.services.gold_governance import materialize_gold_sql
    from app.services.trino_service import execute as trino_execute

    if periods_by_connection is None:
        linhas = await trino_execute(
            "SELECT connection_code, billing_period "
            f"FROM iceberg.{layers.GOLD}.{C1_TABLE} "
            "WHERE billing_period IS NOT NULL "
            "GROUP BY 1, 2 ORDER BY 1, 2",
            timeout_s=900,
        )
        agrupado: dict[str, list[str]] = {}
        for linha in linhas or []:
            agrupado.setdefault(str(linha.get("connection_code")), []).append(
                str(linha.get("billing_period"))
            )
        periods_by_connection = agrupado

    # O status de cada competência vem da classificação de cobertura — a fonte de
    # verdade de comparabilidade —, nunca de uma suposição local. Sem isso, C3
    # compararia um mês de ajuste com um mês operacional.
    statuses = await period_statuses()
    pares = comparable_period_pairs(periods_by_connection, statuses=statuses)
    todos = [
        (conexao, sorted(periodos)[i], sorted(periodos)[i + 1])
        for conexao, periodos in periods_by_connection.items()
        for i in range(len(periodos) - 1)
    ]
    pulados = [
        {
            "connection_code": c,
            "period_previous": a,
            "period_current": b,
            "previous_status": statuses.get((c, a), "NAO_MEDIDO"),
            "current_status": statuses.get((c, b), "NAO_MEDIDO"),
        }
        for c, a, b in todos
        if (c, a, b) not in pares
    ]

    # Cada execução usa o próprio namespace de fatias, para que uma fatia de um
    # par que deixou de ser comparável não sobreviva e entre na montagem.
    build = str(run_id) if run_id is not None else str(time.time_ns())

    fatias: list[str] = []
    vazias: list[str] = []
    total = len(pares) * len(driver_types)
    indice = 0
    for conexao, anterior, atual in pares:
        for driver in driver_types:
            indice += 1
            if progress:
                progress(indice, total, f"C3 {anterior}->{atual} ({driver})")
            tabela = slice_table_name(conexao, atual, driver, build=build)
            linhas = await materialize_c3_slice(
                connection_code=conexao,
                period_previous=anterior,
                period_current=atual,
                driver_type=driver,
                run_id=run_id,
                layer=layer,
                build=build,
            )
            if linhas:
                fatias.append(tabela)
            else:
                vazias.append(tabela)

    publicacao = await materialize_gold_sql(
        cube_code=C3_CUBE_CODE,
        table=C3_TABLE,
        select_sql=c3_union_sql(slice_tables=fatias, layer=layer),
        namespace=layers.GOLD,
        partition_columns=("period_current",),
        run_id=run_id,
    )

    return {
        "table": C3_TABLE,
        "pairs": len(pares),
        "skipped_pairs": pulados,
        "driver_types": list(driver_types),
        "slices": len(fatias),
        "empty_slices": len(vazias),
        "rows": publicacao.rows,
        "build": build,
    }


def reconciliation_sql(
    *,
    period_previous: str,
    period_current: str,
    driver_type: str = "service",
    c1_table: str = C1_TABLE,
    c3_table: str = C3_TABLE,
    c1_namespace: str = layers.GOLD,
    c3_namespace: str = layers.GOLD,
) -> str:
    """Prova que a soma das contribuições fecha o delta do par.

    Compara duas medidas independentes: o `total_delta` publicado em C3 e a
    diferença real entre as competências em C1. Se não fecharem, o driver não
    explica a variação e o cubo não pode ser servido.
    """
    return f"""
WITH c1_delta AS (
    SELECT ROUND(SUM(CASE WHEN billing_period = '{period_current}' THEN effective_cost ELSE 0 END)
               - SUM(CASE WHEN billing_period = '{period_previous}' THEN effective_cost ELSE 0 END), 2)
           AS delta_c1
    FROM iceberg.{c1_namespace}.{c1_table}
    WHERE billing_period IN ('{period_current}', '{period_previous}')
),
c3_delta AS (
    SELECT ROUND(SUM(delta), 2) AS delta_c3, COUNT(*) AS drivers, ROUND(MAX(total_delta), 2) AS total_declarado
    FROM iceberg.{c3_namespace}.{c3_table}
    WHERE period_current = '{period_current}' AND period_previous = '{period_previous}'
      AND driver_type = '{driver_type}'
)
SELECT c1.delta_c1, c3.delta_c3, c3.total_declarado, c3.drivers,
       ROUND(COALESCE(c3.delta_c3, 0) - COALESCE(c1.delta_c1, 0), 2) AS delta_entre_medidas,
       ROUND(c3.total_declarado - c1.delta_c1, 2) AS total_declarado_delta
FROM c1_delta c1 CROSS JOIN c3_delta c3
""".strip()
