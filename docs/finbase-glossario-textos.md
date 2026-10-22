# Glossário de textos do FinBase (padrão FinOps)

Base: vocabulário do FinOps Framework (finops.org), com fases Informar, Otimizar e Operar, domínios e capabilities. Nomes de dados seguem o FOCUS. Este glossário vale para os textos visíveis do frontend e do menu. Chaves, rotas, parâmetros de URL, valores guardados no banco e nomes de API não mudam.

## Fases

| Termo adotado | Origem FinOps | Uso |
|---|---|---|
| Informar | Inform | Visibilidade, alocação, relatórios e análises, previsão, meta |
| Otimizar | Optimize | Oportunidades de economia, compromissos de tarifa, recursos ociosos |
| Operar | Operate | Políticas e governança, dados, coletas e execuções, administração |

## Capabilities e domínios

| Termo adotado | Capability FinOps |
|---|---|
| Alocação / custo rateado | Allocation (antigo "rateio") |
| Relatórios e análises | Reporting & Analytics |
| Anomalias / gasto anômalo | Anomaly Management |
| Previsão | Forecasting |
| Meta / aderência à meta | Budgeting |
| Compromissos de tarifa (RIs e Savings Plans) | Rate Optimization |
| Economia (saving) / oportunidades | Workload Optimization |
| Políticas e governança | Policy & Governance |
| Ferramentas e conexões de nuvem (origens de dados) | FinOps Tools |

## Termos adotados (novos ou trocados)

| Evitar | Usar | Observação |
|---|---|---|
| acurácia | aderência (meta) ou precisão (previsão) | |
| organograma | estrutura organizacional | nas telas de estrutura; a árvore de navegação segue "hierarquia" |
| ID Orçamento | código de orçamento | |
| rateio | alocação / custo rateado | |
| forecast | previsão | "Previsão manual", "Previsão base" |
| departamento (texto visível) | unidade | |
| perfil (de acesso) | papel | |
| pessoas (de acesso) | usuários | |
| orçamento (valor) | meta | |
| Realizado | efetivado | valores e chaves de dado continuam como estão |
| extração / extrações | coleta / coletas | "Programação" para agendamentos |
| conexão (em listas de fonte) | fonte / origem de dados | "Conexão" fica no nome técnico do cadastro |
| Uso sob demanda (OnDemand) | sob demanda | |
| Regras de negócio | ajustes de custo | |
| Serviço auxiliar | serviço de apoio | |
| Validar conexão / validação | testar conexão / teste | |
| Ação corretiva | ação de correção | |

## Nomes de dados (FOCUS)

Campos mantêm o nome do FOCUS quando aparecem como dado: BilledCost → Custo faturado; EffectiveCost → Custo efetivo; ListCost → Custo de lista; ContractedCost → Custo contratado; ServiceCategory, ServiceName, ServiceSubcategory e ChargePeriod ficam como nomes de campo.

## Verbos de ação

- Criar, Incluir e Remover substituem Cadastrar, Adicionar e Excluir, quando o verbo pede objeto específico (ex.: Criar iniciativa, Criar regra de clonagem, Remover conexão).
- Atualizar virou Recarregar (ex.: Recarregar dados, Recarregar lista).
- Salvar, Salvando…, Cancelar e Fechar continuam como estão nos diálogos, por padrão de interface.
- Gravar aparece em mensagens de confirmação e erro ("Não foi possível gravar a regra").

## Textos que permanecem iguais por decisão

- Nomes de produto e fornecedor: Azure, GCP, OCI, GitHub, NotebookLM, Gemini, Claude, Databricks, MongoDB Atlas, Savings Plan, Excel, PDF, CSV, JSON.
- Valores guardados no banco e comparados em código: categorias de justificativa de desvio (Pico sazonal, Projeto novo, Erro de precificação), o departamento "Consumo Cloud", o sentinela "Não informado" nas análises de anomalia e os códigos de status.
- Termos técnicos de cloud: resource group, subscription, ResourceId, tenant, client ID, PTU, WAPE, MAD.

## Pendências

- "Desvio" e "Variação" convivem: nos números usamos Variação e nas abas de orçamento ainda há Desvio. Definir um só.
- O item de menu de extração ainda pode aparecer como "Extrações" enquanto a página mostra "Coletas". Alinhar no catálogo de menu.
- Valores de categoria guardados (justificativas) não foram renomeados porque são gravados no banco.
