"""Cliente do Jev (TypeSafe System One).

O Jev **não é um chat**: é um serviço de decisão tipada. Ele recebe um `state`
(a situação) e um conjunto de `questions` com critérios nomeados, e devolve uma
posição tipada. Dois primitivos interessam ao plano:

- **choice**: escolhe uma opção entre até 255;
- **score**: posição ponderada num espectro ordenado;
- **noul**: probabilidade de "sim" para uma condição.

A distinção importa: `noul` **não tem confiança separada** — o próprio valor é a
probabilidade. E `confidence` de choice/score mede **concentração da distribuição**,
não correção da decisão: um `noul` perto de 0,5 significa probabilidade parecida
para sim e não, não intensidade média.

Papel no sistema (P12 do plano): **assistir**, nunca decidir. As regras que o Jev
não pode violar:

- não muda `INVALID` para `VALID`;
- falha do serviço **não** apaga candidato;
- modelo, versão e motivo ficam auditados.

Endpoint, caminho e timeout são configuração — nada chumbado. O token fica em
`secret_json` e só é usado aqui.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PATH = "/v1/systemone"
DEFAULT_TIMEOUT_S = 90.0

# Tipos de questão aceitos pelo serviço. Um tipo fora disto é erro do chamador,
# não do serviço — melhor falhar aqui do que enviar payload inválido.
QUESTION_TYPES = ("choice", "score", "noul")


# Cache por hash do pedido. A skill é explícita: "changing a weight or display
# filter need not rerun inference when evidence and question meanings are
# unchanged". Evita pagar duas vezes pela mesma pergunta.
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LIMIT = 512


def _cache_get(chave: str) -> dict[str, Any] | None:
    return _CACHE.get(chave)


def _cache_put(chave: str, valor: dict[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_LIMIT:
        # Descarte simples: o mais antigo sai.
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[chave] = valor


class JevError(RuntimeError):
    """Falha ao consultar o Jev. Nunca deve derrubar o fluxo do chamador."""


def _resolver(integration) -> tuple[str, str, str, float]:
    """Endpoint, caminho, token e timeout, da configuração."""
    config = dict(getattr(integration, "config_json", None) or {})
    segredo = dict(getattr(integration, "secret_json", None) or {})
    base = str(config.get("base_url") or "").strip().rstrip("/")
    caminho = str(config.get("path_systemone") or DEFAULT_PATH).strip()
    token = str(segredo.get("api_key") or "").strip()
    try:
        timeout = float(config.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    if not base:
        raise JevError("O Jev não tem `base_url` configurado.")
    if not token:
        raise JevError("O Jev não tem token configurado.")
    return base, caminho or DEFAULT_PATH, token, timeout


def hash_entrada(state: str, questions: dict[str, Any]) -> str:
    """Hash do pedido, para cache e para auditoria de reprodutibilidade.

    Duas execuções com o mesmo estado e as mesmas perguntas produzem o mesmo
    hash — é o que permite dizer que a decisão foi reproduzida, não refeita.
    """
    base = json.dumps(
        {"state": state, "questions": questions},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def validar_questions(questions: dict[str, Any]) -> None:
    """Recusa pergunta malformada antes de gastar uma chamada."""
    if not isinstance(questions, dict) or not questions:
        raise JevError("`questions` precisa ter ao menos uma pergunta.")
    for nome, spec in questions.items():
        if not isinstance(spec, dict):
            raise JevError(f"Pergunta '{nome}' precisa ser um objeto.")
        tipo = str(spec.get("type") or "").lower()
        if tipo not in QUESTION_TYPES:
            raise JevError(
                f"Pergunta '{nome}': tipo '{tipo}' inválido. Aceitos: {', '.join(QUESTION_TYPES)}."
            )
        if not spec.get("criteria"):
            raise JevError(f"Pergunta '{nome}': falta `criteria`.")


async def ask(
    integration,
    state: str,
    questions: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consulta o Jev e devolve a decisão tipada, sem alterar o fato.

    Devolve sempre um dicionário com `status`:
    - `OK` com `answers` quando o serviço respondeu;
    - `UNAVAILABLE` com `reason` quando falhou.

    O chamador **decide o que fazer** com isso. A função não levanta para falha de
    rede: o plano exige que falha do serviço não apague candidato.
    """
    validar_questions(questions)
    chave = hash_entrada(state, questions)
    guardado = _cache_get(chave)
    if guardado is not None:
        return {**guardado, "cached": True}
    base, caminho, token, timeout = _resolver(integration)
    # O serviço exige `model`. Vem da configuração, nunca chumbado.
    modelo = str(dict(getattr(integration, "config_json", None) or {}).get("model") or "").strip()
    if not modelo:
        raise JevError(
            "O Jev exige `model`. Configure em `config.model` "
            "(ex.: `jev-latest`; a lista sai de GET /v1/models)."
        )
    corpo: dict[str, Any] = {"model": modelo, "state": state, "questions": questions}
    if context:
        corpo["context"] = context

    try:
        async with httpx.AsyncClient(timeout=timeout) as cliente:
            resposta = await cliente.post(
                f"{base}{caminho}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=corpo,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Jev inacessível: %s", exc)
        return {
            "status": "UNAVAILABLE",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
            "input_hash": hash_entrada(state, questions),
        }

    if resposta.status_code >= 400:
        return {
            "status": "UNAVAILABLE",
            "reason": f"HTTP {resposta.status_code}: {resposta.text[:300]}",
            "input_hash": hash_entrada(state, questions),
        }

    try:
        dados = resposta.json()
    except Exception:  # noqa: BLE001
        return {
            "status": "UNAVAILABLE",
            "reason": f"Resposta não é JSON: {resposta.text[:200]}",
            "input_hash": hash_entrada(state, questions),
        }

    resultado = {
        "status": "OK",
        "answers": dados,
        "input_hash": chave,
        # A versão do serviço entra na auditoria: o plano exige modelo/versão.
        "model": (dados.get("model") if isinstance(dados, dict) else None),
        "elapsed_ms": dados.get("elapsed_ms") if isinstance(dados, dict) else None,
    }
    _cache_put(chave, resultado)
    return resultado


async def smoke_test(integration) -> dict[str, Any]:
    """Chamada mínima que prova que o serviço responde.

    Usa uma pergunta trivial de `choice`: um `200` com resposta tipada prova
    endpoint, token e contrato de uma vez — melhor que checar um `/health` que
    pode existir sem o token valer.
    """
    return await ask(
        integration,
        "Connectivity check: classify this sentence.",
        {
            "category": {
                "type": "choice",
                "instructions": "Which team should handle this request",
                "criteria": {
                    "billing": "Payment or subscription issues",
                    "technical": "Bugs or integration problems",
                    "other": "Nothing else fits",
                },
            }
        },
    )
