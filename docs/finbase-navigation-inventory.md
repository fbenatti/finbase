# Árvore de navegação do FinBase

Gerada a partir de apps/api/app/core/menu_catalog.json. Cada tela tem rota e permissão próprias: mudar o rótulo ou o grupo não muda a rota nem a permissão.

A busca do menu encontra qualquer tela pelo próprio nome, em qualquer nível, sem depender do grupo. A permissão continua valendo para o caminho inteiro.

## Panorama  (`/panorama`)

- **Resumo de custos**: `/custos/resumo`
- **Orçamento**: `/planejamento/historico-do-orcamento`
- **Compromissos**: `/planejamento/consumo-de-compromissos`
- **Câmbio do dólar**: `/planejamento/evolucao-do-dolar`
- **Visão de alertas**: `/alertas/painel` (antiga `/alertas/visao-geral`, redirecionada) _(em desenvolvimento)_
- **Painel de desligamento**: `/otimizacao/desligamento/painel`

## Alertas  (`/alertas/inicio`)

- **Impacto financeiro**: `/alertas/impacto-financeiro` _(em desenvolvimento)_
- **Casos em investigação**: `/alertas/investigacoes` _(em desenvolvimento)_
- **Eventos esperados**: `/alertas/eventos-esperados` _(em desenvolvimento)_
- **Saúde do modelo**: `/alertas/saude-do-modelo` _(em desenvolvimento)_
- **Retrospectivas**: `/alertas/retrospectivas` _(em desenvolvimento)_
- **Anomalias confirmadas**: `/alertas/confirmados` _(em desenvolvimento)_
- **Configuração de alertas**: `/alertas/configuracao` _(em desenvolvimento)_

## Planejamento  (`/planejamento/inicio`)

- **Orçamentos**: `/planejamento/orcamentos`
- **Aderência ao orçamento**: `/planejamento/aderencia-orcamento`
- **Projeções manuais**: `/planejamento/projecoes-manuais`
- **Executar previsões**: `/projecoes/configuracao`
- **Regras de previsão**: `/projecoes/regras`
- **Histórico de previsões**: `/projecoes/historico`
- **Precisão: visão geral**: `/projecoes/precisao/visao`
- **Desvios da previsão**: `/projecoes/precisao/desvios`
- **Justificativas dos desvios**: `/projecoes/precisao/justificativas`
- **Carta de crédito**: `/planejamento/carta-de-credito`
- **Defesa orçamentária**: `/planejamento/defesa-orcamentaria`
- **Pedidos SAP**: `/planejamento/pedidos-sap`

## Otimização  (`/otimizacao/inicio`)

- **Oportunidades**: `/otimizacao/oportunidades`
- **Compromissos e compras**: `/otimizacao/compromissos-e-compras`
- **Governança do desligamento**: `/otimizacao/desligamento/governanca`
- **Exceções do desligamento**: `/otimizacao/desligamento/excecoes`
- **Recursos órfãos**: `/otimizacao/recursos-orfaos`
- **Economia gerada**: `/otimizacao/economia-gerada`

## Dados e operação  (`/dados/inicio`)

- **Fontes de dados**: `/dados/fontes`
- **Catálogo de tabelas**: `/dados/catalogo/tabelas`
- **Relacionamentos**: `/dados/catalogo/relacionamentos`
- **Fluxos de dados**: `/dados/fluxos`
- **Cargas de dados**: `/operacoes/dados`
- **Execuções de previsão**: `/operacoes/projecoes`
- **Precisão das previsões**: `/operacoes/precisao`
- **Custo compartilhado**: `/operacoes/rateios`
- **Materialização de cubos**: `/operacoes/cubos`
- **Cubos analíticos**: `/governanca/cubos`
- **Extrações**: `/operacoes/extracoes`
- **Agendamentos**: `/operacoes/agendamentos`
- **Histórico de execuções**: `/operacoes/historico`
- **Explorador: tabelas de destino**: `/dados/explorador/destinos`
- **Explorador: fontes de origem**: `/dados/explorador/fontes`
- **Base de origem**: `/dados/explorador/origem`
- **Base preparada**: `/dados/explorador/preparados`
- **Base consolidada**: `/dados/explorador/consolidados`
- **Área temporária**: `/dados/explorador/temporarios`

## Consultas  (`/consultas/inicio`)

- **Tabelas dinâmicas**: `/dados/explorador/tabelas-dinamicas`
- **Busca na fonte**: `/dados/explorador/busca`

## Governança  (`/governanca/inicio`)

- **Hierarquia**: `/governanca/organizacao`
- **Classificação de projetos**: `/governanca/classificacao-de-projetos`
- **Regras de ajuste**: `/governanca/regras-de-ajuste`
- **Reservas e Savings Plans**: `/governanca/compromissos`
- **Contratos e compromissos**: `/governanca/contratos`
- **Reservas Azure**: `/governanca/reservas-azure`
- **Tags Automatizadas**: `/governanca/politicas-de-tags`

## Administração  (`/administracao/inicio`)

- **Pessoas e acessos**: `/administracao/pessoas`
- **Grupos**: `/administracao/grupos`
- **Funções e permissões**: `/administracao/funcoes`
- **Trilha de auditoria**: `/administracao/auditoria`
- **Login corporativo**: `/administracao/identidade`
- **Conectores**: `/administracao/conectores`
- **Módulos**: `/administracao/modulos`

## Telas fora do menu

Estas telas têm rota e permissão, mas não aparecem na navegação: Reservas Azure, Tags Automatizadas, Carta de crédito, Defesa orçamentária e Pedidos SAP. Ficam acessíveis por link direto e pelas abas das telas de origem.
