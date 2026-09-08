# 07 — Azure Tag Policy (Criação de Tags)

Geração de Azure Policy a partir das regras de classificação, exposta na aba
**"Criação de Tags"** da página **Regras de Classificação**.

## Objetivo

As regras de negócio do OpenFinops classificam as linhas de custo Azure em
departamentos (`departamento_nivel_1..5`), centro de custo e ambiente. Esses
mesmos valores podem ser materializados como **tags** nos recursos Azure, para o
time de Cloud reconciliar governança com o billing.

O módulo gera, para cada tenant Azure, os arquivos de política (JSON de definição
+ script `az` de deploy com Remediation Tasks), que o usuário baixa e envia ao
time de Cloud.

## Mapeamento de tags

As tags são configuradas na subaba **Mapeamento de Tags**. A chave é livre e o
valor deve ser um campo existente no MODELO FOCUS; a interface impede a seleção
de um campo inexistente. O mapeamento é global para os tenants.

| Chave Azure | Campo FOCUS |
|---|---|
| `projeto` | `departamento_nivel_5` |
| `diretoria` | `departamento_nivel_4` |
| `centro-de-custos` | `centro_custo` |

As operações usam `addOrReplace`; portanto, uma tag existente com a mesma chave
é substituída pelo valor definido pela policy.

## Lógica reversa FOCUS → alias Azure

As regras de classificação referenciam colunas FOCUS (`ResourceId`,
`resourceGroupName`, `subscriptionName`, `ResourceName`, `ResourceType`,
`RegionName`). A Azure Policy usa campos/aliases ARM. A reversão acontece em
`azure_tag_policy_service.py` via:

1. `SourceDestinationMapping` (relação `destination_column -> source_column` por
   conexão).
2. Fallback estático `FOCUS_TO_ALIAS`:

| FOCUS (Gold) | Alias/ campo Azure |
|---|---|
| `ResourceName` | `name` |
| `resourceGroupName` | `name` |
| `subscriptionName` | `subscriptionId` |
| `ResourceType` | `type` |
| `RegionName` / `ResourceLocation` / `RegionId` | `location` |
| `ResourceId` | `id` |

## Precedência e compilação

Azure Policy não tem o contrato de "first match wins" das regras de negócio.
Por isso, o exportador ordena regras por `priority, id` e emite condições de
decisão mutuamente exclusivas. Uma regra principal recebe `not` somente de uma
regra anterior quando todas estas condições forem atendidas:

1. a regra anterior é ARM implantável e possui subscription comprovada;
2. as duas regras alcançam ao menos uma mesma subscription;
3. seus seletores ARM podem alcançar o mesmo recurso;
4. elas produzem classificações/tags diferentes.

O compilador faz essa análise de trás para frente a partir da policy principal.
Regras de outros projetos com subscriptions ou seletores comprovadamente
disjuntos são apenas registradas em `pruned_exclusions` para diagnóstico; não
viram `not` no JSON nem comentário de exceção no script. Assim uma policy de
Terra Brasil exclui Terra México por Resource ID, RG ou subscription somente se
a exceção puder realmente estar dentro do alcance da regra Terra Brasil.

Menor `priority` (e, no empate, menor `id`) vence. Portanto, somente regras
anteriores podem ser exceções: uma regra posterior não sobrescreve a principal.

As policies são diretas, em duas saídas por decisão: Resource Groups (`mode:
All`) e recursos filhos (`mode: Indexed`). A herança global de RG não é usada,
pois ela não representa com segurança uma exceção por recurso.

Somente campos observáveis pelo ARM são implantáveis: subscription, RG,
Resource ID, nome, tipo, SKU e localização. Regras de billing como Purchase,
Return, reservas e clones aparecem no relatório de cobertura como não
implantáveis, sem gerar tags.

Nomes de assinatura são resolvidos em GUIDs pelo Azure Resource Graph; um nome
ausente ou ambíguo bloqueia a regra afetada, sem gerar uma policy insegura. O
escopo de assignment de regras sem GUID explícito vem das subscriptions que
aparecem classificadas para aquele projeto em
`iceberg.dimon.dest_dm_cost_visao_completa`, e não de todas as subscriptions
acessíveis à credencial Azure.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/azure-tag-policy/tenants` | Lista conexões Azure (tenants) |
| GET | `/azure-tag-policy/export?connection_id=&department_id=&fmt=&plan_only=` | Gera políticas e/ou script; `department_id` produz o teste isolado e `plan_only=true` torna PLAN o padrão do script |

## Exemplo de download

Na UI, o usuário seleciona um tenant e clica em "Gerar políticas"; opcionalmente
seleciona um projeto organizacional para testar somente suas policies. O teste
mantém as exclusões de regras de maior prioridade. Em seguida
"Baixar arquivos" produz:

- `azure-tag-policy-<code>-<data>.json` — um por tenant.
- `azure-tag-policy-deploy-<data>.sh` — script único com assignments e
  `az policy remediation create`.

O checkbox **Gerar script em modo PLAN** define literalmente `PLAN_ONLY="true"`
no arquivo baixado; quando desmarcado, o arquivo contém `PLAN_ONLY="false"`.
Em PLAN o script consulta Azure em modo somente leitura e relata `CREATE`, `UPDATE`,
`UNCHANGED` ou `ABORT` para definitions, assignments, RBACs, remediations e trigger
scan, sem criar ou alterar recursos Azure.

## Execução segura do script

O script cria uma definition/assignment ausente e atualiza uma existente apenas
quando sua definition tiver `metadata.category=OpenFinops`. Uma definition ou
assignment com o mesmo nome associado a outra policy interrompe a execução, sem
sobrescrever recursos de terceiros. A identidade system-assigned existente é
preservada e uma ausente é associada ao assignment.

Cada definition possui `metadata.openfinopsContentHash`, um SHA-256
determinístico do conteúdo efetivo (`mode`, `displayName`, `description` e
`policyRule`). O hash **não** inclui o próprio bloco `metadata`, evitando
instabilidade circular. O script compara esse valor para marcar uma definition
OpenFinops inalterada como `UNCHANGED`, em vez de atualizá-la.

| Variável | Padrão | Uso |
|---|---|---|
| `LOCATION` | `eastus2` | Região da identidade gerenciada |
| `PLAN_ONLY` | `false` | Com `true`, não executa mutações Azure |
| `WAIT_FOR_REMEDIATION` | `false` | Aguarda no máximo cinco minutos pelo estado assíncrono |
| `RETRY_ATTEMPTS` | `5` | Tentativas para RBAC da identidade recém-criada |
| `RETRY_DELAY_SECONDS` | `5` | Intervalo entre tentativas de RBAC |

São necessárias permissões para ler/criar/atualizar policy definitions e
assignments, criar a identidade gerenciada, conceder `Tag Contributor` no
escopo da assinatura e criar/consultar remediation tasks. O script faz retry
limitado para a propagação Microsoft Entra ID e falha visivelmente em RBAC ou
remediation.

Uma policy `modify` não corrige recursos existentes sozinha: a remediation task
é necessária. O script dispara `az policy state trigger-scan --no-wait` na
assinatura já selecionada (o CLI não aceita `--resource` com o path ARM da
subscription) e, em cada APPLY, cria uma **nova** remediation pontual com nome
único (`of-rem-<UTC timestamp>-<seq>`) e `--resource-discovery-mode ReEvaluateCompliance`.
Em PLAN essa task aparece como `CREATE`, não como `UNCHANGED` de uma task antiga.

### Reexecução (não é preciso matar a policy)

Não apague definition nem assignment para “atualizar”. O conteúdo OpenFinops é
sobrescrito **no mesmo nome** via `az policy definition update` quando
`openfinopsContentHash` mudou; se o hash for igual, a definition fica `UNCHANGED`.
O assignment e a managed identity SystemAssigned existentes são preservados.

- Recursos **novos** que já batem na condição: o assignment continua; o efeito
  `modify` aplica tags na criação. Recursos existentes ainda sem tag precisam de
  uma **nova remediation** (reexecute o script em APPLY).
- Mudança de classificação/tags no OpenFinops: o script faz UPDATE da definition
  no lugar e, no APPLY, cria outra remediation para o estoque já existente.

Remediações Azure são registros pontuais: o script **não** apaga tasks
anteriores. Elas acumulam no Azure; use o portal ou `az policy remediation list`
para histórico. O script imprime o comando de acompanhamento e não espera por
padrão a avaliação assíncrona.

Definitions e metadata são passadas ao CLI com prefixo `@arquivo`, conforme
`az policy definition create --rules` / `--metadata`. O ID da definition no
PLAN (`$scope/providers/Microsoft.Authorization/policyDefinitions/$name`) é o
formato de definition no nível da subscription, o mesmo retornado por
`az policy definition show --query id`.

## Limitações

- `like`/`notLike` do Azure Policy aceitam no máximo **um wildcard `*`**. Regras
  com regex complexa são simplificadas para `contains`/`like`/`equals`.
- O módulo não cobre regras de billing sem representação ARM (reservas,
  compras, ajustes etc.); elas ficam somente no diagnóstico.
- Isto não substitui uma política corporativa completa de exemptions, auditoria
  e gestão de mudanças Azure.
