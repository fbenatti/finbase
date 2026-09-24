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

C2_COST_TABLE = "finops_cost_resource_cost"
C2_INVENTORY_TABLE = "finops_cost_resource_inventory"

C2_TABLE = "finops_cost_resource_monthly"
C2_CUBE_CODE = "finops_cost_resource_monthly"

#: Prefixo das tabelas auxiliares de bucket. Silver: agregado intermediário,
#: removido no fim da fatia.
C2_BUCKET_PREFIX = "finops_cost_resource_bucket"

#: Colunas publicadas em `finops_cost_resource_monthly`, com o tipo **explícito**.
#:
#: O tipo é declarado, e não inferido: o C2 é construído por fatia, e a primeira
#: fatia de um período de ajuste infere tipos diferentes dos de um mês operacional
#: (`billed_cost` saiu `double` numa fatia e `bigint` na outra). Sem cast explícito,
#: a fatia seguinte falha por tipo incompatível no publicador por partição.
#:
#: `charge_days` **não** está no contrato: era `COUNT(DISTINCT data)` e não
#: sobrevive a uma soma entre buckets. Publicá-lo exigiria um segundo passo sobre a
#: Silver por recurso, que é o custo que o particionamento por bucket evita.
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
    ("rows_without_tag", "bigint"),
    ("resource_id_status", "varchar"),
)

#: Nomes das colunas de contrato, na ordem de publicação.
_C2_OUTPUT_COLUMNS: tuple[str, ...] = tuple(nome for nome, _ in _C2_OUTPUT_TYPES)


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




def c2_projection_sql(select_sql: str) -> str:
    """Projeta o resultado da montagem no contrato, com tipo explícito.

    É o que torna a publicação por fatia idempotente entre períodos de naturezas
    diferentes: mês operacional e mês de ajuste produzem o mesmo schema.
    """
    colunas = ", ".join(
        f"CAST({nome} AS {tipo}) AS {nome}" for nome, tipo in _C2_OUTPUT_TYPES
    )
    return f"SELECT {colunas} FROM ({select_sql}) c2_raw"


async def _publish_c2_slice(
    *,
    billing_period: str,
    connection_code: str,
    frame: Any,
    run_id: int | None,
) -> int:
    """Publica uma fatia `(competência, conexão)` de forma incremental.

    O C2 é construído por fatia, então cada execução substitui **uma** e precisa
    preservar as outras. `publish_gold_cube` faz swap de tabela inteira, o que
    apagaria as demais competências.

    A troca usa o publicador por fatia do projeto
    (`write_destination_staged` + `publish_staged_partition`): adota os arquivos
    Parquet numa transação Iceberg que substitui só
    `(_billing_period, source_connection_id)`. Leitores veem a fatia anterior ou a
    nova, nunca um lote parcial.

    A primeira publicação não tem fatia a substituir e também passa pelo publicador
    por fatia, que cria a tabela quando ela não existe.
    """
    from app.services import iceberg_service as iceberg
    from app.services.gold_governance import record_frame_publication

    valores = frame["source_connection_id"].dropna().unique().tolist()
    conexoes = [int(v) for v in valores] or [0]
    total = 0
    for conexao in conexoes:
        parte = frame[frame["source_connection_id"] == conexao]
        if parte.empty:
            continue
        staged = iceberg.write_destination_staged(
            C2_TABLE, parte, partition_col="_billing_period", namespace=layers.GOLD
        )
        try:
            total += iceberg.publish_staged_partition(
                C2_TABLE,
                staged,
                billing_period=billing_period,
                connection_id=conexao,
                namespace=layers.GOLD,
            )
        except Exception:
            try:
                iceberg.cleanup_staged_table(staged, namespace=layers.GOLD)
            except Exception:  # noqa: BLE001
                pass
            raise

    await record_frame_publication(
        cube_code=C2_CUBE_CODE,
        physical_table=C2_TABLE,
        rows_written=int(len(frame)),
        namespace=layers.GOLD,
        run_id=run_id,
        source_tables=[f"silver.resource_cost", f"silver.{C2_INVENTORY_TABLE}"],
    )
    return int(len(frame))


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


#: Agregação do bucket, em pandas. As chaves de agrupamento são as do grão do cubo,
#: e a partição por bucket garante que **nenhum grupo se divide** entre buckets: o
#: bucket é função pura da chave canônica do recurso. Por isso a concatenação dos
#: agregados é igual ao agregado da fatia inteira.
_C2_GROUP_KEYS: tuple[str, ...] = (
    "billing_period", "provider", "connection_code", "sub_account_id",
    "canonical_resource_id", "source_connection_id", "charge_category", "currency",
)


def aggregate_c2_bucket(frame: Any) -> Any:
    """Agrega as linhas cruas de um bucket no grão do cubo.

    O que **não** é aditivo fica de fora do cubo: `charge_days` era
    `COUNT(DISTINCT data)` e não sobrevive a uma soma entre buckets. Antes ele era
    publicado e ninguém o usava para decidir; mantê-lo exigiria uma segunda passada
    sobre a Silver por recurso, que é o custo que este desenho evita.
    """
    import pandas as pd

    if frame.empty:
        return frame
    for coluna in (
        "provider", "connection_code", "sub_account_id", "canonical_resource_id",
        "charge_category", "currency", "cost_resource_name", "cost_resource_group_id",
        "service_key", "service_category", "region_key",
    ):
        if coluna in frame.columns:
            frame[coluna] = frame[coluna].astype("string")

    agregado = (
        frame.groupby(list(_C2_GROUP_KEYS), dropna=False)
        .agg(
            effective_cost=("effective_cost", "sum"),
            list_cost=("list_cost", "sum"),
            pricing_quantity=("pricing_quantity", "sum"),
            rows=("effective_cost", "size"),
            first_charge_day=("charge_day", "min"),
            last_charge_day=("charge_day", "max"),
            rows_without_tag=("without_tag", "sum"),
            cost_resource_name=("cost_resource_name", "max"),
            cost_resource_group_id=("cost_resource_group_id", "max"),
            service_key=("service_key", "max"),
            service_category=("service_category", "max"),
            region_key=("region_key", "max"),
        )
        .reset_index()
    )
    return agregado


def enrich_c2_aggregate(agregado: Any, inventario: Any) -> Any:
    """Junta inventário vigente e deriva as colunas do contrato.

    O `merge` é 1:1 (o inventário vem deduplicado por competência), então a contagem
    de linhas é preservada — e é a reconciliação do total que confirma isso.
    """
    import pandas as pd

    inv = inventario if inventario is not None else pd.DataFrame()
    if not inv.empty:
        agregado = agregado.merge(
            inv,
            on=["provider", "sub_account_id", "canonical_resource_id", "billing_period"],
            how="left",
        )
    else:
        for coluna in (
            "inventory_resource_name", "inventory_resource_type",
            "inventory_resource_sku", "inventory_resource_group_id",
            "inventory_resource_state", "operating_system",
            "inventory_snapshot_period", "inventory_snapshots", "inventory_status",
        ):
            agregado[coluna] = None

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
    # Sem snapshot vigente o status é explícito, e os atributos acima ficam nulos.
    agregado["inventory_status"] = agregado["inventory_status"].fillna(
        "NO_TEMPORAL_SNAPSHOT"
    )
    agregado["inventory_snapshots"] = (
        pd.to_numeric(agregado["inventory_snapshots"], errors="coerce")
        .fillna(0)
        .astype("int64")
    )
    agregado["billed_cost"] = None
    agregado["contracted_cost"] = None
    id_texto = agregado["canonical_resource_id"].astype("string")
    valido = id_texto.str.startswith("/", na=False) & id_texto.str.contains(
        "/providers/", regex=False, na=False
    )
    agregado["resource_id_status"] = valido.map({True: "VALIDO", False: "NAO_RECURSO"})
    return agregado


async def materialize_c2_slice(
    *,
    billing_period: str,
    connection_code: str,
    buckets: int = DEFAULT_BUCKETS,
    run_id: int | None = None,
    layer: str = layers.SILVER,
    inventory: Any | None = None,
) -> dict[str, Any]:
    """Constrói uma fatia `(competência, conexão)` bucket a bucket e a publica.

    Sequência por bucket: projetar (SQL), agregar (pandas), **descartar as linhas
    cruas**. Ao fim, os agregados dos buckets são concatenados e publicados uma única
    vez por fatia.

    Por que não uma consulta só: medido neste ambiente, a agregação por recurso de
    uma competência inteira derruba o worker, e o `UNION ALL` de 16 buckets numa
    consulta também ("No worker nodes available"). Uma consulta por bucket é o que
    funciona, e publicar uma vez por fatia evita a tempestade de operações de
    catálogo — a versão anterior criava uma tabela por bucket, ~1.100 no total, e
    esgotava o catálogo.

    Nenhum resultado de bucket é persistido: o que se acumula é o agregado em
    memória, e a única tabela criada é o staging da fatia.
    """
    import pandas as pd

    from app.services.trino_service import execute as trino_execute

    total_buckets = bucket_count(buckets=buckets)

    if inventory is None:
        inventario = pd.DataFrame(
            await trino_execute(inventory_temporal_sql(layer=layer), timeout_s=1800)
        )
    else:
        inventario = inventory

    agregados: list[Any] = []
    linhas_cruas = 0
    for bucket in range(total_buckets):
        linhas = await trino_execute(
            c2_bucket_projection_sql(
                layer=layer,
                billing_period=billing_period,
                bucket=bucket,
                buckets=total_buckets,
                connection_code=connection_code,
            ),
            timeout_s=1800,
        )
        if not linhas:
            continue
        frame = pd.DataFrame(linhas)
        linhas_cruas += len(frame)
        agregados.append(aggregate_c2_bucket(frame))
        del frame

    if not agregados:
        return {
            "billing_period": billing_period,
            "connection_code": connection_code,
            "rows": 0,
            "source_rows": 0,
            "buckets": total_buckets,
        }

    agregado = pd.concat(agregados, ignore_index=True) if len(agregados) > 1 else agregados[0]
    del agregados
    agregado = enrich_c2_aggregate(agregado, inventario)

    # Colunas do contrato, na ordem declarada.
    for nome, _ in _C2_OUTPUT_TYPES:
        if nome not in agregado.columns:
            agregado[nome] = None
    agregado = agregado[[nome for nome, _ in _C2_OUTPUT_TYPES]]
    agregado["_billing_period"] = agregado["billing_period"]

    linhas_publicadas = await _publish_c2_slice(
        billing_period=billing_period,
        connection_code=connection_code,
        frame=agregado,
        run_id=run_id,
    )
    return {
        "billing_period": billing_period,
        "connection_code": connection_code,
        "rows": linhas_publicadas,
        "source_rows": linhas_cruas,
        "buckets": total_buckets,
    }


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
    import pandas as pd

    from app.services.trino_service import execute as trino_execute

    inventario_frame = pd.DataFrame(
        await trino_execute(inventory_temporal_sql(layer=layer), timeout_s=1800)
    )

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
                inventory=inventario_frame,
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


CURRENT_SNAPSHOT = "CURRENT_SNAPSHOT"
HISTORICAL_SNAPSHOT = "HISTORICAL_SNAPSHOT"
NO_TEMPORAL_SNAPSHOT = "NO_TEMPORAL_SNAPSHOT"

#: Valores possíveis de `inventory_status`. Fechados e explícitos.
INVENTORY_STATUSES: tuple[str, ...] = (
    CURRENT_SNAPSHOT,
    HISTORICAL_SNAPSHOT,
    NO_TEMPORAL_SNAPSHOT,
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


def c2_select_sql(
    *, layer: str = layers.SILVER, billing_period: str | None = None,
    connection_code: str | None = None,
) -> str:
    """SELECT completo do C2 numa consulta, para inspeção e teste.

    `materialize_c2` **não** usa este caminho: ele projeta por bucket e agrega em
    pandas, porque uma consulta só derruba o worker neste ambiente. Aqui a utilidade
    é outra — permitir conferir o resultado contra `resource_cost` numa consulta
    auditável.
    """
    return c2_projection_sql(
        c2_bucket_projection_sql(
            layer=layer,
            billing_period=billing_period or "000000",
            bucket=0,
            buckets=1,
            connection_code=connection_code,
        )
    )


def quality_columns() -> tuple[str, ...]:
    """Colunas de qualidade que acompanham o valor.

    `inventory_status` é a cobertura de join custo↔inventário vigente;
    `resource_id_status` separa recurso de linha de ajuste.
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
    return frozenset({"rows_without_tag", "inventory_snapshots"})


def c2_bucket_projection_sql(
    *,
    layer: str = layers.SILVER,
    billing_period: str,
    bucket: int,
    buckets: int = DEFAULT_BUCKETS,
    connection_code: str | None = None,
) -> str:
    """Projeta as linhas cruas de **um** bucket, sem agregar.

    Existe porque o Trino deste ambiente não sustenta nem uma agregação por recurso
    de uma competência inteira, nem o `UNION ALL` de 16 buckets numa consulta
    (medido: "No worker nodes available" e queda dos dois workers). O que funciona —
    e foi medido — é a consulta de **um** bucket por vez: 4,5 s para 832 grupos.

    Como o bucket é função pura da chave canônica do recurso, todos os registros de
    um recurso caem no mesmo bucket. Agregar bucket a bucket e concatenar produz
    exatamente o mesmo resultado que agregar a fatia inteira — e permite descartar
    as linhas cruas a cada bucket em vez de mantê-las todas no processo.
    """
    conexao = ""
    if connection_code:
        conexao = (
            " AND COALESCE(_source_connection_code, _source_connection_name) = "
            f"{_quote_literal(connection_code)}"
        )
    rid = sql_canonical_resource_id("resource_id")
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
    {sql_region_key("region_id", charge_category_expr="charge_category", service_expr="service_name")} AS region_key,
    TRY_CAST(effective_cost AS DOUBLE) AS effective_cost,
    TRY_CAST(list_cost AS DOUBLE) AS list_cost,
    TRY_CAST(pricing_quantity AS DOUBLE) AS pricing_quantity,
    DATE(charge_period_start) AS charge_day,
    CASE WHEN NULLIF(TRIM(CAST(tags_json AS VARCHAR)), '') IS NULL
           OR TRIM(CAST(tags_json AS VARCHAR)) IN ('{{}}', '[]')
         THEN 1 ELSE 0 END AS without_tag
FROM iceberg.{layer}.resource_cost
WHERE CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) = {_quote_literal(billing_period)}{conexao}
  AND {bucket_predicate(rid, bucket, buckets=buckets)}
""".strip()


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
