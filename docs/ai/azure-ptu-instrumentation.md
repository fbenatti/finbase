# Instrumentação Azure OpenAI PTU (subprojetos)

Os deployments PTU são compartilhados. O Azure Monitor é a fonte autoritativa de tokens, requests e utilização. Eventos de aplicação servem **somente** para atribuir consumo a subprojetos.

## Regras

- Não envie prompt, resposta, usuário, telefone, IP ou qualquer PII.
- Publique de forma assíncrona; falha de telemetria **nunca** bloqueia a chamada de IA.
- `telemetry_client_id` precisa estar cadastrado em OpenFinops (fonte Azure PTU → subprojetos). Copie apenas o identificador público.
- Eventos desconhecidos vão para quarentena; tokens sem evento permanecem `unattributed` (nunca são rateados automaticamente).
- Deduplicação por `event_id`. Atrasos de até 7 dias são aceitos.

## Contrato

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "occurred_at": "2026-08-24T15:32:10Z",
  "telemetry_client_id": "vivo-mais-app-x",
  "project_key": "vivo-mais",
  "subproject_key": "atendimento-digital",
  "environment": "production",
  "azure_account_resource_id": "/subscriptions/.../accounts/...",
  "deployment_name": "gpt-5.4-mini-ptu",
  "model_name": "gpt-5.4-mini",
  "model_version": "2026-03-17",
  "api_operation": "responses",
  "stream": true,
  "status_code": 200,
  "is_spillover": false,
  "request_count": 1,
  "input_tokens": 1200,
  "cached_input_tokens": 300,
  "output_tokens": 250,
  "reasoning_tokens": 0,
  "total_tokens": 1450,
  "latency_ms": 1850
}
```

Envio: Event Hub da fonte Azure PTU, ou `POST /azure-ptu/usage-events` em laboratório.

Exemplo Python: `docs/ai/azure-ptu-instrumentation-example.py`.
