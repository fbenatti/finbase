"""Sanitized Azure OpenAI PTU telemetry helper. Never logs prompts or responses."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_event(*, telemetry_client_id: str, project_key: str, subproject_key: str,
                deployment_name: str, model_name: str | None = None, api_operation: str,
                stream: bool, usage: dict[str, Any] | None, status_code: int,
                is_spillover: bool = False, latency_ms: float | None = None,
                azure_account_resource_id: str | None = None) -> dict[str, Any]:
    usage = usage or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cached = int(usage.get("cached_tokens") or usage.get("cached_input_tokens") or 0)
    reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
    total = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "occurred_at": _now(),
        "telemetry_client_id": telemetry_client_id,
        "project_key": project_key,
        "subproject_key": subproject_key,
        "environment": "production",
        "azure_account_resource_id": azure_account_resource_id,
        "deployment_name": deployment_name,
        "model_name": model_name,
        "api_operation": api_operation,
        "stream": stream,
        "status_code": status_code,
        "is_spillover": is_spillover,
        "request_count": 1,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
        "latency_ms": latency_ms,
    }


async def instrumented_chat_completions(client, publish: Callable[[dict[str, Any]], Any], /, **kwargs):
    started = time.perf_counter()
    try:
        response = await client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage or {})
        event = build_event(
            telemetry_client_id=kwargs.pop("_telemetry_client_id", "replace-me"),
            project_key=kwargs.pop("_project_key", ""),
            subproject_key=kwargs.pop("_subproject_key", ""),
            deployment_name=kwargs.get("model"),
            api_operation="chat.completions",
            stream=False,
            usage=usage_dict,
            status_code=200,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        try:
            await publish(event)
        except Exception:
            pass
        return response
    except Exception as exc:
        event = build_event(
            telemetry_client_id="replace-me", project_key="", subproject_key="",
            deployment_name=kwargs.get("model"), api_operation="chat.completions",
            stream=False, usage={}, status_code=getattr(exc, "status_code", 500) or 500,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        try:
            await publish(event)
        except Exception:
            pass
        raise


async def instrumented_responses_stream(client, publish: Callable[[dict[str, Any]], Any], /, **kwargs):
    started = time.perf_counter()
    usage: dict[str, Any] = {}
    stream = await client.responses.create(stream=True, **kwargs)
    async for event in stream:
        if getattr(event, "type", "") == "response.completed":
            resp = getattr(event, "response", None)
            usage = getattr(resp, "usage", None) or {}
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
        yield event
    payload = build_event(
        telemetry_client_id="replace-me", project_key="", subproject_key="",
        deployment_name=kwargs.get("model"), api_operation="responses",
        stream=True, usage=usage, status_code=200,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
    try:
        await publish(payload)
    except Exception:
        pass
