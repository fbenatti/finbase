# Desligue Programado — Documentação de Uso

A aba **Desligue Programado** no `Dimon Dashboard` mede o saving gerado pelo desligue automático de VMs Azure (via Azure Automation) e permite criar regras de exceção para pools de recursos que devem permanecer ligados.

## Coleta real via ARM (padrão das coletas de PTU)

A conexão **Azure Automation** coleta direto da nuvem (ARM API), sem upload manual:

1. `tenant_id` / `client_id` / `client_secret` (ou `credential_profile` = `azure-4p` / `azure-ti`).
2. **Scan** descobre as Automation Accounts nas subscriptions.
3. **Extração** (`/etl/run` → worker) coleta:
   - **jobs** (últimos `backfill_days`, padrão 93): `JobId, Time, RunbookName, Status, VM, ResourceGroup, SubscriptionId, Action, CorrelationId, Caller, ResourceId`.
   - **schedules** (snapshot atual): `SnapshotDate, ScheduleName, ScheduleRunbook, ResourceId, VM, ResourceGroup, SubscriptionId, Enabled, NextRunTime, LastJobTime, ScheduleStatus`.
4. Persiste na Bronze `raw_<connection_code>` (jobs e schedules na mesma tabela, com `_blob_path` distinto).
5. **Reprocess** do destino `MODELO AUTOMATION` filtra apenas blobs `automation/jobs/`; `MODELO AUTOMATION SCHEDULE` filtra `automation/schedules/`.

Arquivos: `apps/api/app/services/azure_automation_service.py`, `apps/api/worker.py`, `apps/api/app/routers/etl.py`.

### Limitação conhecida

Os runbooks da 4P/TI são **por tag** (`StartStopMySQLFlexible`, `StartStopAppGwByTag`, …): os jobs não trazem o `ResourceId` da VM afetada nos parâmetros (vem vazio). O saving por VM/mês é calculado corretamente pelo billing (baseline vs horas faturadas), mas a flag "com automação" por VM depende de um futuro vínculo (ex.: VMs com a tag usada pelo runbook).

## Conceitos

- **Baseline de horas**: número de horas que uma VM estaria ligada 24×7 no mês. Para VMs recém-criadas (< 60 dias), a baseline é proporcional à idade do recurso no mês.
- **Horas economizadas**: `baseline - horas_faturadas`.
- **Saving estimado**: `horas_economizadas × preço_on_demand_hora` (usando `ListUnitPrice` do billing).
- **Custo da exceção**: quanto a empresa "deixa de economizar" ao manter VMs cobertas por uma regra de exceção ligadas 24×7.

## Multi-tenant (4P, TI, etc.)

Você pode criar uma conexão Azure Automation para cada tenant (ex: `azure-automation-4p`, `azure-automation-ti`). Todas elas compartilham a mesma tabela Gold `dest_modelo_automation`, mas cada uma tem seus próprios mappings e dados no Bronze.

Ao criar a conexão, o backend cria automaticamente o destino `MODELO AUTOMATION` e os mappings padrão.

## Snapshot diário de Schedules e Governança

Além dos jobs, você pode importar **snapshots diários dos schedules** (quais VMs estão programadas para desligar) no destino **MODELO AUTOMATION SCHEDULE** (`dest_modelo_automation_schedule`):

```csv
snapshot_date,schedule_name,runbook_name,resource_id,vm,resource_group,subscription_id,enabled,next_run_time,last_job_time,status
```

Com pelo menos 2 snapshots, a aba **Governança** do Desligue Programado compara D vs D-1:

- **REMOVIDO**: VM saiu do schedule.
- **DESABILITADO**: schedule pausado.
- **ADICIONADO**: VM entrou no schedule.
- **REABILITADO**: schedule voltou.
- **Custo desde a saída**: estimativa `dias_fora × 24h × preço_on_demand` da VM.

## Regra Dimon de Saving (seed)

O setup cria automaticamente a regra Dimon `saving_desligue_vm` (SQL custom) com a
**lógica de detecção de desligue**:

- **Passo 1**: agrupa consumo por VM/mês (`ResourceId` + `_billing_period`) filtrando
  `ServiceSubcategory LIKE '%Virtual Machines%'` e `ChargeCategory = 'Usage'`.
- **Passo 2**: calcula a baseline de horas possíveis proporcional à primeira aparição
  da VM no mês (`first_seen` via `ChargePeriodStart`); VMs com histórico < 60 dias são
  marcadas com `is_new_vm = TRUE` (baseline instável).
- Preço por hora = `EffectiveCost / ConsumedQuantity` (proxy on-demand, pois
  `ListUnitPrice` está vazio na Gold atual).

Reprocesse a regra em `/tables?tab=cost-relationship` para materializar
`dest_dm_cost_saving_desligue_vm`. O dashboard `/dimon-dashboard?tab=desligue` pode
consultar essa Dimon ou o endpoint `POST /scheduled-shutdown/savings`.

## Snapshot de tags shutdown=auto (modo Snapshot da Dimon)

A aba **Camada Dimon de Custo** ganhou o **Modo Snapshot**: quando ativo, o reprocess
**não substitui** a tabela — adiciona uma cópia datada (coluna `_snapshot_date`),
preservando o histórico. Assim é possível comparar dias e detectar quem **saiu da automação**.

1. O setup cria a regra `snapshot_tags_automacao` (modo snapshot) com SQL que extrai por
   recurso a flag `has_shutdown_auto` (tag `shutdown=auto` / `Shutdown=auto` / `startStop=auto`).
2. Reprocesse essa regra **diariamente** → cada execução adiciona um snapshot datado.
3. `GET /scheduled-shutdown/tag-delta` compara os 2 snapshots mais recentes:
   - `SAIU_DA_AUTOMACAO`: tinha a tag ontem, não tem hoje → **custo desde a saída**.
   - `ENTROU_NA_AUTOMACAO`: ganhou a tag.
4. No dashboard (aba **Governança**), painel **Delta de tags shutdown=auto**.

> ⚠️ A tag `shutdown=auto` precisa estar presente na coluna `Tags` do billing FOCUS.
> Atualmente **não há** nenhuma VM com essa tag na Gold — verifique se a exportação de
> custo do Azure inclui tags, ou informe o nome exato da tag usada pelos runbooks.

## Fluxo de implementação

### 1. Criar a conexão Azure Automation

1. Vá em `http://localhost:5173/connections`.
2. Aba **Ferramentas Cloud**.
3. Clique em **Nova Conexão** → escolha **Azure Automation**.
4. Preencha `code` (ex: `azure-automation`) e `name`.
5. Salve.

> O backend cria automaticamente o destino `MODELO AUTOMATION` (`dest_modelo_automation`) e os mappings de colunas.

### 2. Enviar o CSV de jobs

1. Vá em `http://localhost:5173/reprocess?tab=extraction`.
2. Selecione a conexão `azure-automation`.
3. Faça upload de um CSV com as colunas:

```csv
JobId,Time,RunbookName,Status,VM,ResourceGroup,SubscriptionId,Action,CorrelationId,Caller,ResourceId
```

- `Time`: data/hora do job (formato ISO ou `YYYY-MM-DD HH:MM:SS`).
- `Action`: `Start` ou `Stop`.
- `ResourceId`: identificador completo da VM no Azure (deve casar com `ResourceId` do billing).

### 3. Relacionar fonte → destino

1. Vá em `http://localhost:5173/tables?tab=relationship`.
2. Selecione a conexão `azure-automation` e o destino `MODELO AUTOMATION`.
3. Arraste as colunas do CSV para as colunas do destino (mappings padrão já devem estar criados).
4. Salve.

### 4. Reprocessar

1. Vá em `http://localhost:5173/reprocess`.
2. Selecione o destino `MODELO FOCUS` e a conexão de billing Azure para atualizar o custo.
3. Selecione o destino `MODELO AUTOMATION` e a conexão `azure-automation` para gerar a Gold de jobs.
4. Execute o reprocessamento.

### 5. Usar a aba Desligue Programado

Acesse `http://localhost:5173/dimon-dashboard?tab=desligue`.

#### Dashboard

- **Saving estimado**: total de saving no período.
- **Horas economizadas**: soma das horas não faturadas.
- **VMs com desligue**: VMs cujo uptime foi menor que 100%.
- **Com automação**: VMs que têm job do Azure Automation no período.
- **Custo das exceções**: soma do saving perdido por regras de exceção.
- **VMs recém-criadas (< 60d)**: VMs com histórico curto (baseline proporcional).

#### Tabela de VMs

Lista VM/mês com:
- VM / ResourceId
- Mês
- Horas faturadas
- Baseline (cheia ou proporcional)
- Uptime %
- Idade (dias)
- Horas economizadas
- Saving
- Exceção e custo da exceção
- Jobs de automação

#### Exceções

Crie regras de exceção com escopo:
- **ResourceId**: VM específica.
- **Subscription**: toda uma subscription.
- **Resource Group**: futuro (não implementado na primeira versão).
- **Tag**: futuro.

A vigência usa `YYYYMM`. O custo da exceção é calculado automaticamente para VMs cobertas.

### 6. (Opcional) Criar uma Dimon customizada via SQL

1. Vá em `http://localhost:5173/tables?tab=cost-relationship`.
2. Crie uma nova regra.
3. Mude para a aba **SQL Custom**.
4. Escreva uma query que cruze `iceberg.gold.dest_modelo_focus` com `iceberg.gold.dest_modelo_automation`.
5. Salve e reprocesse.

Exemplo de query SQL para Dimon de saving por subscription/mês:

```sql
SELECT
  b._billing_period,
  b.SubAccountId AS subscription_id,
  COUNT(DISTINCT b.ResourceId) AS vm_count,
  SUM(COALESCE(TRY_CAST(b.ConsumedQuantity AS DOUBLE), 0)) AS hours_billed,
  SUM(COALESCE(TRY_CAST(b.ListUnitPrice AS DOUBLE), 0) * COALESCE(TRY_CAST(b.ConsumedQuantity AS DOUBLE), 0)) AS saving_brl
FROM iceberg.gold.dest_modelo_focus b
WHERE LOWER(CAST(b.ServiceName AS VARCHAR)) LIKE '%virtual machines%'
  AND (b.ChargeCategory = 'Usage' OR b.ChargeCategory IS NULL)
GROUP BY b._billing_period, b.SubAccountId
```

> A query deve produzir as dimensões e medidas desejadas. A materialização cria `dest_dm_cost_<code>`.

## Regras de negócio implementadas

1. Apenas linhas de `Virtual Machines` no billing entram no cálculo.
2. `ChargeCategory = 'Usage'` (exclui compras de RI/SP).
3. Baseline proporcional para VMs com `age_days < 60`.
4. Saving usa preço on-demand (`ListUnitPrice`) para não ser distorcido por RI/SP.
5. Exceções reduzem o saving e geram o custo da exceção.

## Limitações conhecidas

- O cruzamento com Azure Automation depende do `ResourceId` presente no CSV de jobs.
- Resource Group e Tag ainda não são extraídos do billing para matching de exceção.
- O custo da exceção assume que, sem a exceção, a VM estaria desligada (baseline = 0 uptime).
