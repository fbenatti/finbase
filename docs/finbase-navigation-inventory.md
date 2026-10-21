# Finbase.cloud — inventário de navegação

Atualizado em 21/10/2026, branch `layot_carbon`.

O catálogo do backend é a fonte única dos menus e rotas. A disponibilidade por cliente pode ser alterada em **Administração → Módulos**.

Implementação e inclusão de novos menus: [menu-catalog.md](menu-catalog.md). Vocabulário dos dados: [finbase-data-language.md](finbase-data-language.md).

Os layouts Finbase e Carbon compartilham o catálogo. IA e Unit Economics permanecem fora dos menus. As rotas de favoritos anteriores redirecionam para os endereços abaixo.

## Panorama

Visão geral: `/panorama`.

- **Resumo de custos**: `/custos/resumo`.

## Alertas

Visão geral: `/alertas/inicio`.

- **Visão geral de alertas**: `/alertas/visao-geral`.
- **Impacto financeiro**: `/alertas/impacto-financeiro`.
- **Casos em investigação**: `/alertas/investigacoes`.
- **Eventos esperados**: `/alertas/eventos-esperados`.
- **Saúde do modelo**: `/alertas/saude-do-modelo`.
- **Retrospectivas**: `/alertas/retrospectivas`.
- **Anomalias confirmadas**: `/alertas/confirmados`.
- **Configuração de alertas**: `/alertas/configuracao`.

## Planejamento

Visão geral: `/planejamento/inicio`.

- **Histórico do orçamento**: `/planejamento/historico-do-orcamento`.
- **História dos contratos**: `/planejamento/historia-dos-contratos`.
- **Consumo de compromissos**: `/planejamento/consumo-de-compromissos`.
- **Orçamentos**: `/planejamento/orcamentos`.
- **Acurácia do orçamento**: `/planejamento/acuracia-do-orcamento`.
- **Projeções manuais**: `/planejamento/projecoes-manuais`.
- **Evolução do dólar**: `/planejamento/evolucao-do-dolar`.
- **Acurácia · visão**: `/projecoes/acuracia/visao`.
- **Acurácia · desvios**: `/projecoes/acuracia/desvios`.
- **Acurácia · justificativas**: `/projecoes/acuracia/justificativas`.
- **Configuração e execução**: `/projecoes/configuracao`.
- **Regras de projeção**: `/projecoes/regras`.
- **Histórico de projeções**: `/projecoes/historico`.

## Otimização

Visão geral: `/otimizacao/inicio`.

- **Oportunidades**: `/otimizacao/oportunidades`.
- **Compromissos e compras**: `/otimizacao/compromissos-e-compras`.
- **Desligamento · painel**: `/otimizacao/desligamento/painel`.
- **Desligamento · governança**: `/otimizacao/desligamento/governanca`.
- **Desligamento · exceções**: `/otimizacao/desligamento/excecoes`.
- **Recursos · órfãos atuais**: `/otimizacao/recursos-orfaos`.
- **Recursos · economia gerada**: `/otimizacao/economia-gerada`.

## Dados e operação

Visão geral: `/dados/inicio`.

- **Fontes de dados**: `/dados/fontes`.
- **Catálogo de dados**: `/dados/catalogo/tabelas`.
- **Relacionamentos**: `/dados/catalogo/relacionamentos`.
- **Fluxos de dados**: `/dados/fluxos`.
- **Execuções de dados**: `/operacoes/dados`.
- **Execuções de forecast**: `/operacoes/projecoes`.
- **Acurácia do forecast**: `/operacoes/acuracia`.
- **Custo compartilhado**: `/operacoes/rateios`.
- **Materialização de cubos**: `/operacoes/cubos`.
- **Extrações**: `/operacoes/extracoes`.
- **Agendamentos**: `/operacoes/agendamentos`.
- **Histórico de execuções**: `/operacoes/historico`.
- **Explorador · tabelas de destino**: `/dados/explorador/destinos`.
- **Explorador · fontes de origem**: `/dados/explorador/fontes`.
- **Base de origem**: `/dados/explorador/origem`.
- **Base preparada**: `/dados/explorador/preparados`.
- **Base consolidada**: `/dados/explorador/consolidados`.
- **Área temporária**: `/dados/explorador/temporarios`.
- **Explorador · tabelas dinâmicas**: `/dados/explorador/tabelas-dinamicas`.
- **Explorador · busca na fonte**: `/dados/explorador/busca`.

## Governança

Visão geral: `/governanca/inicio`.

- **Organização**: `/governanca/organizacao`.
- **Classificação de projetos**: `/governanca/classificacao-de-projetos`.
- **Regras de ajustes**: `/governanca/regras-de-ajuste`.
- **Reservas e Savings Plans**: `/governanca/compromissos`.
- **Contratos e compromissos**: `/governanca/contratos`.
- **Cubos analíticos**: `/governanca/cubos`.

## Administração

Visão geral: `/administracao/inicio`.

- **Pessoas e acessos**: `/administracao/pessoas`.
- **Grupos**: `/administracao/grupos`.
- **Funções e permissões**: `/administracao/funcoes`.
- **Trilha de auditoria**: `/administracao/auditoria`.
- **Login corporativo**: `/administracao/identidade`.
- **Módulos**: `/administracao/modulos`.

## Rotas preservadas fora da navegação

- **Reservas Azure**: `/governanca/reservas-azure`.
- **Tags Automatizadas**: `/governanca/politicas-de-tags`.
- **Carta de crédito**: `/planejamento/carta-de-credito`.
- **Defesa orçamentária**: `/planejamento/defesa-orcamentaria`.
- **Pedidos SAP**: `/planejamento/pedidos-sap`.
