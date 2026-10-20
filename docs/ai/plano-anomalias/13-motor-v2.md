# Motor de anomalias v2

- Baseline próprio (nível 14d + MAD 56d + fator semanal), sem depender do forecast para abrir caso.
- Compromissos por `resource_id` (livro de compromissos).
- Detecção disparada pelo pipeline de coleta (`anomaly_after_pipeline` no agendamento).
- Configuração `detection_engine=v2` (padrão). Motor v1 permanece para rollback.

Código: `apps/api/app/services/anomaly_v2/`.
