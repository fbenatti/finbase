# Identidade visual e shell do frontend (Fases F0 e F1)

Branch `redesign/shell-theme`, a partir de `layot_carbon`. Plano completo: `plans/frontend-identidade-visual-plano.md` na pasta compartilhada do projeto.

## O que mudou no shell

- **Trilho de domínios** à esquerda, fundo azul escuro. Cada domínio é um botão com ícone e nome. O domínio ativo é destacado.
- **Painel de menus** ao lado do trilho. Lista só os menus do domínio escolhido, com rota. Ao escolher um domínio, abre o primeiro menu dele.
- **Busca de menus** no painel, com atalho Ctrl/Cmd + K. Com busca ativa, mostra os resultados de todos os domínios, com o nome do domínio como legenda.
- **Cabeçalho** com o domínio (legenda), o nome do menu atual, sino de notificações, usuário e sair.
- **Sem item "Visão geral"** gerado automaticamente em cada grupo. Um domínio sem menus com rota não aparece.
- **Mobile**: o trilho e o painel abrem juntos em uma gaveta, pelo botão de menu do cabeçalho ou Ctrl/Cmd + K.
- **Layout Carbon removido**: o alternador Finbase/Carbon saiu do cabeçalho, e o `localStorage` com a chave `finbase.layout` não é mais lido.

Regra de permissão mantida: toda aba vira menu e todo menu tem rota. O catálogo do backend continua sendo a fonte única dos menus.

## Tokens de identidade

Arquivo único: `apps/web/src/theme/tokens.ts`.

| Grupo | Uso |
| --- | --- |
| `colors.brand*` | Ação principal, seleção, foco (azul do logo `#0F62FE`) |
| `colors.accent` | Destaque laranja (`#EA580C`) |
| `colors.canvas`, `paper`, `raised`, `tableHead`, `rowHover` | Superfícies e linhas de tabela |
| `colors.border`, `borderStrong` | Bordas e divisórias |
| `colors.text*` | Texto principal, secundário e de apoio |
| `colors.rail*` | Trilho de domínios |
| `colors.success`, `warning`, `error`, `info` | Estados (mantidos semânticos) |
| `chartSeries` | Paleta de categorias dos gráficos |
| `chartSemantic` | Realizado, orçamento, forecast, negativo e positivo |
| `fontFamily` | Inter (texto), Manrope (títulos), JetBrains Mono |
| `radius` | Controles 8 px, superfícies 12 px, pílulas |

O tema MUI (`theme/theme.ts`) lê esses valores. O mapa `legacyColors` e a função de cores por hash de `chartTheme.ts` foram removidos. `chartColor` agora só converte nomes de cinza e as cores semânticas `red` e `green`.

`index.html` carrega Inter e Manrope, e a cor do navegador é `#0F62FE`.

## Alterações por módulo

| Módulo | Alteração |
| --- | --- |
| Shell (`layout/Layout.tsx`) | Reescrito: trilho de domínios, painel de menus, cabeçalho novo. Remove o ramo Carbon, o alternador de layout e o item "Visão geral". |
| Busca de menus (`navigation/WorkspaceSearch.tsx`) | Ícones MUI. Sempre expandida no novo shell. |
| Navegação (`App.tsx`) | Remove as rotas `ia/capacidade`, `ia/adocao`, `ia/github` e seus redirecionamentos antigos (`visoes-consumo-ia`, `dashboards-ia`, `github-copilot`, `azure-ptu-ops`). Caem na rota padrão. As rotas fora do menu listadas em `finbase-navigation-inventory.md` foram mantidas. |
| Permissões (`auth/moduleMap.ts`) | Remove as três entradas de `/ia/*`. |
| Inteligência artificial (`pages/AzurePTU`, `pages/Dashboards/DashboardsIA.tsx`, `pages/GitHubCopilotDashboard`) | Removido. O helper de PDF foi para `utils/pdfExport.ts`, usado pelo Planejamento. |
| Unit Economics (`pages/AzurePTU/WisperDashboard.tsx`) | Removido, junto com `getWisperDashboard` e o tipo `WisperDashboardPayload`. |
| Serviços (`services/api.ts`) | Remove os clientes de Azure PTU, Projeção de custo de projeto, Sinfonia, Datamesh, Wisper, IA (provedor e Copilot). Nenhum outro arquivo os usava. Os endpoints do backend permanecem. |
| Tema (`theme/theme.ts`, `theme/tokens.ts`) | Tema único, com tokens. A função `createAppTheme` não recebe mais o layout. |
| Gráficos (`theme/chartTheme.ts`, `chartTheme.test.ts`) | Usa `chartSeries` e `chartSemantic`. Testes atualizados. |
| Hook `theme/useInterfaceTheme.ts` | Removido. `FinbaseDialog`, `Plot` e `HierarchicalDecompTree` usam `useTheme` do MUI. |
| Ícones (`components/FinbaseIcons.tsx`, `components/DataGridIcons.tsx`) | Trocados de Carbon para `@mui/icons-material`. Mesmos nomes exportados. |
| Planejamento (`pages/Planning/Planning.tsx`, `DollarEvolution.tsx`) | Ícones MUI. Classes de impressão renomeadas para `finops-*`. |
| Árvore hierárquica (`pages/GoldDashboard/HierarchicalDecompTree.tsx`) | Cores semânticas por token. |
| Estilos globais (`index.css`, `main.tsx`, `index.html`) | Remove o CSS do Carbon e a importação de `@carbon/styles`. Corpo com tokens. Classes de impressão renomeadas. |
| Dependências (`package.json`) | Remove `@carbon/react` (e `@carbon/styles`, que vinha junto). |
| Utilitários (`utils/pdfExport.ts`) | Novo local do helper de impressão (antes em `pages/AzurePTU`). |

## Cores fixas ainda no código

Nesta etapa a paleta passou a ser azul do logo: primária `#0F62FE`, neutros levemente esverdeados, trilho `#162B3A` (azul-marinho do logotipo) e laranja como destaque pontual. As cores azuis, índigo e roxas literais em componentes e gráficos foram trocadas pelos tons da nova paleta. Ainda existem cores literais de dados e de status (vermelho, verde, âmbar) em componentes. Elas serão migradas para tokens por componente na Fase 4, com revisão visual.

## Próximas fases

- **F2**: estrutura de abas e cabeçalhos de página.
- **F3**: textos, nomes de menus e glossário.
- **F4**: datagrids, widgets e interações de gráficos, incluindo as cores restantes.
- **F5**: revisão visual em telas principais e no celular, e atualização final desta documentação.

## Pendências conhecidas

- Testes de build e de unidade passam, exceto dois testes de `PolicyFlowGraph.test.ts` que já falhavam em `layot_carbon`.
- Erros de tipo do Plotly em `BudgetAccuracy`, `BudgetAccuracyWidget(V2)` e `CommitmentsDashboard` também já existiam em `layot_carbon`.
- A revisão visual no navegador (com API ativa) não foi feita nesta fase.

## Ícones e uso de cor

**Biblioteca única:** `@mui/icons-material`, sempre na variante *Outlined* (ex.: `SaveOutlined`, `FilterAltOutlined`). Não use a variante preenchida nem outra biblioteca de ícones. Os imports são `import { SaveOutlined } from "@mui/icons-material"` ou com alias de uso, como `AddOutlined as Add`.

**Tamanho:** `fontSize="small"` (20px) em botões, campos, menus e tabelas. `fontSize="large"` só em estados vazios e cabeçalhos de cartão.

**Cor dos ícones:** `colors.textSecondary` por padrão, `colors.brand` quando o item está ativo ou é a ação principal, e as cores de status (`success`, `warning`, `error`) somente para indicar estado.

**Trilho de áreas:** fundo branco com borda `colors.border`. O item ativo usa fundo `colors.railActive`, texto `colors.brandDark` e um indicador azul à esquerda. Não use fundo escuro nem laranja no trilho.

**Cor de destaque:** o laranja `colors.accent` é reservado para um ponto de atenção por tela (ex.: indicador de seleção, alerta de prioridade). Não use em botões primários.
