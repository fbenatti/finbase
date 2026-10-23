"""Normalização semântica das dimensões conformadas da Gold.

Fonte de verdade **única** para como uma dimensão é escrita na Gold. Existe
porque o mesmo vocabulário estava normalizado de formas diferentes em cada
consumidor — ou não estava normalizado:

- `Microsoft.Compute` (2.249.339 linhas) e `microsoft.compute` (2.228.455) eram
  agrupados separados, e a soma de um serviço aparecia dividida em duas linhas;
- `BRAZILSOUTH` (384.258 linhas) convivia com `brazilsouth` (6.857.678);
- ausência de região misturava reserva global (`Microsoft.Capacity`, que é
  global por natureza) com dado desconhecido;
- `resource_id` era usado como chave de join entre tenants sem conta e provider.

Regras (seção 4.1 do plano):

- a **Silver preserva o valor original**; normalizar é responsabilidade da Gold;
- o valor normalizado é **estável e determinístico** — nunca depende da ordem
  de chegada, do dataset ou do volume de linhas;
- a normalização **não altera nenhuma medida financeira**. `SUM(custo)` com e sem
  normalização é idêntico, porque só as chaves de agrupamento mudam.

Cada normalizador tem duas formas equivalentes:

- ``*_key(valor)`` — a forma em Python, usada por API, teste e serviço;
- ``sql_*(expr)`` — a mesma regra como expressão Trino, usada pelo
  materializador governado.

As duas precisam concordar. O teste ``test_semantic_normalization.py`` prova a
equivalência nos casos reais medidos em 23/09/2026.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# --- Marcadores de ausência -------------------------------------------------
#
# Ausência **nunca** vira zero, e os três estados abaixo são diferentes entre si
# (seção 4.4 do plano):
#
# - ``UNKNOWN``  — não sabemos a dimensão. É uma limitação de dado.
# - ``GLOBAL``   — sabemos que a cobrança não tem região por natureza (reserva,
#                  compromisso, imposto). Não é limitação.
# - ``NULL``     — a origem não trouxe o valor. Nunca é agrupado como se fosse
#                  um valor conhecido.

UNKNOWN = "UNKNOWN"
GLOBAL = "GLOBAL"

#: Provider canônico por prefixo de conexão/serviço. Vocabulário controlado.
PROVIDER_ALIASES: dict[str, str] = {
    "azure": "azure",
    "az": "azure",
    "microsoft": "azure",
    "gcp": "gcp",
    "google": "gcp",
    "aws": "aws",
    "amazon": "aws",
    "oci": "oci",
    "oracle": "oci",
    "ibm": "ibm",
    "mongodb": "mongodb",
    "atlas": "mongodb",
    "github": "github",
    "claude": "claude",
    "gemini": "gemini",
    "databricks": "databricks",
    "oci-prod": "oci",
}

#: Categorias de cobrança comprovadamente **globais** — não têm região por
#: natureza. `Purchase` e `Refund` de `Microsoft.Capacity` são ordens de reserva
#: (`/providers/microsoft.capacity/reservationorders/...`), que a Azure emite sem
#: região: R$ 6.482.044,66 de compra e −R$ 486.819,09 de estorno em 23/09/2026.
#: `Tax` e `Credit` seguem a mesma lógica contábil.
GLOBAL_CHARGE_CATEGORIES: frozenset[str] = frozenset(
    {"purchase", "refund", "tax", "credit", "roundingadjustment"}
)

#: Serviços cuja cobrança é global mesmo quando a categoria é `Usage`.
#: `Microsoft.Capacity` representa capacidade reservada; a região da reserva é
#: atributo do pedido, não da linha de consumo.
GLOBAL_SERVICES: frozenset[str] = frozenset({"microsoft.capacity"})


def _text(value: Any) -> str | None:
    """Texto canônico de entrada: `str`, sem espaços, vazio vira ``None``.

    `None`, `''`, `'null'`, `'None'` e `'undefined'` são ausência — nunca um
    valor. É o que impede uma sentinela textual de virar categoria na Gold.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "undefined", "nan", "nat"}:
        return None
    return text


def provider_key(value: Any) -> str:
    """Provider em minúsculas, vocabulário controlado. Ausente vira ``UNKNOWN``."""
    text = _text(value)
    if text is None:
        return UNKNOWN
    low = text.lower()
    if low in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[low]
    # `Microsoft.Compute` como provider não deveria acontecer, mas o prefixo
    # antes do ponto resolve o caso sem inventar vocabulário.
    if "." in low:
        head = low.split(".", 1)[0]
        if head in PROVIDER_ALIASES:
            return PROVIDER_ALIASES[head]
    return low


def service_key(value: Any) -> str:
    """Chave canônica do serviço: caixa e espaços não dividem o mesmo serviço.

    `Microsoft.Compute` e `microsoft.compute` produzem a mesma chave. Espaços são
    removidos por completo, e não só colapsados: `Microsoft. Compute` é o mesmo
    serviço que `Microsoft.Compute`, e o ponto é a chave, não a grafia — a grafia
    fica em :func:`service_display_name`. O valor original continua na Silver.
    """
    text = _text(value)
    if text is None:
        return UNKNOWN
    return "".join(text.lower().split())


def resource_type_key(value: Any) -> str:
    """Chave canônica do tipo de recurso: `trim(lower(resource_type))`.

    `resource_type` é NULL em 13.519.512/13.519.512 linhas de `resource_cost` em
    23/09/2026, então na prática esta chave devolve `UNKNOWN` para todo o livro
    de custo — o tipo do recurso vem do inventário (C2), não do custo.
    """
    text = _text(value)
    if text is None:
        return UNKNOWN
    return "".join(text.lower().split())


def canonical_resource_id(value: Any) -> str | None:
    """ID canônico de recurso: `trim(lower(resource_id))`.

    Devolve ``None`` quando não há ID — nunca uma string vazia. Uma linha de
    custo sem ID é um estado válido e precisa permanecer distinguível de uma
    linha cujo ID é literalmente ``''``.
    """
    text = _text(value)
    if text is None:
        return None
    return text.lower()


def is_valid_resource_id(value: Any) -> bool:
    """``True`` quando o valor é um ID de recurso de nuvem utilizável.

    Existe porque **presença não é validade**. Medido em 23/09/2026: as 5.294.906
    linhas de ajuste de uma conexão Azure (202504/202505) têm `resource_id` **preenchido**
    com valores como ``'320core'``, ``'100core'`` e ``'96core'``. Um teste de
    "`resource_id` é nulo?" devolve zero linhas problemáticas e o dado sujo passa
    silenciosamente — foi exatamente o que aconteceu na primeira versão da
    classificação de cobertura.

    Um ID canônico de nuvem é um caminho de provedor: começa com ``/`` e contém
    ``/providers/``. Qualquer outra coisa é identificador sintético de carga, não
    recurso, e não pode virar dimensão de C2.
    """
    text = canonical_resource_id(value)
    if text is None:
        return False
    return text.startswith("/") and "/providers/" in text


def is_valid_sub_account_id(value: Any) -> bool:
    """``True`` quando a conta tem forma de identificador de assinatura/conta.

    GUID de assinatura Azure (`4816bdc1-7cce-4c84-b9e9-4529014f3da1`) é o caso
    real. Identificador sintético de ajuste é curto e sem separador.
    """
    text = _text(value)
    if text is None:
        return False
    limpo = text.strip()
    if len(limpo) < 8:
        return False
    return any(ch.isdigit() for ch in limpo) or "-" in limpo


def billing_period_key(value: Any) -> str | None:
    """Competência exatamente ``YYYYMM``, ou ``None``.

    Aceita `2025-04`, `2025/04`, `202504` e `20250401` — todos os formatos que
    aparecem entre provedores — mas **descarta** o que não for um mês válido, em
    vez de inventar uma competência. Não converte para timestamp (seção 4.1).
    """
    text = _text(value)
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6:
        return None
    yyyymm = digits[:6]
    year, month = int(yyyymm[:4]), int(yyyymm[4:6])
    if not (1900 <= year <= 2999) or not (1 <= month <= 12):
        return None
    return yyyymm


def currency_code(value: Any) -> str | None:
    """Moeda em ISO maiúsculo. Ausente vira ``None`` — nunca ``BRL`` por omissão.

    Somar dinheiro exige moeda (seção 4.1). Assumir a moeda da maioria seria
    exatamente o erro que a regra proíbe.
    """
    text = _text(value)
    if text is None:
        return None
    return text.upper()


def is_global_region(value: Any) -> bool:
    """``True`` quando o valor já é o marcador de cobrança global."""
    text = _text(value)
    return text is not None and text.upper() == GLOBAL


def region_key(value: Any, *, charge_category: Any = None, service_name: Any = None) -> str:
    """Região canônica, distinguindo ``GLOBAL`` de ``UNKNOWN``.

    Ordem de decisão:

    1. valor presente → ``trim(lower(valor))``. `'global'` é preservado como
       ``GLOBAL``, porque o próprio provedor já o declarou global;
    2. valor ausente **e** cobrança comprovadamente global
       (`charge_category` em :data:`GLOBAL_CHARGE_CATEGORIES`, ou
       `service_name` em :data:`GLOBAL_SERVICES`) → ``GLOBAL``;
    3. qualquer outra ausência → ``UNKNOWN``.

    Sem a etapa 2, R$ 6.482.044,66 de compra de reserva seriam rotulados como
    "dado faltante" em vez de "cobrança sem região por natureza". Sem a etapa 3,
    dado faltante seria rotulado como global. Os dois erros são graves.
    """
    text = _text(value)
    if text is not None:
        low = " ".join(text.lower().split())
        return GLOBAL if low == "global" else low

    if service_key(service_name) in GLOBAL_SERVICES:
        return GLOBAL
    if (_text(charge_category) or "").lower() in GLOBAL_CHARGE_CATEGORIES:
        return GLOBAL
    return UNKNOWN


def is_allocatable_region(value: Any) -> bool:
    """``True`` quando a região é um local físico (nem ``GLOBAL`` nem ``UNKNOWN``).

    Um filtro de região na UI só pode oferecer locais físicos; somar ``GLOBAL``
    dentro de um filtro de região produziria um total que não é de região nenhuma.
    """
    text = _text(value)
    if text is None:
        return False
    return text.upper() not in {GLOBAL, UNKNOWN}


def service_display_name(values: Iterable[Any]) -> str:
    """Nome de exibição determinístico para um grupo de variantes de serviço.

    O plano exige `service_name_display` **determinístico**: a mesma entrada
    produz o mesmo rótulo, independentemente da ordem das linhas. A regra:

    1. só a forma com **mais de uma letra maiúscula** é grafia de marca legítima
       (`Microsoft.Compute` e não `microsoft.compute`);
    2. entre as elegíveis, a que aparece **mais vezes** vence;
    3. empate é resolvido por ordem lexical, para não depender da ordem de
       leitura do Trino.

    Exemplo medido: `microsoft.network` tem 3 variantes e vence a forma
    capitalizada, que é a grafia do provedor.
    """
    contagem: dict[str, int] = {}
    for raw in values:
        text = _text(raw)
        if text is None:
            continue
        contagem[text] = contagem.get(text, 0) + 1
    if not contagem:
        return UNKNOWN

    def _maiusculas(text: str) -> int:
        return sum(1 for ch in text if ch.isupper())

    candidatos = [t for t in contagem if _maiusculas(t) >= 2]
    if not candidatos:
        candidatos = list(contagem)
    return min(candidatos, key=lambda t: (-contagem[t], t))


def connection_key(code: Any, name: Any = None) -> str:
    """Identidade estável da conexão.

    O `code` é a chave durável (`azure-producao`); o `name` na Silver atual é o nome da
    **tabela Bronze** (`raw_azure_producao`), não o nome de negócio. Preferir o código
    evita que o rótulo mude quando a origem for renomeada.
    """
    return _text(code) or _text(name) or UNKNOWN


def cost_join_key(
    *, provider: Any, sub_account_id: Any, resource_id: Any
) -> tuple[str, ...] | None:
    """Chave segura de ligação entre custo, inventário e telemetria.

    ``resource_id`` **sozinho nunca é chave**: o mesmo ID pode existir em tenants
    ou providers diferentes. A chave é
    ``(provider, sub_account_id, canonical_resource_id)``.

    Devolve ``None`` quando falta qualquer componente — uma chave incompleta
    ligaria recursos de tenants distintos, que é o defeito que a regra 4.1
    proíbe. Quem chama deve tratar ``None`` como "não liga", não como "liga com
    nulo".
    """
    prov = provider_key(provider)
    conta = _text(sub_account_id)
    rid = canonical_resource_id(resource_id)
    if prov == UNKNOWN or conta is None or rid is None:
        return None
    return (prov, conta.lower(), rid)


# ── Expressões Trino equivalentes ────────────────────────────────────────────
#
# O materializador governado não passa por pandas: ele monta SQL. Estas funções
# devolvem a expressão SQL para a MESMA regra, para que não exista uma segunda
# definição de "serviço normalizado" escrita em SQL.


def _as_text(expr: str) -> str:
    """`CAST(expr AS VARCHAR)` textual, ou NULL literal."""
    return f"CAST({expr} AS VARCHAR)"


def _nullif_marker(expr: str) -> str:
    """Expressão que transforma sentinelas textuais em NULL real."""
    text = _as_text(expr)
    return (
        f"NULLIF(NULLIF(NULLIF(NULLIF(TRIM({text}), ''), 'null'), 'NULL'), 'None')"
    )


def sql_provider_key(expr: str) -> str:
    """Expressão Trino para :func:`provider_key`.

    Só normaliza caixa e ausência. O dicionário de aliases não é replicado em
    SQL: as conexões atuais emitem o vocabulário controlado direto (`azure`), e
    manter um segundo dicionário em SQL criaria a divergência que este módulo
    existe para eliminar. Alias novo entra por `PROVIDER_ALIASES` e é aplicado no
    serviço que lê a linha, não no agregador.
    """
    inner = _nullif_marker(expr)
    return (
        f"CASE WHEN {inner} IS NULL THEN '{UNKNOWN}' "
        f"ELSE LOWER(TRIM({inner})) END"
    )


def sql_service_key(expr: str) -> str:
    """Expressão Trino para :func:`service_key`."""
    inner = _nullif_marker(expr)
    return (
        f"CASE WHEN {inner} IS NULL THEN '{UNKNOWN}' "
        f"ELSE REGEXP_REPLACE(LOWER(TRIM({inner})), '\\s+', '') END"
    )


def sql_resource_type_key(expr: str) -> str:
    """Expressão Trino para :func:`resource_type_key`."""
    return sql_service_key(expr)


def sql_canonical_resource_id(expr: str) -> str:
    """Expressão Trino para :func:`canonical_resource_id` (preserva NULL)."""
    inner = _nullif_marker(expr)
    return f"CASE WHEN {inner} IS NULL THEN NULL ELSE LOWER(TRIM({inner})) END"


def sql_billing_period_key(expr: str) -> str:
    """Expressão Trino para :func:`billing_period_key`.

    Só aceita 6 dígitos que formem mês válido; qualquer outra coisa vira NULL em
    vez de uma competência inventada.
    """
    inner = _nullif_marker(expr)
    return (
        "CASE WHEN REGEXP_LIKE(REGEXP_REPLACE(CAST("
        f"{inner} AS VARCHAR), '[^0-9]', ''), '^[0-9]{{6}}') "
        "AND CAST(SUBSTR(REGEXP_REPLACE(CAST("
        f"{inner} AS VARCHAR), '[^0-9]', ''), 5, 2) AS INTEGER) BETWEEN 1 AND 12 "
        "AND CAST(SUBSTR(REGEXP_REPLACE(CAST("
        f"{inner} AS VARCHAR), '[^0-9]', ''), 1, 4) AS INTEGER) BETWEEN 1900 AND 2999 "
        "THEN SUBSTR(REGEXP_REPLACE(CAST("
        f"{inner} AS VARCHAR), '[^0-9]', ''), 1, 6) ELSE NULL END"
    )


def sql_currency_code(expr: str) -> str:
    """Expressão Trino para :func:`currency_code`."""
    inner = _nullif_marker(expr)
    return f"CASE WHEN {inner} IS NULL THEN NULL ELSE UPPER(TRIM({inner})) END"


def sql_region_key(
    expr: str, *, charge_category_expr: str | None = None, service_expr: str | None = None
) -> str:
    """Expressão Trino para :func:`region_key`.

    Exige as expressões de `charge_category` e `service_name` para poder
    distinguir ``GLOBAL`` de ``UNKNOWN`` — é o ponto da regra.
    """
    inner = _nullif_marker(expr)

    condicoes_globais: list[str] = []
    if service_expr is not None:
        svcs = ", ".join(f"'{s}'" for s in sorted(GLOBAL_SERVICES))
        condicoes_globais.append(
            "REGEXP_REPLACE(LOWER(TRIM(COALESCE(CAST("
            f"{service_expr} AS VARCHAR), ''))), '\\s+', '') IN ({svcs})"
        )
    if charge_category_expr is not None:
        cats = ", ".join(f"'{c}'" for c in sorted(GLOBAL_CHARGE_CATEGORIES))
        condicoes_globais.append(
            "LOWER(TRIM(COALESCE(CAST("
            f"{charge_category_expr} AS VARCHAR), ''))) IN ({cats})"
        )

    if condicoes_globais:
        global_por_natureza = " OR ".join(condicoes_globais)
        ramo_ausente = f"CASE WHEN {global_por_natureza} THEN '{GLOBAL}' ELSE '{UNKNOWN}' END"
    else:
        # Sem as expressões de contexto não há como provar que a ausência é
        # global, então a ausência é limitação de dado — o default seguro.
        ramo_ausente = f"'{UNKNOWN}'"

    normalizado = f"REGEXP_REPLACE(LOWER(TRIM({inner})), '\\s+', '')"
    return (
        f"CASE WHEN {inner} IS NULL THEN {ramo_ausente} "
        f"WHEN {normalizado} = 'global' THEN '{GLOBAL}' "
        f"ELSE {normalizado} END"
    )


def sql_cost_join_key(
    *, provider_expr: str, sub_account_expr: str, resource_id_expr: str
) -> str:
    """Expressão Trino para a chave segura de ligação.

    Concatena com separador `|` e devolve NULL quando qualquer componente falta.
    Uma chave parcial nunca liga.
    """
    prov = sql_provider_key(provider_expr)
    conta = _nullif_marker(sub_account_expr)
    rid = sql_canonical_resource_id(resource_id_expr)
    return (
        f"CASE WHEN {conta} IS NULL OR {rid} IS NULL THEN NULL "
        f"ELSE CONCAT({prov}, '|', LOWER(TRIM({conta})), '|', {rid}) END"
    )


# ── Garantia de normalização antes de somar dinheiro ─────────────────────────


class CurrencyMixedError(ValueError):
    """Mais de uma moeda no mesmo escopo de soma. Somar seria inválido."""


def assert_single_currency(rows: Iterable[Mapping[str, Any]], *, currency_field: str = "currency") -> str:
    """Valida que um escopo tem exatamente uma moeda e a devolve.

    Chamado antes de qualquer `SUM` financeiro na Gold. A regra 4.1 é explícita:
    não somar BRL e USD na mesma medida. Se houver duas moedas, o certo é
    **falhar**, não converter implicitamente.
    """
    moedas = {currency_code(r.get(currency_field)) for r in rows}
    moedas.discard(None)
    if not moedas:
        raise CurrencyMixedError("nenhuma moeda informada no escopo")
    if len(moedas) > 1:
        raise CurrencyMixedError(f"escopo com múltiplas moedas: {', '.join(sorted(moedas))}")
    return moedas.pop()
