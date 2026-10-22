"""Guarda do explorador SQL (C2): só SELECT, uma instrução, tabelas liberadas.

Não é um parser SQL completo. Ele rejeita o que não consegue provar seguro:
qualquer palavra de escrita fora de string ou comentário, mais de uma
instrução e tabelas que não estão na allowlist. Em dúvida, nega.
"""
import re

FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "merge", "drop", "alter", "create", "truncate",
    "grant", "revoke", "call", "execute", "set", "use", "copy", "vacuum", "analyze",
    "refresh", "comment", "rename", "replace",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENT = re.compile(r'"(?:[^"]|"")*"')
_TABLE_REF = re.compile(
    r'\b(?:from|join)\s+((?:"[^"]+"|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][\w$]*))*)',
    re.I,
)
_CTE_NAME = re.compile(r'(?:\bwith\b|,)\s*("?[A-Za-z_][\w$]*"?)\s+as\s*\(', re.I)


class SqlRejected(ValueError):
    """Consulta recusada. A mensagem pode ser mostrada ao usuário."""


def _strip_literals(sql: str) -> str:
    """Remove comentários e conteúdo de strings, para análise de palavras-chave."""
    no_comments = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql))
    no_strings = _STRING.sub("''", no_comments)
    return _QUOTED_IDENT.sub('""', no_strings)


def _normalize_name(raw: str) -> str:
    parts = [p.strip().strip('"').lower() for p in raw.split(".")]
    return ".".join(parts)


def referenced_tables(sql: str) -> set[str]:
    """Tabelas citadas após FROM/JOIN, normalizadas, sem CTEs definidas na própria consulta."""
    clean = _strip_literals(sql)
    ctes = {_normalize_name(m.group(1)) for m in _CTE_NAME.finditer(clean)}
    tables = {_normalize_name(m.group(1)) for m in _TABLE_REF.finditer(clean)}
    return tables - ctes


# Destinos são guardados sem schema (ex.: "dest_modelo_focus"). A tabela real fica
# no schema silver, e as consultas prontas do front usam gold. O nome simples
# libera só esses dois schemas, nenhum outro.
DESTINATION_SCHEMAS = ("silver", "gold")


def _expand_allowed(allowed_tables: set[str]) -> set[str]:
    expanded: set[str] = set()
    for table in allowed_tables:
        name = table.strip().lower()
        expanded.add(name)
        if "." not in name:
            expanded.update(f"{schema}.{name}" for schema in DESTINATION_SCHEMAS)
    return expanded


def check_read_only_select(sql: str, allowed_tables: set[str]) -> None:
    """Levanta SqlRejected se a consulta não for uma leitura permitida."""
    clean = _strip_literals(sql).strip().rstrip(";").strip()
    if not clean:
        raise SqlRejected("Consulta vazia.")
    if ";" in clean:
        raise SqlRejected("Envie uma instrução por vez.")

    first = clean.split(None, 1)[0].lower()
    if first not in ("select", "with"):
        raise SqlRejected("Só são permitidas consultas SELECT.")

    words = set(re.findall(r"[A-Za-z_]+", clean.lower()))
    banned = sorted(words.intersection(FORBIDDEN_KEYWORDS))
    if banned:
        raise SqlRejected(f"Palavra não permitida na consulta: {banned[0].upper()}.")

    allowed = _expand_allowed(allowed_tables)
    for table in sorted(referenced_tables(sql)):
        # aceita "schema.tabela" ou "catalogo.schema.tabela": compara os dois últimos nomes
        short = ".".join(table.split(".")[-2:])
        if short not in allowed and table not in allowed:
            raise SqlRejected(f"Tabela fora da lista liberada: {table}.")
