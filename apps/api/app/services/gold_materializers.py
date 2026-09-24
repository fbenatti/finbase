"""Materializadores registrados por chave — allowlist fechada.

Os cubos `finops_*` não são um único `SELECT` sobre as fontes: a cobertura é
produzida em Python, o C1 tem dois estágios, o C2 é construído por competência ×
conexão × bucket e depende do cubo de cobertura. Registrá-los em `gold_rule` exige
que a regra declare **como** materializar, e não só **o quê**.

A coluna `gold_rule.materializer_key` guarda uma chave deste dicionário. O que ela
**não** guarda:

- nome de função Python;
- módulo, atributo ou expressão;
- qualquer string que vire `importlib`, `eval` ou `getattr`.

A resolução é por dicionário: chave desconhecida é erro explícito, nunca um
fallback silencioso para o caminho genérico de pivô. Um cubo multi-estágio
materializado pelo caminho errado publicaria uma tabela diferente da que o nome
promete.

Ordem de execução entre cubos: `finops_period_coverage` **antes** de C3, porque C3
recusa par não comparável e a comparabilidade vem da cobertura publicada. A
dependência é declarada em `DEPENDENCIES` e o executor respeita a ordem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.services import lakehouse_layers as layers

#: Chaves de materializador. Vocabulário fechado.
MATERIALIZER_COVERAGE = "finops_period_coverage"
MATERIALIZER_C1 = "finops_cost_monthly"
MATERIALIZER_C2 = "finops_cost_resource_monthly"
MATERIALIZER_C3 = "finops_cost_change_driver"


@dataclass(frozen=True)
class MaterializerSpec:
    """Como materializar um cubo multi-estágio."""

    key: str
    physical_table: str
    namespace: str
    #: Códigos de cubo que precisam estar publicados antes deste.
    depends_on: tuple[str, ...] = ()
    #: Fontes declaradas para o manifesto e para o catálogo.
    sources: tuple[str, ...] = ()
    description: str = ""


def _run_coverage(**kwargs: Any) -> Awaitable[dict[str, Any]]:
    from app.services.period_coverage import materialize_coverage

    return materialize_coverage(run_id=kwargs.get("run_id"))


def _run_c1(**kwargs: Any) -> Awaitable[dict[str, Any]]:
    from app.services.finops_cost_monthly import materialize_c1

    return materialize_c1(
        run_id=kwargs.get("run_id"),
        expected_total=kwargs.get("expected_total"),
        progress=kwargs.get("progress"),
    )


def _run_c2(**kwargs: Any) -> Awaitable[dict[str, Any]]:
    from app.services.finops_cost_resource import materialize_c2

    return materialize_c2(
        run_id=kwargs.get("run_id"),
        expected_total=kwargs.get("expected_total"),
        progress=kwargs.get("progress"),
    )


def _run_c3(**kwargs: Any) -> Awaitable[dict[str, Any]]:
    from app.services.finops_cost_driver import materialize_c3

    return materialize_c3(run_id=kwargs.get("run_id"), progress=kwargs.get("progress"))


#: Registro por chave. Só estas quatro chaves são executáveis, e cada uma aponta
#: para uma função do código — não há indireção por string.
MATERIALIZERS: dict[str, tuple[MaterializerSpec, Callable[..., Awaitable[dict[str, Any]]]]] = {
    MATERIALIZER_COVERAGE: (
        MaterializerSpec(
            key=MATERIALIZER_COVERAGE,
            physical_table="finops_period_coverage",
            namespace=layers.GOLD,
            depends_on=(),
            sources=("silver.resource_cost",),
            description="Classificação de cobertura por conexão e competência.",
        ),
        _run_coverage,
    ),
    MATERIALIZER_C1: (
        MaterializerSpec(
            key=MATERIALIZER_C1,
            physical_table="finops_cost_monthly",
            namespace=layers.GOLD,
            depends_on=(MATERIALIZER_COVERAGE,),
            sources=("silver.resource_cost",),
            description="Custo mensal por escopo e dimensões conformadas (C1).",
        ),
        _run_c1,
    ),
    MATERIALIZER_C2: (
        MaterializerSpec(
            key=MATERIALIZER_C2,
            physical_table="finops_cost_resource_monthly",
            namespace=layers.GOLD,
            depends_on=(MATERIALIZER_C1,),
            sources=("silver.resource_cost", "silver.resource_inventory"),
            description="Custo por recurso com contexto de inventário vigente (C2).",
        ),
        _run_c2,
    ),
    MATERIALIZER_C3: (
        MaterializerSpec(
            key=MATERIALIZER_C3,
            physical_table="finops_cost_change_driver",
            namespace=layers.GOLD,
            depends_on=(MATERIALIZER_C1, MATERIALIZER_COVERAGE),
            sources=("gold.finops_cost_monthly", "gold.finops_period_coverage"),
            description="Drivers da variação entre competências comparáveis (C3).",
        ),
        _run_c3,
    ),
}


def is_registered(key: str | None) -> bool:
    """``True`` quando a chave existe na allowlist."""
    return bool(key) and str(key) in MATERIALIZERS


def spec_for(key: str) -> MaterializerSpec:
    """Especificação de uma chave. Chave desconhecida levanta."""
    try:
        return MATERIALIZERS[str(key)][0]
    except KeyError as exc:
        raise KeyError(
            f"materializer_key desconhecido: {key!r}. "
            f"Registrados: {', '.join(sorted(MATERIALIZERS))}"
        ) from exc


def runner_for(key: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Função de materialização de uma chave. Chave desconhecida levanta."""
    try:
        return MATERIALIZERS[str(key)][1]
    except KeyError as exc:
        raise KeyError(
            f"materializer_key desconhecido: {key!r}. "
            f"Registrados: {', '.join(sorted(MATERIALIZERS))}"
        ) from exc


def execution_order(keys: list[str]) -> list[str]:
    """Ordena chaves de forma que cada dependência venha antes do dependente.

    Uma chave fora da allowlist é descartada aqui — quem valida é
    :func:`runner_for`, que levanta. Ordenar não pode esconder chave inválida.
    """
    validas = [k for k in keys if is_registered(k)]
    ordenadas: list[str] = []
    pendentes = list(dict.fromkeys(validas))
    while pendentes:
        progresso = False
        for chave in list(pendentes):
            deps = spec_for(chave).depends_on
            if all(d in ordenadas or d not in validas for d in deps):
                ordenadas.append(chave)
                pendentes.remove(chave)
                progresso = True
        if not progresso:
            # Ciclo declarado: falha explícita em vez de ordem arbitrária.
            raise RuntimeError(
                "ciclo de dependência entre materializadores: " + ", ".join(pendentes)
            )
    return ordenadas


async def materialize_by_keys(
    keys: list[str],
    *,
    run_id: int | None = None,
    expected_total: float | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Executa os materializadores das chaves, na ordem de dependência.

    Cada resultado é agregado no retorno. O executor **não** engole falha: um cubo
    que não reconcilia interrompe a fila, e os cubos seguintes não são publicados
    sobre dado não reconciliado.

    Quem precisa distinguir **qual** chave falhou deve iterar `execution_order` e
    chamar `runner_for` diretamente — é o que `materialize_gold_cubes` faz, para não
    marcar como `ERROR` um cubo que já publicou.
    """
    resultados: dict[str, Any] = {}
    for chave in execution_order(keys):
        resultados[chave] = await runner_for(chave)(
            run_id=run_id,
            expected_total=expected_total,
            progress=progress,
        )
    return resultados
