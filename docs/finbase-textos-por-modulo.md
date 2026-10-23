# Textos do FinBase por módulo (pt-BR, padrão FinOps)

Resumo do que foi reescrito em cada módulo do frontend, com o estado de cada um. O vocabulário e as decisões estão em `docs/finbase-glossario-textos.md`. Só texto visível muda: rotas, chaves, parâmetros, valores guardados e nomes de API ficam como estão.

## Como conferir

- Trabalho feito na cópia do Mac (`OpenFinops`, branch `layot_carbon`). Nada foi enviado do container.
- O catálogo de menu (`apps/api/app/core/menu_catalog.json`) foi reescrito. Depois de alterar, reinicie a API ou rode a sincronização dos rótulos para o banco.
- Não houve build nem typecheck neste ambiente. A conferência foi feita por diff e busca de textos.

## Módulos

| Módulo | O que mudou | Estado |
|---|---|---|
| Menu (catálogo) | Rótulos de seções e páginas (ex.: Anomalias, Orçamento e previsão, Compromissos de tarifa, Fontes de custo) | Feito |
| Governança / Políticas e governança | Título, abas, descrições | Feito |
| Estrutura organizacional (Departments, DepartmentTreePicker, orgLabels) | Não mexido: a outra thread já reescreveu | Fora do escopo |
| Classificação e tags (Classification, PolicyFlow, AzureTagExecutiveDashboard) | Abas, políticas, exclusões, painel executivo | Feito |
| Ajustes de custo (Rules) | Formulário, operações, mensagens | Feito |
| Clonagem (CloneRules) | Regras de clonagem, colunas, vigência | Feito |
| Compromissos / Savings (Savings, CommitmentsDashboard) | Abas, colunas, botões (Criar iniciativa, Recarregar dados) | Feito |
| Reservas (Reservations) | Reservas ativas, vencimento, modalidade | Feito |
| Contratos financeiros (FinancialContracts, Governance) | Acerto mensal, excedente, códigos de orçamento | Feito |
| Recursos órfãos (OrphanedResources) | Status, categorias, tempo de existência | Feito |
| Modelos analíticos (GoldMarts) | Modelos, consolidação, reprocessar base | Feito |
| Tabelas, Explorer, Destinos, Mapeamentos | Relações entre tabelas, campos, fonte de dados | Feito |
| Análise (Analysis) | Competência, tabela de destino, consulta executada | Feito |
| Esteira (Pipeline) | Carregar esteira, fontes, ajustes | Feito |
| Relações (Relationship) | Relações entre fontes e tabelas | Feito |
| Anomalias (Anomalies, Settings, Evolution, CaseScope, CasesDataGrid, Impact) | Casos, ações, critérios, gráficos | Feito na maior parte; faltam telas novas vindas de outra thread (AnomaliesPainel) e alguns cabeçalhos |
| Ideias (Ideas) | Status, tipo, impacto, painel | Feito |
| Extração / Coletas (Extraction, SchedulesPanel, ExtractionHistory) | Coletas, programações, presets | Feito |
| Reprocessamento (Reprocess, 10 arquivos) | Abas, logs, linhas, etapas | Feito |
| Previsão (Forecast, ForecastConfigDialog, RuleDialog, constants, fieldHelp) | Regras, métodos, histórico | Feito |
| Acurácia da previsão (ForecastAccuracy, Desvios, Acks) | Aderência, desvios, justificativas | Feito; categorias gravadas mantidas |
| Visão esperada e dashboards (ExpectedView, ExpectedViewV2, Acks/BudgetAccuracy widgets) | Títulos, legendas, filtros | Feito |
| Dashboard de custos (GoldDashboard e variantes, CostSummary, VisaoContabil, DesligueProgramado, Hierarchical) | Cabeçalhos, filtros, widgets | Feito; alguns valores de dado mantidos |
| Planejamento (BudgetAccuracy, BudgetDefensePanel, FinancialPlanning, DollarEvolution) | Textos de defesa, câmbio | Feito |
| Planejamento (Planning.tsx, MonthlyValuesDialog) | Não mexido: outra thread é dona | Pendente |
| Fontes de dados (Connections, AzureSourcesPanel, ConnectionsDataGrid, IntegrationsPanel, ManualCsvUploadDialog) | Nomes de fonte, testes, formulários | Feito |
| Serviços de apoio (IntegrationsPanel) | Serviços, tipos, mensagens | Feito |
| Admin e acesso (Admin, SsoProviders, ChangePassword, Login) | Papéis, SSO, senha | Feito |
| Notificações (NotificationContext) | Títulos e avisos | Feito |
| API (services/api.ts) | Mensagens de erro padrão | Feito |
| Relatórios PDF e exportações | Botões de exportação | Feito nos módulos acima |

## Itens que não foram renomeados e por quê

- Valores de categoria de justificativa (Pico sazonal, Projeto novo, Erro de precificação) e o valor "Erro de planejamento": são gravados no banco.
- "Consumo Cloud" e "Não informado": são comparados em código.
- Nomes de produto e fornecedor (Azure, GCP, OCI, GitHub, NotebookLM, Gemini, Claude, Databricks, MongoDB Atlas).
- Botões genéricos Salvar, Salvando…, Cancelar e Fechar: mantidos por padrão de interface.

## Pendências

- Planning.tsx e MonthlyValuesDialog: outra thread é dona. Ficam por último.
- AzurePTU e GitHubCopilotDashboard: não existem na árvore do Mac. Confirmar o nome certo da tela.
- FinancialContractsDashboard.tsx: foi reescrito no Mac por outra alteração. Ainda tem textos como "Planejamento · Uso de compromissos" e "Real acumulado". Não foi mexido por causa da concorrência.
- AnomaliesPainel.tsx (novo no Mac): ainda não entrou nesta passada.
- Alinhar "Extrações" (catálogo de menu) com "Coletas" (página).
- "Desvio" e "Variação" convivem nas abas de orçamento.
