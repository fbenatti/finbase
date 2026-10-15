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

Os dois scripts PLAN e os dois scripts de aplicação são restritos ao GUID acima. Fundação (#52) inclui exclusão por seletor dos dois padrões Prisma. Prisma (#78) só seleciona `PCCAGENTLESSSCANRESOURCEGROUP` e `dig-security-rg`. Todas as policies de recursos excluem explicitamente `Microsoft.Sql/servers/databases` com nome `master`. As definitions mantêm `addOrReplace`, `conflictEffect=audit` e só selecionam tags ausentes/divergentes. Os scripts de aplicação não foram executados.

Os PLANs foram executados com Azure CLI em contexto local temporário para a subscription alvo, restaurado ao final. Fundação confirmou a reutilização dos assignments `openfinops-rg-azure-ti-test-fundacao-telefonica-1-63dbc6fc` e `openfinops-resource-azure-ti-test-fundacao-telefonica-2-63dbc6fc`; ambos mantêm a managed identity e o RBAC Tag Contributor existente. Prisma não tinha assignments candidatos e recebeu os nomes estáveis `of-def-2-78-rg-7750aa7cbe85`, `of-asg-2-78-rg-7750aa7cbe85`, `of-def-2-78-resource-2200555412a0` e `of-asg-2-78-resource-2200555412a0`.

## Depois desta implementação

Definitions e assignments foram atualizados/criados na subscription alvo. Fundação reutilizou os dois assignments e principais originais. Prisma tem dois assignments novos e identidades próprias com Tag Contributor na subscription. Como as remediações ficaram em `Evaluating` com 0/0 deployments, as três tags gerenciadas foram aplicadas diretamente aos 10 Resource Groups usando atualização por chave; tags adicionais existentes foram preservadas. A leitura posterior confirmou 8 RGs Fundação e 2 Prisma corretos; os 94 recursos tagueáveis continuam com tags Fundação corretas, e o SQL `fundacaodw/master` segue sem tags.

As quatro remediações novas continuam pendentes de avaliação; não foram reportadas como concluídas. Policy Insights ainda mostra estados anteriores (8 RGs e 94 recursos `NonCompliant`) sem estados para os dois assignments Prisma, portanto a conformidade final precisa de nova avaliação Azure. O serviço local permanece sem regras ativas #52/#78, a conexão `azure-ti` falha pelo client secret inválido e o Trino retornou 503; os JSON/scripts foram montados com as regras e valores confirmados na investigação e validados em PLAN. Para fechar a conformidade, restaure a credencial, disponibilize o Trino e gere pela exportação oficial; depois confirme a avaliação atual dos quatro assignments e a conclusão das remediações sem alterar custos históricos.
