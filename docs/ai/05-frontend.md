# 05 — Frontend

SPA React + TypeScript + Vite + Material UI (`apps/web/`). Dev server na porta 5173 com
proxy `/api` → API. Cliente HTTP centralizado em `src/services/api.ts` (axios,
timeout 120s) com interfaces TypeScript espelhando os schemas do backend.

## Rotas (`src/App.tsx`)

| Path | Página | Propósito |
|---|---|---|
| `/connections` | `Connections/` | CRUD de conexões de origem (Azure/GCP/Atlas) |
| `/explorer/:connectionId` | `Explorer/` | Explora blobs de uma conexão (scan, ingestão, skip) |
| `/departments` | `Departments/` | Árvore hierárquica de departamentos e projetos |
| `/destinations` | `Destinations/` | Tabelas de destino e suas colunas |
| `/relationship` | `Relationship/` | Relacionamento origem↔destino (visão de mappings por conexão) |
| `/mappings` | `Mappings/` | Mapeamentos coluna origem→destino |
| `/rules` | `Rules/` | Business rules (EXPRESSION/CONDITIONAL/PTAX/TAX/MARKUP) |
| `/clone-rules` | `CloneRules/` | Regras de clone On-Demand + pendências de preço |
| `/classifications` | `Classification/` | Regras de classificação multi-condição |
| `/extraction` | `Extraction/` | Disparo e acompanhamento de extrações (polling de status) |
| `/analysis` | `Analysis/` | Pivot tables e SQL sobre a Gold |
| `/pipeline` | `Pipeline/` (PipelineBuilder) | Visualização drag-and-drop do pipeline (React Flow) |
| `/reprocess` | `Reprocess/` | Reprocessamento de destinos (seleção de conexões/períodos, logs) |
| `/forecast` | `Forecast/` | Configuração, execução e acompanhamento de forecasts |
| `/forecast/history/:runId` | `Forecast/ForecastHistory.tsx` | Histórico/resultados de uma run específica (usa `HistoryGrid.tsx`) |

## Componentes compartilhados (`src/components/`)

| Componente | Uso |
|---|---|
| `PivotAnalysis.tsx` | Pivot interativo (react-pivottable) acoplado ao `/analysis/pivot` |
| `SortableTable.tsx` | Tabela genérica com ordenação |
| `SourceSearch.tsx` | Busca/seleção de origens |
| `EmptyState.tsx` | Estado vazio padronizado |

## Layout (`src/layout/Layout.tsx`)

- Navegação lateral para todas as páginas.
- **Banner global de tarefas**: mostra execuções ativas diferenciando Extração,
  Reprocessamento e Forecast (polling; a tela de forecast atualiza a cada 3s).

## Padrões de integração

- Todo acesso à API passa por `services/api.ts` (nunca axios direto nas páginas).
- Tipos TS exportados por domínio: `SourceConnection`, `BlobScanResult`, `IngestedBlob`,
  `EtlMapping`, `BusinessRule`, etc. — manter sincronizados com `schemas.py`.
- Páginas de execução longa (Extraction, Reprocess, Forecast) usam polling de endpoints
  de status (`/etl/status/{id}`, `/etl/reprocess-status/{id}`, `/forecast/runs/{id}`).
- O Forecast carrega metadata dinâmico da Gold selecionada (`/forecast/metadata/{id}`) e
  nunca assume nomes de colunas fixos de provedor; sugestões FOCUS aparecem apenas se as
  colunas existirem no schema.
