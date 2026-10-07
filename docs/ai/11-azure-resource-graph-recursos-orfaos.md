# 11 — Governança de Recursos Órfãos via Azure Resource Graph

> Proposta de arquitetura: coleta de **inventário e recursos órfãos do Azure** direto
> da nuvem via **Azure Resource Graph (ARG)**, seguindo exatamente o mesmo padrão do
> conector **Azure Automation — Jobs 4P/TI** (`azure_automation_jobs`), com visão
> multi-cloud no futuro (GCP, OCI, IBM, AWS).

## 1. Objetivo

Criar, na página **Governança**, uma estrutura para **buscar recursos órfãos**:

1. **Fonte de dados dentro de ferramentas cloud** — novo conector na tela Conexões
   (categoria "Ferramentas"), igual ao "Azure Automation".
2. **Credenciais** — reutilizar as mesmas Service Principals `azure-4p` / `azure-ti`
   já usadas pelo Automation (nada novo de infra).
3. **Download Bronze** — a extração (worker) consulta a API do ARG e grava o resultado
   cru no Iceberg (`bronze.raw_<code>`), registrando o `IngestedBlob`.
4. **Normalização em tabela destino** — setup automático de `destination_table` +
   `SourceDestinationMapping`; o reprocess normaliza Bronze → Silver → Gold, e a aba
   de governança consulta a Gold via Trino.

## 2. O padrão atual (blueprint: `azure_automation_jobs`)

Fluxo completo que será replicado:

```
Conexões (UI)                    API                     Worker                     Iceberg / Metadata
─────────────                    ───                     ──────                     ───────────────────
cria conexão                    POST /connections        │
connector_type=                 └─ setup automático:      │
  azure_automation_jobs           dest_modelo_automation │
                                  (+ schedule)           │
"Testar"        ──────────────►  discover_accounts (ARM) │
"Scan"          ──────────────►  discover_accounts (ARM) │
"Extrair" (run) ──────────────►  etl_task QUEUED ───────►│ worker.py: extraction
                                                          │  └─ run_automation_collection
                                                          │     ├─ ClientSecretCredential
                                                          │     ├─ GET ARM jobs/schedules
                                                          │     └─ iceberg.write_bronze
                                                          │        blob: automation/jobs/YYYYMM.csv
                                                          │        IngestedBlob (billing_period)
Reprocess ───────────────────────────────────────────────►│ mapeia Bronze→Gold
                                                          │  dest_modelo_automation (Gold)
Governança: "Desligue Programado" ◄───── Trino ◄──────────┘  query sobre a Gold
```

Pontos-chave do padrão:

- **Credenciais**: `config_json.credential_profile = "azure-4p" | "azure-ti"` →
  `resolve_config()` busca `tenant_id/client_id/client_secret` em `Settings`
  (carregados de `.env-4p` / `.env-ti`). Alternativa: credenciais direto no `config_json`.
- **Bronze**: `iceberg.write_bronze(conn.code, df, billing_period, blob_path)` +
  registro em `IngestedBlob` com `blob_path` particionado (`automation/jobs/…`,
  `automation/schedules/…`).
- **Setup automático**: ao criar a conexão, `_setup_for_connection` cria a
  `destination_table` (colunas + tipos Iceberg) e os `SourceDestinationMapping`
  (RENAME 1:1) — ver `routers/scheduled_shutdown.py`.
- **Reprocess**: o endpoint de reprocess filtra os blobs Bronze pelo prefixo
  (`automation/jobs/` vs `automation/schedules/`) e aplica mapping → regras →
  classificação → Gold (`routers/etl.py`).
- **Governança**: a aba "Desligue Programado" consulta a Gold via
  `trino_service.execute` e cruza com FOCUS para calcular saving.

## 3. Design proposto — Azure Resource Graph

### 3.1 O endpoint

| Item | Valor |
|---|---|
| API | `POST https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01` |
| Auth | Mesmo `ClientSecretCredential` (scope `https://management.azure.com/.default`) |
| Body | `{ "query": "<KQL>", "subscriptions": [...], "options": { "$top": 1000, "$skipToken": "…" } }` |
| Resposta | `{ "data": { "columns": [{name,type}], "rows": [[…]] }, "$skipToken", "count" }` |
| Paginação | `$skipToken` — limite de **1000 linhas/página**; ~10.000 linhas por query (acima disso, particionar por subscription) |
| Custo | API gratuita; custo é de IAM/permissão apenas |

### 3.2 Conector novo

- **`connector_type`**: `azure_resource_graph` (label UI: "Azure Resource Graph").
- **Categoria**: `ferramenta` (entra em `TOOL_CONNECTOR_TYPES` em `routers/connections.py`).
- **`config_json`**:
  ```json
  {
    "credential_profile": "azure-4p",
    "subscription_ids": ["sub1", "sub2"],
    "snapshot_mode": "daily",
    "orphan_rules": {
      "vm_deallocated":  "resources | where type =~ 'microsoft.compute/virtualmachines' | …",
      "disk_unattached": "resources | where type =~ 'microsoft.compute/disks' | …",
      "public_ip_idle":  "…",
      "snapshot_orphan": "…"
    }
  }
  ```
  - `orphan_rules` são **configuráveis por conexão** (default no código); cada regra vira
    uma fatia Bronze (`arg/orphans/<rule>/<snapshot>.csv`) e uma flag na Gold.
  - Se vazio, coleta apenas o **inventário completo** (`resources | project …`).

### 3.3 Serviço (`services/azure_resource_graph_service.py`)

Espelho de `azure_automation_service.py`:

1. `resolve_config(config)` / `_credential(config)` — **reutilizar** o mesmo mecanismo
   (`credential_profile` → `Settings.azure_{profile}_*`). Pode ser extraído para um
   helper compartilhado (`azure_identity.py`) para evitar duplicação.
2. `run_query(client, headers, kql, subscriptions, skip_token)` — POST ARG + paginação
   via `$skipToken`, com `resultFormat` padrão `table` (columns+rows) e conversão para
   `pd.DataFrame`.
3. `collect_snapshot(config, snapshot_date)` — executa a query de inventário + cada
   regra de órfão, devolve `{rule: DataFrame}`.
4. `run_resource_graph_collection(session, connection_id, *, snapshot_date, task_id)`
   — padrão idêntico ao `run_automation_collection`:
   - `iceberg.delete_bronze_slice(conn.code, blob_path=…, billing_period=…)` (recoleta idempotente)
   - `iceberg.write_bronze(...)` com `billing_period = YYYYMM` da snapshot
   - registra `IngestedBlob` (prefixo `arg/inventory/` e `arg/orphans/<rule>/`)
   - atualiza `EtlTask` (status, phase, rows_raw, result_json)

### 3.4 Integração ponto a ponto (arquivos)

| Arquivo | Mudança |
|---|---|
| `app/routers/connections.py` | adicionar `azure_resource_graph` em `TOOL_CONNECTOR_TYPES` + `GRID_FILTER_VALUES`; hook de setup automático no `create_connection` |
| `app/routers/etl.py` | branchs de **scan** (query leve de contagem/validação), **test** (idem) e **run** (permitir o connector_type) |
| `app/services/azure_resource_graph_service.py` | **novo** — coleta ARG + Bronze |
| `app/routers/azure_resource_graph_setup.py` (ou dentro de `scheduled_shutdown.py`) | **novo** — `_setup_for_connection` idempotente: destination tables + mappings |
| `app/services/task_runner.py` | permitir o connector_type no guard de tipos válidos |
| `api/worker.py` | branch `extraction` para `azure_resource_graph` (espelho do bloco automation, linhas ~852–894) |
| `app/routers/governance_orphans.py` | **novo** — endpoints da aba (filtros, listagem, resumo, potencial de economia) via `trino_service.execute` |
| `apps/web/src/pages/Connections/Connections.tsx` | opção no wizard (campos: subscription_ids, snapshot_mode, orphan_rules opcional) |
| `apps/web/src/pages/Governance/Governance.tsx` | nova aba **"Recursos Órfãos"** |

### 3.5 Tabelas destino (Gold)

**`dest_cloud_resource_inventory`** — "MODELO INVENTÁRIO CLOUD" (já pensado multi-cloud):

| Coluna | Tipo | Origem |
|---|---|---|
| resource_id | TEXT | `id` |
| resource_name | TEXT | `name` |
| resource_type | TEXT | `type` |
| cloud_scope | TEXT | `azure` (futuro: gcp/oci/ibm/aws) |
| subscription_id | TEXT | `subscriptionId` |
| resource_group | TEXT | derivado do `id` |
| location | TEXT | `location` |
| sku | TEXT | `sku.name` / `sku.tier` |
| tags | TEXT (JSON) | `tags` |
| power_state | TEXT | `properties.extended.instanceView.powerState.code` (VMs) |
| created_at | TIMESTAMPTZ | `properties.timeCreated` |
| snapshot_date | DATE | data da coleta |
| source_connection_id / cloud_origem / _billing_period | — | internas do reprocess |

**`dest_cloud_orphan_resources`** — "MODELO RECURSOS ÓRFÃOS":

| Coluna | Tipo | Origem |
|---|---|---|
| resource_id / resource_name / resource_type | TEXT | idem |
| subscription_id / resource_group / location | TEXT | idem |
| orphan_rule | TEXT | nome da regra KQL (ex.: `disk_unattached`) |
| orphan_reason | TEXT | descrição (ex.: "Disco não anexado há N dias") |
| tipo_recurso | TEXT | classificação de negócio (DISCO / VM / IP / SNAPSHOT / LOAD_BALANCER) — **não usar `tipo`** (reservado pela plataforma: realizado/forecast) |
| age_days | INTEGER | dias desde criação/last activity |
| estimated_monthly_cost_brl | DOUBLE | preenchido pela Dimon (join FOCUS) |
| tags | TEXT (JSON) | |
| snapshot_date / _billing_period / source_connection_id / cloud_origem | — | internas do reprocess |

> Multi-cloud: como o destino é agnóstico (`cloud_scope`), cada conector de nuvem
> (GCP/OCI/IBM) escreve na MESMA Gold — a visão de governança é única. Essa é a
> resposta para "faz sentido uma coleta de dados cloud": **sim, 1 modelo Gold
> normalizado + 1 conector por cloud**, em vez de N destinos isolados.

### 3.5.1 Exclusão de discos PVC/AKS (não são órfãos)

Discos gerenciados criados pelo driver do AKS (`kubernetes-azure-dd`) ficam
"Unattached" no ARG quando o node pool é reduzido, mas estão **vinculados a um
PVC/PV**. A regra `disk_unattached` os exclui por tag e por prefixo:

```kusto
| where isnull(tags['kubernetes.io-created-for-pvc-name'])
  and isnull(tags['kubernetes.io-created-for-pv-name'])
  and not (name startswith 'pvc-')
```

Na coleta real de 2026-08: **213 → 5 discos órfãos** (208 eram PVC/AKS, alguns com
custo relevante — ex.: disco de 8192 GB a R$ 2.436/mês). O inventário completo
continua guardando os PVCs (com tags) para eventuais análises de capacidade.

### 3.5.2 Custo por recurso órfão — cubo Gold (join com FOCUS)

A regra Gold `custo_recursos_orfaos` (criada/atualizada no setup ou pelo endpoint
`POST /azure-resource-graph/seed-gold`) materializa o custo dos órfãos cruzando
o detalhe Silver com o detalhe FOCUS. O destino de órfãos é compartilhado entre
as conexões 4P e TI; `cloud_origem` e `source_connection_id` mantêm a origem,
enquanto os valores `azure-resource-graph-4p` e `azure-resource-graph-ti` são
traduzidos para `azure-4p` e `azure-ti` no join de custo:

```sql
WITH orphan_ids AS (
  SELECT DISTINCT lower(resource_id) AS rid, "_billing_period",
         CASE cloud_origem
           WHEN 'azure-resource-graph-4p' THEN 'azure-4p'
           WHEN 'azure-resource-graph-ti' THEN 'azure-ti'
           ELSE cloud_origem END AS focus_cloud_origem
  FROM iceberg.silver.dest_cloud_orphan_resources
), focus_dedup AS (
  SELECT resourceid, "_billing_period", cloud_origem, billedcost, effectivecost, realizado
  FROM (
    SELECT f.*, MIN(f.resourceid) OVER (
      PARTITION BY lower(f.resourceid), f."_billing_period", f.cloud_origem
    ) AS canonical
    FROM iceberg.silver.dest_modelo_focus f
    JOIN orphan_ids o ON lower(f.resourceid) = o.rid
      AND f."_billing_period" = o."_billing_period"
      AND lower(f.cloud_origem) = lower(o.focus_cloud_origem)
  ) WHERE resourceid = canonical
)
SELECT o."_billing_period", o.resource_id, o.resource_name, o.orphan_rule,
       o.tipo_recurso, o.subscription_id, o.resource_group, o.disk_size_gb,
       o.age_days, o.snapshot_date, o.cloud_origem, o.source_connection_id,
       COUNT(f.resourceid) AS focus_rows,
       ROUND(SUM(COALESCE(TRY_CAST(f.billedcost AS DOUBLE), 0)), 2) AS billedcost,
       ROUND(SUM(COALESCE(TRY_CAST(f.effectivecost AS DOUBLE), 0)), 2) AS effectivecost,
       ROUND(SUM(COALESCE(TRY_CAST(f.realizado AS DOUBLE), 0)), 2) AS cost_brl
FROM iceberg.silver.dest_cloud_orphan_resources o
LEFT JOIN focus_dedup f ON lower(f.resourceid) = lower(o.resource_id)
  AND f."_billing_period" = o."_billing_period"
  AND lower(f.cloud_origem) = lower(CASE o.cloud_origem
    WHEN 'azure-resource-graph-4p' THEN 'azure-4p'
    WHEN 'azure-resource-graph-ti' THEN 'azure-ti'
    ELSE o.cloud_origem END)
GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12
```

A materialização (`POST /gold/reprocess` com `rule_ids`) cria
`gold.dest_dm_cost_custo_recursos_orfaos` pré-agregada; os endpoints de governança
(`/summary` → `cost_brl`/`by_tipo`; `/orphans` → `cost_brl` por linha) fazem apenas
LEFT JOIN leve com essa materialização, sem varrer o FOCUS por requisição. Foco no
particionamento `_billing_period` nas duas tabelas.

> **Custo = coluna `realizado`**: a soma do custo do órfão usa a coluna
> **`realizado`** da Gold FOCUS (custo realizado em BRL com taxas/markup aplicados
> pelas regras de negócio; `tipo='realizado'`), não o `billedcost` bruto. Exemplo:
> disco de 120 GB → billed R$ 50,77 → **realizado R$ 60,21**. `billedcost` e
> `effectivecost` ficam como colunas de referência na Dimon. Total realizado dos
> órfãos (202608): **R$ 15.901,45**.

> **Case-sensitivity do join**: o ARG devolve o resource group no case armazenado
> (`pro-monitor`), enquanto o export FOCUS normaliza para `PRO-MONITOR`. O join é
> `LOWER(f.resourceid) = LOWER(o.resource_id)` — sem isso apenas 1 de 4 discos
> casava. O template restringe ao período mais recente dos órfãos
> (`WHERE o."_billing_period" = (SELECT MAX(...))`) para podar a partição do FOCUS
> (materialização ~30s vs vários minutos sem a restrição).
>
> **Dedupe de case no FOCUS**: o export pode conter o mesmo recurso em DUAS
> variantes de case (11 recursos, ~527 linhas em 202608) — sem dedupe o custo era
> somado em dobro (~R$ 3.981 a mais em LBs). O template usa `focus_dedup`:
> `MIN(resourceid) OVER (PARTITION BY lower(resourceid), período)` com o filtro de
> `orphan_ids` ANTES do window (evita varrer o FOCUS inteiro). Total corrigido:
> R$ 13.438,21/mês.
>
> **Carga enxuta**: as regras KQL de órfãos já projetam apenas as colunas
> necessárias (~9-10). O inventário completo (~8.6k linhas, ~8s) é **opcional** via
> `collect_inventory: false` na conexão — desativado nas conexões 4P/TI para carga
> focada em órfãos (~5s).
>
> **Falsos positivos de VMs**: VMs desalocadas **intencionalmente** (automação,
> segurança, DR, patching agendado) não são órfãs. A regra `vm_deallocated` exclui
> VMs com a tag **`Patch Management`** (qualquer valor: "Desligada" ou schedule —
> ex.: "Servicos - Toda Quarta"). Exemplo: `4p-pro-security-automacao-vm-br` saiu
> da lista (19 → 14 VMs).
>
> **Achado de qualidade**: ~122 dos 128 `lb_idle` com custo são LBs
> `kubernetes-internal`/`kubernetes` de clusters AKS (RGs `MC_*`/`*-aks-*`) — o ARG
> mostra backend pool vazio, mas o LB pode estar em uso (backend = nós VMSS). Precisa
> validação de negócio antes de excluir da regra `lb_idle` (ao contrário dos discos
> PVC, que já são excluídos por tag/prefixo/RG `MC_`).

### 3.6 Aba "Recursos Órfãos" na Governança

- Filtros: período (snapshot), subscription, resource group, tipo de recurso, `orphan_rule`, status.
- Grid com os candidatos + KPI cards (total de órfãos, por tipo, potencial de economia BRL/mês).
- Ações de governança: marcar `EXCECAO` / `EM_ANALISE` (persistido na Gold via reprocess
  ou tabela auxiliar de anotações em PostgreSQL — recomendado: tabela auxiliar, pois a
  Gold é derivada).
- Consulta via `trino_service.execute` (padrão do "Desligue Programado").

## 4. Comparativo multi-cloud — o "Azure Resource Graph" de cada nuvem

| Nuvem | Equivalente do ARG | API/SDK | Como encaixa no pipeline |
|---|---|---|---|
| **Azure** | Azure Resource Graph (KQL) | `POST management.azure.com/providers/Microsoft.ResourceGraph/resources` · SDK `azure-mgmt-resourcegraph` | Este documento |
| **GCP** | **Cloud Asset Inventory** — `searchAllResources` | `POST cloudasset.googleapis.com/v1/{scope}:searchAllResources` (`scope` = organization/project) · `google-cloud-asset` | Mapeia 1:1: query + assetTypes + pageToken; requer API Cloud Asset habilitada + permissão `cloudasset.assets.searchAllResources` |
| **OCI** | **Resource Search API** (`/20180409/resources`) + OCI Inventory | SDK `oci.resource_search` (ResourceSearchClient.query_resources) · query estruturada `query all resources where …` · paginação por `page` · escopo por tenancy/compartment | Precisa enumerar compartments (ListCompartments) para cobrir a tenancy; tags freeform/defined no resultado |
| **IBM Cloud** | **Sem equivalente único**: combinação de **Global Search API** (`api.global-search.cloud.ibm.com/search`) + **Resource Controller** (`/v2/resource_instances`) + **Global Tagging** | SDK `ibm_platform_services` (GlobalSearchV2, ResourceControllerV2, GlobalTaggingV2) | Resource Controller lista instâncias por resource group; Global Search pesquisa por query com cursor; inventário = junção dos dois + tags |
| **AWS** (futuro) | **Resource Explorer** (Search) + **Config advanced queries** (SQL sobre `aws_*` tables) | `resource-explorer-2` `Search` + `config` `SelectResourceConfig` | Resource Explorer é o mais parecido com ARG; Config dá estado/histórico |

**Recomendação**: manter o padrão único — cada cloud vira um `connector_type`
(`gcp_asset_inventory`, `oci_resource_search`, `ibm_resource_controller`), todos
gravando no mesmo **`dest_cloud_resource_inventory`** / **`dest_cloud_orphan_resources`**.
A camada de coleta ("fonte de dados dentro de ferramentas cloud") é por nuvem; a camada
de governança é única.

## 5. O que mais extrair do ARG (catálogo FinOps)

Além dos órfãos, o mesmo endpoint entrega dados de altíssimo valor FinOps (basta
adicionar colunas/regras):

1. **Inventário completo de recursos** — base para o "asset register" multi-cloud e
   para cruzar com billing (FOCUS já tem `resource_id`; o join ARG×FOCUS dá
   custo por recurso mesmo sem tags).
2. **Tags** (`owner`, `costcenter`, `ambiente`, `shutdown`, `projeto`, `expires_on`)
   — mede **cobertura de tagging** (%, recursos sem tag), habilita chargeback e
   detecção de órfão organizacional (sem owner/costcenter).
3. **Power state de VMs** (`PowerState/running|deallocated|stopped`) — complementa o
   "Desligue Programado": mostra VMs **fora do schedule** (ligadas 24x7 sem automação)
   → maior oportunidade de saving.
4. **SKU / tamanho / tier / região** — rightsizing (VM superdimensionada) e
   **migração de região** (preço por região), análise de cobertura RI/Commit.
5. **Discos** — `diskState` (Unattached), `diskSizeGB`, `sku` → economia de
   excluir/encolher disco órfão (custo por GB).
6. **Snapshots** — `creationData.sourceResourceId` de disco deletado → snapshot órfão.
7. **IPs públicos** — sem `ipConfiguration` → IP idle cobrado; também NAT gateways,
   LBs vazios, NICs/NSGs sem associação, App Service Plans sem apps, SQL DBs sem conexões.
8. **Data de criação / última modificação** — age de recursos (lifecycle, expiração).
9. **Resource groups sem recursos** — limpeza de casca vazia.
10. **Dimensões de governança extra** (`policyresources`) — compliance, mas fora do escopo FinOps direto.

> **Importante**: ARG não traz custo — só inventário/estado. O valor financeiro
> (economia BRL/mês) vem do **join com FOCUS** (`gold.dest_modelo_focus`) ou com o
> price sheet, já disponíveis na plataforma. Sugestão: criar uma regra Dimon
> "custo estimado de órfãos" (espelho do `saving_desligue_vm`).

## 6. Queries KQL de exemplo (regras de órfão)

```kusto
// 1) Discos não anexados (maior economia clássica)
resources
| where type =~ "microsoft.compute/disks"
| where properties.diskState == "Unattached"
| project resource_id=id, resource_name=name, resource_group=resourceGroup,
          subscription_id=subscriptionId, location, sku=sku.name,
          disk_size_gb=properties.diskSizeGB, tags, created_at=properties.timeCreated

// 2) VMs desalocadas (deallocated) — candidatas a exclusão, não a start
resources
| where type =~ "microsoft.compute/virtualmachines"
| extend power_state = tostring(properties.extended.instanceView.powerState.code)
| where power_state == "PowerState/deallocated"
| project resource_id=id, resource_name=name, resource_group=resourceGroup,
          subscription_id=subscriptionId, location, vm_size=properties.hardwareProfile.vmSize,
          tags, created_at=properties.timeCreated

// 3) IPs públicos não associados
resources
| where type =~ "microsoft.network/publicipaddresses"
| where isnull(properties.ipConfiguration)
| project resource_id=id, resource_name=name, resource_group=resourceGroup,
          subscription_id=subscriptionId, location, sku=sku.name,
          ip_address=properties.ipAddress, tags

// 4) Snapshots cujo disco de origem não existe mais (órfão de snapshot)
resources
| where type =~ "microsoft.compute/snapshots"
| extend source_disk = tostring(properties.creationData.sourceResourceId)
| join kind=leftouter (
    resources | where type =~ "microsoft.compute/disks" | project disk_id = id
  ) on $left.source_disk == $right.disk_id
| where isnull(disk_id)
| project resource_id=id, resource_name=name, resource_group=resourceGroup,
          subscription_id=subscriptionId, location, disk_size_gb=properties.diskSizeGB,
          source_disk, tags

// 5) Inventário completo (base para o asset register / join com FOCUS)
resources
| project resource_id=id, resource_name=name, resource_type=type,
          subscription_id=subscriptionId, resource_group=resourceGroup,
          location, sku=tostring(sku.name), tags,
          power_state = tostring(properties.extended.instanceView.powerState.code),
          created_at = tostring(properties.timeCreated)
```

## 7. Correção do erro Trino 200 e publicação em PRD

O erro `Table 'iceberg.silver.dest_cloud_orphan_resources' does not exist` foi
causado pelo template persistido no PostgreSQL conter literalmente
`{layers.detail_layer()}`: o SQL da regra não era uma f-string. A query falhava
no parser do Trino (erro de `{`), antes de consultar qualquer tabela. Também
havia endpoints usando `silver` hardcoded; esses agora resolvem a camada pela
função central `lakehouse_layers.detail_layer()`.

Após publicar a versão corrigida do backend em PRD:

1. Confirmar no PostgreSQL PRD que Resource Graph está habilitado no catálogo
   das conexões Azure do ambiente. No padrão atual, **não** se cria uma conexão
   `azure_resource_graph` separada: a credencial fica em `azure-4p`/`azure-ti` e
   cada fonte é uma seleção em `azure_connection_source`.

   ```sql
   SELECT sc.id, sc.code, acs.source_key, acs.enabled, acs.subscription_ids
   FROM source_connection sc
   JOIN azure_connection_source acs ON acs.connection_id = sc.id
   WHERE sc.code IN ('azure-4p', 'azure-ti')
     AND sc.connector_type = 'azure_blob'
     AND acs.source_key = 'resource_graph'
   ORDER BY sc.code;
   ```

   Esperado: uma linha habilitada por ambiente que deve compor a visão unificada.
   Não copie IDs de desenvolvimento: os IDs variam entre ambientes. Se a fonte
   estiver desligada, habilite-a em **Carregando fontes Azure** antes de coletar.
2. Confirmar destinos e mappings associados aos IDs reais das conexões do ambiente:

   ```sql
   SELECT sc.code, d.physical_name, COUNT(m.id) AS mappings_ativos
   FROM source_connection sc
   JOIN source_destination_mapping m ON m.source_connection_id = sc.id AND m.active
   JOIN destination_table d ON d.id = m.destination_table_id
   WHERE sc.code IN ('azure-4p', 'azure-ti')
     AND d.physical_name IN ('dest_cloud_orphan_resources', 'dest_cloud_resource_inventory')
   GROUP BY sc.code, d.physical_name
   ORDER BY sc.code, d.physical_name;
   ```

   Se a seleção estiver habilitada e faltarem destinos/mappings, chamar
   `POST /azure-resource-graph/setup` (ou habilitar novamente a fonte; o setup
   agora é acionado pelo catálogo). O endpoint é idempotente e não cria conexão
   standalone.
3. Executar, autenticado na API PRD, `POST /azure-resource-graph/seed-gold`.
   O seed é idempotente: atualiza a regra PostgreSQL `gold_mart_rule` de código
   `custo_recursos_orfaos`, grava o SQL com o namespace resolvido para aquele
   ambiente, define `materialization_status = 'PENDING'` e limpa o erro anterior.
   Não é necessário alterar manualmente `destination_table` nem criar uma nova
   regra.
4. Verificar no PostgreSQL PRD:

   ```sql
   SELECT id, code, materialization_status, materialization_error,
          materialization_table, updated_at
   FROM gold_mart_rule
   WHERE code = 'custo_recursos_orfaos';
   ```

   `materialization_status` deve estar `PENDING` antes da execução, e o campo
   `sql_template` não pode conter `{layers.detail_layer()}`. A tabela final
   configurada permanece `dest_dm_cost_custo_recursos_orfaos`.
5. Reprocessar somente essa regra em `POST /gold/reprocess` usando seu `rule_id`;
   depois confirmar `materialization_status = 'UPDATED'`, ausência de erro e
   consulta bem-sucedida no dashboard.
6. Confirmar que `silver.dest_cloud_orphan_resources` contém as duas origens
   (4P e TI) e que a Gold mantém `cloud_origem`/`source_connection_id`, sem
   misturar custos: o join deve continuar particionado pela origem Azure
   correspondente (`azure-4p` ou `azure-ti`).

O seed não altera dados de custo diretamente nem cria uma tabela Silver. A Gold
é reconstruída pelo fluxo normal de materialização. Em produção, executar os
passos após o deploy e validar primeiro a contagem por `cloud_origem`; não rodar
DDL manual no Trino.

O startup da API reconcilia os destinos/mappings das fontes configuradas, mas a
coleta continua controlada pela seleção do catálogo. Para conferir Bronze e
Silver após a coleta/reprocessamento, use o histórico da tarefa e valide o
`source_key = resource_graph`, o `source_connection_id` do ambiente e o snapshot
selecionado. Uma tarefa anterior em `FAILED` não invalida uma tentativa posterior:
considere a tentativa final do grupo e seus blobs/linhas processados.

## 8. Plano de implementação (ordem sugerida)

1. **`services/azure_resource_graph_service.py`** (novo) — coleta + Bronze; testar com
   `python -c` contra um tenant (valida permissão da SPN).
2. **Setup de destino** — criar `dest_cloud_resource_inventory` e
   `dest_cloud_orphan_resources` + mappings (função idempotente; hook no
   `create_connection`).
3. **Backend router** — scan/test/run em `etl.py`; guard de tipos em `task_runner.py`;
   branch no `worker.py`.
4. **Frontend** — opção no wizard de Conexões; aba "Recursos Órfãos" na Governança
   (+ router `governance_orphans.py` consultando Trino).
5. **Valor financeiro** — regra Dimon "custo estimado de órfãos" (join FOCUS).
6. **Multi-cloud** — repetir 1–3 para `gcp_asset_inventory`, `oci_resource_search`,
   `ibm_resource_controller` (mesmo destino Gold).

## 9. Riscos e limites

- **Permissão**: a SPN precisa de leitura (Reader) nas subscriptions e a query do ARG
  exige o provider `Microsoft.ResourceGraph` — para escopo tenant-wide, papel no
  management group raiz (restrito); recomendado **por subscription** (`subscription_ids`).
- **Limites ARG**: 1.000 linhas/página, ~10.000 por query sem particionar; para
  inventários grandes, particionar por subscription (o serviço deve fazer isso
  automaticamente quando `count == 10.000`).
- **Rate limit**: ~10–15 requisições por 5s por usuário/SPN — coletar regras
  sequencialmente com pequeno `sleep` (padrão já usado no Automation com semáforo).
- **Dado de estado ≠ dado de custo**: órfão técnico é indicador; o valor financeiro
  exige join com FOCUS/price sheet.
- **"Órfão" é regra de negócio**: as definições (N dias desligado, sem tag, disco
  unattached) devem ficar **configuráveis** na conexão/regras — não hardcoded.

## 10. Referências

- Azure Resource Graph REST: https://learn.microsoft.com/azure/governance/resource-graph/concepts/query-language
- Consultas ARG para cost optimization (marketplace aiskillstore): https://github.com/aiskillstore/marketplace/blob/main/skills/microsoft/azure-cost-optimization/references/azure-resource-graph.md
- Playbook azure-orphan-disks (OptimNow/cloud-finops-skills): https://github.com/optimnow/cloud-finops-skills/blob/main/skills/cloud-finops/playbooks/azure-orphan-disks.md
- Cloud Asset Inventory `searchAllResources` (GCP): https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources
- OCI Querying Resources (Resource Search): https://docs.oracle.com/en-us/iaas/Content/Search/Tasks/queryingresources.htm
- IBM Cloud Global Search API: https://cloud.ibm.com/apidocs/search
- IBM Cloud Resource Controller API: https://cloud.ibm.com/apidocs/resource-controller
- AWS Resource Explorer: https://docs.aws.amazon.com/resource-explorer/latest/userguide/what-is.html
