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

## Contrato de tags

| Tag | Origem |
|---|---|
| `departamento_nivel_1`..`departamento_nivel_5` | caminho raiz→folha de `Department` |
| `centro_custo` | `Department.centro_custo` herdado |
| `ambiente` | `Department.ambiente` herdado |

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
| GET | `/azure-tag-policy/export?connection_id=&department_id=&fmt=` | Gera políticas e/ou script; `department_id` produz o teste isolado de um projeto organizacional |

## Exemplo de download

Na UI, o usuário seleciona um tenant e clica em "Gerar políticas"; opcionalmente
seleciona um projeto organizacional para testar somente suas policies. O teste
mantém as exclusões de regras de maior prioridade. Em seguida
"Baixar arquivos" produz:

- `azure-tag-policy-<code>-<data>.json` — um por tenant.
- `azure-tag-policy-deploy-<data>.sh` — script único com assignments e
  `az policy remediation create`.

## Limitações

- `like`/`notLike` do Azure Policy aceitam no máximo **um wildcard `*`**. Regras
  com regex complexa são simplificadas para `contains`/`like`/`equals`.
- O script usa as subscriptions GUID descobertas, cria identidade gerenciada,
  concede Tag Contributor e cria remediation tasks. O operador precisa de
  permissão para criar policy definitions, assignments e role assignments.
