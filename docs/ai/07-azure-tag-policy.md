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

## Regra de compactação (3 camadas)

Para não exceder os limites do Azure Policy (400/initiative, 4000/tenant), as
políticas são emitidas em três camadas:

1. **Herança de RG** — built-in `cd3aa116-8754-49c9-a813-ad46512ece54`
   ("Inherit a tag from the resource group"), uma atribuição por tag.
2. **Padrão nome/SKU/região** — casamentos por `name like`/`contains`, `location`
   ou alias como `Microsoft.Compute/virtualMachines/sku.name`. Cobre casos como
   "Terra México" (nome) e "família de servidores" (SKU family).
3. **Resource Groups explícitos** — agrupados por combinação única de tags, com
   `field: name in [...]`.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/azure-tag-policy/tenants` | Lista conexões Azure (tenants) |
| GET | `/azure-tag-policy/export?connection_id=&fmt=` | Gera políticas e/ou script (`fmt=json|script|both`) |

## Exemplo de download

Na UI, o usuário seleciona um tenant e clica em "Gerar políticas"; em seguida
"Baixar arquivos" produz:

- `azure-tag-policy-<code>-<data>.json` — um por tenant.
- `azure-tag-policy-deploy-<data>.sh` — script único com assignments e
  `az policy remediation create`.

## Limitações

- `like`/`notLike` do Azure Policy aceitam no máximo **um wildcard `*`**. Regras
  com regex complexa são simplificadas para `contains`/`like`/`equals`.
- O escopo da assignment (`$SCOPE`, management group ou subscription) é preenchido
  pelo time de Cloud antes de rodar o script.
