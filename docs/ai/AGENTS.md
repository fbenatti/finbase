# OpenFinops — Guia de Contexto para Agentes de IA

Este diretório (`docs/ai/`) contém a documentação completa do sistema OpenFinops,
escrita para ser consumida como contexto por agentes de IA (LLMs) que precisem
entender, modificar ou depurar o projeto.

## Leitura recomendada (ordem)

1. **[00-overview.md](00-overview.md)** — o que é o sistema, stack, como rodar, princípios fundamentais.
2. **[01-architecture.md](01-architecture.md)** — componentes, processos (API/worker/scheduler), fluxo de dados end-to-end (medallion Bronze→Silver→Gold).
3. **[02-data-model.md](02-data-model.md)** — todas as tabelas PostgreSQL (metadata) e namespaces Iceberg (dados financeiros).
4. **[03-business-rules.md](03-business-rules.md)** — regras de negócio: mappings, business rules, classificação, clone engine, forecast, períodos. **O documento mais importante para entender o domínio.**
5. **[04-api-reference.md](04-api-reference.md)** — todos os endpoints REST organizados por router.
6. **[05-frontend.md](05-frontend.md)** — páginas React, componentes e integração com a API.
7. **[06-glossary.md](06-glossary.md)** — glossário de termos do domínio FinOps usados no código.
8. **[08-ui-design-reference.md](08-ui-design-reference.md)** — padrão visual e interacional
   de referência, validado em Connections.

## Resumo executivo (TL;DR para a IA)

- **OpenFinops** é uma plataforma FinOps multi-cloud (Azure, GCP, MongoDB Atlas) que ingere
  billing data, normaliza, aplica regras de negócio e classificação organizacional, e grava
  em um lakehouse Iceberg/Parquet consultável via Trino.
- **Regra de ouro da arquitetura:** PostgreSQL guarda APENAS metadata e controle de jobs
  (conexões, regras, mapeamentos, tarefas, configs de forecast). Linhas financeiras vivem
  exclusivamente em Iceberg/Parquet (MinIO S3 ou filesystem local em fallback).
- **Pipeline:** Scan de blobs → Extração (Bronze) → Mappings (Silver) → Business Rules +
  Classificação + Clone (Gold) → Análise/Pivot (Trino) → Forecast (namespace `forecast`).
- **Processos:** API FastAPI (`apps/api/app/main.py`, porta 8000/8001), Worker separado
  (`apps/api/worker.py`, faz polling da fila `etl_task` a cada 2s), Frontend Vite (porta 5173,
  proxy `/api`), Scheduler APScheduler dentro da API (cron em `scheduled_task`).
- **Tudo é metadata-driven:** nenhum nome de coluna de provedor é universal; regras,
  classificações, clones e forecasts são configurados pelo usuário e versionados no PostgreSQL.
- **"First match wins":** regras de negócio e classificação nunca sobrescrevem valores já
  preenchidos por regras de prioridade maior (menor valor de `priority`).
- Documento de design do Forecast: `FORECAST.md` na raiz (fonte de verdade do motor de forecast).

## Convenções do código que uma IA deve respeitar

- Nunca executar SQL/fórmula livre vinda do usuário — apenas os vocabulários declarativos
  existentes (expression engine com `pandas.eval`, clone engine com operações fixas).
- Colunas físicas da Gold podem ter casing diferente das regras: resolver por comparação
  case-insensitive (`by_lower`) sem alterar o schema persistido.
- `tipo = 'realizado'` vs `tipo = 'forecast'`: dados realizados e projetados nunca se
  sobrepõem; forecasts vivem em tabelas Iceberg separadas no namespace `forecast`.
- Datas ambíguas (`MM/DD/YYYY` vs `DD/MM/YYYY`) são desambiguadas usando o billing period;
  parsers sempre com `TRY` para não derrubar o pipeline.
- Ao modificar `forecast_run`, lembrar que runs são snapshots imutáveis de auditoria
  (`config_snapshot`) — nunca cascade-delete a partir de `forecast_config`.
