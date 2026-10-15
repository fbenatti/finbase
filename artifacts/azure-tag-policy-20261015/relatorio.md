# Azure tag policy — relatório de estado e planos

Data da leitura: 15/10/2026. Subscription escopo único: `63dbc6fc-93d8-4ac1-9745-253f6b502240`. Tenant de recursos: `78879b00-f0e9-486c-836f-56f314e47e7f`.

## Antes da correção

Leitura de ARM/Policy Insights feita com Azure CLI autenticada e subscription explícita. Para validar os scripts PLAN, o contexto local do CLI foi temporariamente trocado para o GUID alvo e restaurado ao valor original ao final; nenhuma subscription foi alterada no Azure. A conexão persistida `Azure TI Cost Management` (ID 2, `azure-ti`) falha autenticação por client secret inválido. O Trino local retornou 503 porque workers estavam reiniciando. Por isso, os planos foram montados a partir das regras e tags documentadas no plano do usuário, definições/assignments atuais e inventário Azure de leitura; não foram derivados de uma exportação atual do endpoint da conexão.

- 95 recursos ARM; 94 com `projeto=fundacao telefonica`, `diretoria=DIR FUNDACAO TELEFONICA`, `centro-de-custos=29TN161700` (comparação de chaves sem distinção de caixa). O único sem tags é `Microsoft.Sql/servers/databases/master`, não elegível para tags.
- 10 Resource Groups: 8 sem tags Fundação; os dois RGs Prisma também não têm os valores Prisma esperados. `PCCAgentlessScanResourceGroup` tem apenas metadados de criação; `dig-security-rg-o2rkefvpoqj4g-1162383220130775040-use1` tem tags `PrismaCloud` e valores de inventário distintos da classificação Prisma.
- Assignment `openfinops-rg-azure-ti-test-fundacao-telefonica-1-63dbc6fc`: principal `05fff4cf-224a-41ed-a933-414a82db9b5b`; mode `All`; definition usa o tipo RG incorreto `Microsoft.Resources/resourceGroups`, mas já contém as exceções Prisma.
- Assignment `openfinops-resource-azure-ti-test-fundacao-telefonica-2-63dbc6fc`: principal `5c32b23b-3c37-42d7-b116-0b0d25fdbda8`; mode `Indexed`; definition preserva exceções Prisma, mas não filtra drift de tags.
- Policy Insights para o assignment de recursos: 94 `NonCompliant`. Remediação antiga de recursos: `Succeeded`, 94/94 deployments; remediação antiga de RG: `Succeeded`, 0/0. Não foram criadas remediações nesta execução.

## Plano emitido

Os dois scripts PLAN e os dois scripts de aplicação são restritos ao GUID acima. O PLAN confirma o contexto e não executa mutações Azure. Fundação (#52) inclui exclusão por seletor dos dois padrões Prisma. Prisma (#78) só seleciona `PCCAGENTLESSSCANRESOURCEGROUP` e `dig-security-rg`. Todas as policies de recursos excluem explicitamente `Microsoft.Sql/servers/databases` com nome `master`. As definitions mantêm `addOrReplace`, `conflictEffect=audit` e só selecionam tags ausentes/divergentes. Os scripts de aplicação foram executados na subscription indicada, depois de validação em PLAN.

Os PLANs foram executados com Azure CLI em contexto local temporário para a subscription alvo, restaurado ao final. Fundação confirmou a reutilização dos assignments `openfinops-rg-azure-ti-test-fundacao-telefonica-1-63dbc6fc` e `openfinops-resource-azure-ti-test-fundacao-telefonica-2-63dbc6fc`; ambos mantêm a managed identity e o RBAC Tag Contributor existente. Prisma recebeu os nomes estáveis `of-def-2-78-rg-7750aa7cbe85` / `of-asg-2-78-rg-7750aa7cbe85` para RG. Para recursos, duas execuções chegaram a criar candidatos por uma falha de descoberta: `of-def-2-78-resource-f85c8ff0874e` / `of-asg-2-78-resource-f85c8ff0874e` e `of-def-2-78-resource-2200555412a0` / `of-asg-2-78-resource-2200555412a0`. A primeira definition excluía SQL master com nome literal; a segunda contém a correção `like */master`.

## Correção da duplicidade e verificação final — 15/10/2026

A causa da duplicidade foi o hash de identidade incluir o seletor-base. A correção posterior do seletor SQL `master` mudou esse hash; a execução seguinte gerou um nome novo. O resolvedor anterior procurava apenas IDs de definition com `openfinops`, mas os IDs novos usam `of-def-*`, então não reconheceu o assignment antigo e criou outro. O resolvedor agora procura ambos os formatos e encontra assignments pelo escopo, categoria OpenFinops, regra de origem e camada, reutilizando o objeto existente mesmo quando o seletor evolui.

Mantido o par mais recente, que já contém o seletor correto:

- Assignment `of-asg-2-78-resource-2200555412a0`.
- Definition `of-def-2-78-resource-2200555412a0`.
- Managed identity preservada: `223d101f-8f65-4509-a2b8-9073ecf3a362`, com `Tag Contributor`.

Removidos somente o assignment antigo `of-asg-2-78-resource-f85c8ff0874e`, sua definition `of-def-2-78-resource-f85c8ff0874e` e o RBAC `Tag Contributor` da identidade antiga (`483e845e-a4c3-4ce0-9531-cf3a9db91747`). A remediação antiga ligada a esse assignment (`of-rem-20261015141632-2`) estava `Succeeded`, 0/0 deployments; ela foi preservada como histórico. A consulta atual confirma que o assignment antigo não existe mais e há apenas um assignment Prisma para recursos.

Estado vivo consultado no Azure após a correção:

- RGs: 10/10 corretos — 8 Fundação, 2 Prisma, zero divergências.
- Fundação recursos: 94/94 estados `Compliant` no Policy Insights.
- Fundação RGs: 8/8 estados `Compliant`.
- Prisma recursos: 94 estados retornados, todos `Compliant`; além disso, a consulta ARM encontrou **zero recursos atuais** dentro dos dois RGs Prisma. Portanto, hoje a policy Prisma de recursos não tem recurso filho para remediar; ela protege recursos que vierem a ser criados nesses RGs.
- Prisma RGs: 2/2 estados `Compliant` no Policy Insights.
- SQL `fundacaodw/master`: continua sem tags porque Azure SQL não permite tagging desse recurso.
- Remediação Fundação para RGs `of-rem-20261015140206-1`: `Succeeded`, 8/8 deployments.
- Remediações Prisma novas `of-rem-20261015160632-1` (RG) e `of-rem-20261015160653-2` (resource): na consulta final ainda `Evaluating`, 0/0 deployments. Isso não indica falha de tagging: os RGs estão diretamente confirmados e `Compliant`, e não existem recursos filhos nos dois RGs Prisma. Ainda assim, o estado terminal dessas tarefas não foi confirmado.

A conexão `Azure TI Cost Management` permanece com client secret inválido e o Trino local indisponível (503). Isso impediu validar/exportar pelo endpoint oficial e consultar novamente bronze/Silver pela aplicação. Nenhum custo histórico, dado bronze, classificação financeira ou organograma foi modificado. Os scripts/JSON foram preparados com os dados confirmados na investigação; scripts corrigidos foram validados em PLAN e a execução Prisma reutilizou o assignment único existente.
