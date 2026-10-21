# Finbase.cloud — inventário de navegação e renovação visual

Atualizado em 21/10/2026, branch `novo_layout`.

## Direção visual

- Marca de produto: **finbase.cloud**. O shell já mostra o nome, usa o novo SVG em `apps/web/public/brand/finbase-logo.svg` e traz rodapé com copyright e descrição do produto.
- Referência: dashboards de FinOps com hierarquia visual enxuta, superfícies claras, navegação lateral escura/compacta opcional, realces em azul-petróleo e verde, cards de métricas e gráficos de leitura rápida, inspirados na linguagem de Vantage Cost sem copiar a marca.
- Tipografia de interface: Inter, configurada no tema e nos estilos globais; o wrapper dos gráficos também propaga Inter ao Plotly.
- Ícones: Material UI Outlined, com ícones próprios por área e tamanho uniforme no menu.
- Navegação: grupos laterais recolhíveis e links internos com URL própria. A seleção atual considera os parâmetros de query relevantes, então a página abre diretamente na visão escolhida. O shell inclui menu móvel, modo recolhido, notificações, usuário, saída e rodapé.
- Revisão técnica: não restam elementos MUI `<Tabs>` ou `<Tab>` em `apps/web/src`. Controles que filtram ou alteram o modo de um widget sem representar uma página (por exemplo, formato JSON/script ou seleção de contrato) continuam como seletor/controle dentro do conteúdo.

## Mapa completo do menu

Os grupos abaixo são as seções expansíveis da navegação. Cada item listado é uma entrada de submenu com rota e estado próprios.

### Panorama

- **Custos**: Resumo de custos (`/gold-dashboard?tab=visao-executiva`), Custos gerais (`tab=geral`), Evolução de custos (`tab=evolucao`), Custos por organização (`tab=organizacao`), Custos por serviço (`tab=servicos`), Investigar variações (`tab=investigacao`), Decisões (`tab=decisao`), Detalhamento (`tab=drilldown`) e Qualidade dos dados (`tab=data-quality`).
- **Operação de IA / Azure PTUs**: resumo de capacidade, consumo de tokens, capacidade e operação e recomendações (`/visoes-consumo-ia?view=0&tab=0..3`).
- **Provedores e projetos de IA**: GitHub Copilot (`view=1`); Claude executivo e técnico (`view=2&providerTab=executive|technical`); NotebookLM executivo e técnico (`view=3&providerTab=executive|technical`); Aura Brain (`view=4`); Vivo Mais (`view=5`); Sinfonia (`view=6`); Datamesh (`view=7`); Wisper (`view=8`). Todos usam `/visoes-consumo-ia`.
- **Painéis de IA**: visão executiva, eficiência FinOps e uso por pessoa (`/dashboards-ia?tab=0..2`).
- **Oportunidades**: entrada para `/savings`.
- **Alertas e anomalias**: visão geral, impacto financeiro, casos em investigação, eventos esperados, saúde do modelo, retrospectivas, anomalias confirmadas e configuração de alertas (`/anomalies?tab=0..7`). A visibilidade segue a configuração de ambiente já existente.

### Planejamento

- **Orçamento e contratos**: histórico do orçamento, história dos contratos e consumo de compromissos (`/financial-planning?tab=dashboard&executive=orcamento|historia-contrato|compromisso`).
- **Gestão financeira**: orçamentos, acurácia do orçamento, projeções manuais, créditos e defesa orçamentária (`/financial-planning?tab=orcamento|acuracia-orcamento|forecast-manual|carta-credito|defesa-orcamentaria`).
- **Contabilidade e moeda**: Pedidos SAP e Evolução do dólar (`tab=visao-contabil&subview=pedidos-sap|evolucao-dolar`).
- **Acurácia do forecast**: visão, desvios e justificativas (`/forecast-accuracy?aba=visao|desvios|acks`).
- **Forecast**: configuração e execução, regras de projeção e histórico de projeções (`/forecast?tab=config|rules|history`).

### Otimização

- **Savings**: ações de economia e compromissos e compras (`/savings?tab=calcular|compras-compromissos`).
- **Desligamento programado**: painel, governança e exceções (`tab=desligue&subview=dashboard|governance|exceptions`).
- **Recursos órfãos**: recursos atuais e economia gerada (`tab=orfaos&subview=current|savings`).
- **Reservas**: visão executiva, catálogo, recomendações e fontes de coleta (`/governance?tab=reservas&subtab=executivo|catalogo|recomendacoes|fontes`).

### Dados e operação

- **Integrações**: fontes de dados e serviços auxiliares (`/connections?view=connections|integrations`). Os antigos filtros de categoria/provedor são filtros segmentados na página, não destinos de navegação.
- **Catálogo**: catálogo de dados e relacionamentos (`/tables?tab=destinations|relationship`).
- **Operação de dados**: fluxos de dados (`/pipeline`); execuções de dados (`/reprocess?tab=data`); execuções de forecast e acurácia (`tab=forecast&subtab=forecast|accuracy`); custo compartilhado (`tab=shared`); materialização de cubos e relacionamentos de custo IA (`tab=gold&subtab=cubes|ai-cost`); extrações (`tab=extraction`); agendamentos (`tab=schedules`); histórico de execuções (`tab=logs`).
- **Explorador**: tabelas de destino, fontes de origem, Iceberg Bronze/Silver/Gold, staging, tabelas dinâmicas e busca na fonte (`/analysis?tab=destino|fontes|iceberg&layer=bronze|silver|gold|staging|pivot|busca`).

### Governança

- Organização (`/governance?tab=organization`), classificação de projetos (`tab=classification`), regras de ajustes (`tab=rules&subtab=adjustments`), reservas e Savings Plans (`tab=rules&subtab=clone-rules`), contratos e compromissos (`tab=financial-contracts`) e cubos analíticos (`tab=cost-relationship`).
- **Tags**: painel executivo, políticas Azure e mapeamentos (`tab=tags&subtab=executive|policies|mapping`). Modos de formato/edição dentro da política permanecem controles locais.

### Administração

- **Iniciativas**: portal de iniciativas, fila priorizada, canvas de entrega e resultados das iniciativas (`/ideas?tab=portal|backlog|canvas|dashboard`). Acesso condicionado à permissão de ideias.
- **Acessos**: pessoas e acessos, grupos e funções/permissões (`/admin?tab=users|groups|roles`). Cada entrada respeita as permissões correspondentes.
- **Auditoria e autenticação**: trilha de auditoria (`/admin/audit`) e login corporativo (`/admin/sso`).

## Rotas contextuais e compatibilidade

Além do menu, a aplicação preserva rotas de detalhe e histórico que são alcançadas a partir de uma linha, execução ou ação contextual: exploração de conexão (`/explorer/:connectionId`), detalhe de execução de forecast, custo compartilhado e extração (`/forecast/history/:runId`, `/reprocess/shared-cost/history/:runId`, `/extraction/history/:taskId`). Há também aliases antigos redirecionados para as novas rotas de planejamento, catálogo, governança, extração e operações PTU.

O componente legado `apps/web/src/pages/AzurePTU/AzurePTU.tsx` não é registrado em `App.tsx`; suas visões foram mantidas acessíveis como seletor interno no código legado, enquanto a rota ativa é `/visoes-consumo-ia`. A navegação principal e o mapa acima descrevem as rotas realmente montadas.

## Sequência de nomes proposta

O primeiro corte preserva nomes funcionais próximos ao vocabulário atual para não quebrar treinamento e referências, mas melhora concisão e agrupamento. Para uma segunda rodada, recomendo padronizar os grupos para **Visão geral**, **Planejar**, **Otimizar**, **Operar**, **Governar** e **Configurações**; trocar “Custos por organização” por “Custos por unidade”; “Investigar variações” por “Análise de variações”; “Pessoas e acessos” por “Usuários e acessos”; e “Serviços auxiliares” por “Integrações”. As labels acima ficam como inventário do layout implementado nesta rodada.

## Pontos para validação funcional

- Confirmar em ambiente autenticado o acesso por perfil às rotas de grupos, funções, auditoria, SSO, ideias e anomalias.
- Validar que as páginas leem os parâmetros indicados (especialmente nomes com `subview`, `subtab`, `executive`, `providerTab`, `layer` e `aba`) e mantêm o item correto destacado após navegação direta/refresh.
- O domínio está aplicado à identidade mostrada no produto; DNS, certificado e publicação de `finbase.cloud` são configuração de infraestrutura separada.
