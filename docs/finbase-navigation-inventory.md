# Finbase.cloud — inventário de navegação e renovação visual

Atualizado em 21/10/2026, branch `novo_layout`.

## Direção visual

- Marca de produto: **finbase.cloud**. O shell já mostra o nome, usa o novo SVG em `apps/web/public/brand/finbase-logo.svg` e traz rodapé com copyright e descrição do produto.
- Referência: dashboards de FinOps com hierarquia visual enxuta, superfícies claras, navegação lateral escura/compacta opcional, realces em azul-petróleo e verde, cards de métricas e gráficos de leitura rápida, inspirados na linguagem de Vantage Cost sem copiar a marca.
- Tipografia de interface: Inter, configurada no tema e nos estilos globais; o wrapper dos gráficos também propaga Inter ao Plotly.
- Ícones: Material UI Outlined, com ícones próprios por área e tamanho uniforme no menu.
- Navegação: grupos laterais recolhíveis e links internos com URL própria. A seleção atual considera os parâmetros de query relevantes, então a página abre diretamente na visão escolhida. O shell inclui menu móvel, modo recolhido, notificações, usuário, saída e rodapé.
- O menu inicia recolhido e expande automaticamente o grupo da rota atual. Panorama contém somente o resumo; análises detalhadas de custo foram removidas da navegação a pedido do usuário.
- Revisão técnica: não restam elementos MUI `<Tabs>` ou `<Tab>` em `apps/web/src`. Controles que filtram ou alteram o modo de um widget sem representar uma página (por exemplo, formato JSON/script ou seleção de contrato) continuam como seletor/controle dentro do conteúdo.

## Mapa completo do menu

Os grupos abaixo são as seções da navegação. Cada item listado é uma entrada de submenu com rota e estado próprios. **Unit Economics** fica como seção vazia, conforme solicitado.

### Panorama

- **Resumo de custos**: `/custos?tab=visao-executiva`. Custos gerais, evolução, custos por organização/serviço, investigação, decisões, detalhamento e qualidade dos dados foram removidos do menu; suas visões permanecem na página.

### Operação de IA

- **Operação de IA / Azure PTUs**: resumo de capacidade, consumo de tokens, capacidade e operação e recomendações (`/ia/capacidade?view=0&tab=0..3`).
- **Provedores de IA**: GitHub Copilot (`view=1`); Claude executivo e técnico (`view=2&providerTab=executive|technical`); NotebookLM executivo e técnico (`view=3&providerTab=executive|technical`); Aura Brain (`view=4`); Vivo Mais (`view=5`); Sinfonia (`view=6`); Datamesh (`view=7`); Wisper (`view=8`). Todos usam `/ia/capacidade`.
- **Adoção de IA**: visão executiva, eficiência FinOps e uso por pessoa (`/ia/adocao?tab=0..2`).

### Alertas

- Anomalias: visão geral, impacto financeiro, casos em investigação, eventos esperados, saúde do modelo, retrospectivas, anomalias confirmadas e configuração (`/alertas?tab=0..7`). A visibilidade segue a configuração de ambiente já existente.

### Planejamento

- **Orçamento e contratos**: histórico do orçamento, história dos contratos e consumo de compromissos (`/planejamento?tab=dashboard&executive=orcamento|historia-contrato|compromisso`).
- **Gestão financeira**: orçamentos, acurácia do orçamento e projeções manuais (`/planejamento?tab=orcamento|acuracia-orcamento|forecast-manual`). Carta de crédito e Defesa orçamentária foram retiradas do menu.
- **Contabilidade e moeda**: Evolução do dólar (`tab=visao-contabil&subview=evolucao-dolar`). Pedidos SAP foi retirado do menu.
- **Acurácia das projeções**: visão, desvios e justificativas (`/projecoes/acuracia?aba=visao|desvios|acks`).
- **Projeções**: configuração e execução, regras de projeção e histórico (`/projecoes?tab=config|rules|history`).

### Otimização

- **Otimização**: oportunidades (`/otimizacao`) e compromissos e compras (`/otimizacao?tab=compras-compromissos`).
- **Desligamento programado**: painel, governança e exceções (`tab=desligue&subview=dashboard|governance|exceptions`).
- **Recursos órfãos**: recursos atuais e economia gerada (`tab=orfaos&subview=current|savings`).
- As entradas de **Reservas Azure** foram retiradas do menu; as rotas continuam registradas e podem ser abertas diretamente.

### Dados e operação

- **Integrações**: fontes de dados e serviços auxiliares (`/dados/fontes?view=connections|integrations`). Os filtros de categoria/provedor são controles da página, não destinos de navegação.
- **Catálogo**: catálogo de dados e relacionamentos (`/dados/catalogo?tab=destinations|relationship`).
- **Operação de dados**: fluxos de dados (`/dados/fluxos`); execuções de dados (`/operacoes/execucoes?tab=data`); execuções de forecast e acurácia (`tab=forecast&subtab=forecast|accuracy`); custo compartilhado (`tab=shared`); materialização de cubos e relacionamentos de custo IA (`tab=gold&subtab=cubes|ai-cost`); extrações (`tab=extraction`); agendamentos (`tab=schedules`); histórico de execuções (`tab=logs`).
- **Explorador**: tabelas de destino, fontes de origem, Iceberg Bronze/Silver/Gold, staging, tabelas dinâmicas e busca na fonte (`/dados/explorador?tab=destino|fontes|iceberg&layer=bronze|silver|gold|staging|pivot|busca`).

### Governança

- Organização (`/governanca?tab=organization`), classificação de projetos (`tab=classification`), regras de ajustes (`tab=rules&subtab=adjustments`), reservas e Savings Plans (`tab=rules&subtab=clone-rules`), contratos e compromissos (`tab=financial-contracts`) e cubos analíticos (`tab=cost-relationship`).
- As entradas de **Tags Automatizadas** foram retiradas do menu; suas rotas continuam registradas e podem ser abertas diretamente. Modos de formato/edição dentro da política permanecem controles locais.

### Administração

- **Ideias/Iniciativas** foram retiradas do menu. A rota `/iniciativas` continua registrada e protegida pela permissão de ideias.
- **Acessos**: pessoas e acessos, grupos e funções/permissões (`/administracao/acessos?tab=users|groups|roles`). Cada entrada respeita as permissões correspondentes.
- **Auditoria e autenticação**: trilha de auditoria (`/administracao/auditoria`) e login corporativo (`/administracao/identidade`).

## Rotas contextuais e compatibilidade

Além do menu, a aplicação preserva rotas de detalhe e histórico que são alcançadas a partir de uma linha, execução ou ação contextual: exploração de conexão (`/dados/fontes/:connectionId/explorar`), detalhe de execução de projeção (`/projecoes/execucoes/:runId`), custo compartilhado (`/operacoes/custos-compartilhados/:runId`) e extração (`/operacoes/extracoes/:taskId/historico`).

O componente legado `apps/web/src/pages/AzurePTU/AzurePTU.tsx` não é registrado em `App.tsx`; suas visões foram mantidas acessíveis como seletor interno no código legado, enquanto a rota ativa é `/ia/capacidade`. A navegação principal e o mapa acima descrevem as rotas realmente montadas.

## Sequência de nomes proposta

O primeiro corte preserva nomes funcionais próximos ao vocabulário atual para não quebrar treinamento e referências, mas melhora concisão e agrupamento. Para uma segunda rodada, recomendo padronizar os grupos para **Visão geral**, **Planejar**, **Otimizar**, **Operar**, **Governar** e **Configurações**; trocar “Custos por organização” por “Custos por unidade”; “Investigar variações” por “Análise de variações”; “Pessoas e acessos” por “Usuários e acessos”; e “Serviços auxiliares” por “Integrações”. As labels acima ficam como inventário do layout implementado nesta rodada.

## Pontos para validação funcional

- Confirmar em ambiente autenticado o acesso por perfil às rotas de grupos, funções, auditoria, SSO, ideias e anomalias.
- Validar que as páginas leem os parâmetros indicados (especialmente nomes com `subview`, `subtab`, `executive`, `providerTab`, `layer` e `aba`) e mantêm o item correto destacado após navegação direta/refresh.
- O domínio está aplicado à identidade mostrada no produto; DNS, certificado e publicação de `finbase.cloud` são configuração de infraestrutura separada.
