# Plano de execução — Azure FinOps Intelligence para VMs

> Instrução para um agente executor em harness com etapas independentes. Este documento é a fonte de ordem, contratos e aceite do projeto. Leia a etapa atual, confirme os caminhos no código, implemente apenas o incremento indicado e registre o resultado antes de avançar.
>
> Revisão do plano original de 75 etapas: coleta e fontes repetidas foram unificadas; o fluxo existente do OpenFinops virou a base da arquitetura; o primeiro resultado útil é uma recomendação de VM no mesmo ambiente, demonstrável de ponta a ponta. O plano também inclui a cadeia de consumo AKS por cluster, namespace e workload, reconciliada ao custo FOCUS. Região, Jev e funcionalidades avançadas entram quando seus dados e restrições existirem.

## 1. Resultado esperado

O sistema deve responder, para uma VM Azure selecionada:

1. Qual foi seu custo real e seu uso no período disponível, começando com 15 dias e crescendo até o limite configurado de 90 dias?
2. Quais opções de configuração ou horário podem reduzir o custo?
3. Quais opções são impossíveis, quais dependem de validação e por quê?
4. Qual é a economia **estimada**, com preço, moeda, período e premissas identificados?
5. Qual evidência sustenta desempenho, risco e confiança?
6. Qual ação é recomendada e qual foi a revisão humana?
7. Para AKS, como o custo e o consumo percorrem cluster → node pool/node → namespace → workload → pod/container → volume e recursos Azure associados?

A versão inicial é **somente leitura**. Ela não faz resize, migração, desligamento nem alteração de policy. Começar por Microsoft Azure Virtual Machines e seus discos, NICs, IPs, rede, zonas, backup e dependências detectáveis. Não implementar AWS, GCP ou OCI neste plano.

### Ordem lógica obrigatória

```text
Fonte Azure / export já configurado
  → Bronze da fonte
  → Silver detalhada, normalizada, enriquecida e com regras pertinentes
  → candidatos + restrições + simulação + risco
  → decisão e histórico
  → Gold (cubos e materializações finais)
  → API + React
  → revisão humana e validação de resultado
```

Código/SQL calcula valores objetivos; o motor de regras veta opções inviáveis; Jev, quando existir, avalia alternativas **já viáveis**. Nunca pedir a Jev/LLM que descubra preço, SKU, Resource ID, p95, quota, política, economia ou disponibilidade. Uma recomendação deve poder ser explicada sem LLM.

## 2. O que existe hoje e o que realmente precisa nascer

Conferido no repositório nesta revisão; a etapa P0 valida também o banco e os dados do ambiente, que não foram inspecionados aqui.

| Capacidade | Situação | Ação |
|---|---|---|
| `SourceConnection`, `code`, `connector_type`, `config_json` | Existe em PostgreSQL; `code` é único | Reusar; acrescentar associação estável a fonte/ambiente sem quebrar códigos |
| `etl_task`, worker, scheduler | Existem | Reusar fila, progresso, agendamento, retry e auditoria; estender tipos só se necessário |
| Iceberg Bronze/Silver/Gold, Trino | Existem | Reusar serviços de escrita/leitura, staging e reprocess; dados volumosos ficam no Iceberg |
| Cost Management 4P/TI | Existe via `azure_blob`/FOCUS | Reusar exports, Bronze, mappings e regras; migrar o FOCUS detalhado atual da Gold para Silver |
| Azure Automation | Existe via `azure_automation_jobs` | Reusar jobs/schedules e Bronze; mover detalhes para Silver e cubos de desligue para Gold |
| Azure Resource Graph | Existe via `azure_resource_graph` | Estender inventário existente para perfil VM/relações; não recriar o conector |
| AKS Cost Analysis / OpenCost | Não foi encontrado coletor no projeto | Adicionar como capacidade selecionável quando o add-on/contrato permitir; reconciliar sua alocação ao FOCUS |
| Topologia lógica AKS | ARG enxerga recursos ARM, mas não fornece sozinho todo namespace/pod/container | Criar coleta própria via fonte suportada (Azure Monitor/Container Insights, API Kubernetes ou OpenCost), com capability discovery |
| Azure OpenAI PTU | Existe com pipeline operacional próprio | Preservar; pode aparecer no grupo Azure, mas não depende do MVP VM |
| Business Rules, Classification, Clone Engine | Existem para dados financeiros | Reusar onde se aplicam; não aplicar markup/clone a métricas operacionais |
| Forecast | Existe para projeção de custo | Manter separado; não usá-lo como simulador de desempenho de VM |
| Dimon legado | Ainda há namespace, código, endpoints e documentos no repositório | Remover após migrar suas regras/materializações para cubos Gold; nenhum componente novo pode depender de Dimon |
| Dashboards e UI de Connections | Existem | Estender páginas/padrão visual; não abrir aplicação separada |
| Série de métricas VM, catálogos de preço/SKU/quota, motor de candidatos | Não encontrados na aplicação principal | Implementar incrementalmente |
| Motor Jev de otimização de VM | Não encontrado na aplicação principal | Tratar como integração futura isolada; o HTML congelado `FinOps_v2.5.3_*` não é serviço de produção |

Leituras de entrada: `docs/ai/AGENTS.md`, `00-overview.md`, `01-architecture.md`, `02-data-model.md`, `03-business-rules.md`, `10-finos-intelligence.md`, `desligue-programado.md`, `11-azure-resource-graph-recursos-orfaos.md` e `08-ui-design-reference.md`. `10-finos-intelligence.md` e demais referências a Dimon são documentação legada usada apenas para inventariar funcionalidades que precisam migrar ou ser removidas. Pontos de código: `apps/api/app/models/source_connection.py`, `apps/api/app/routers/connections.py`, `apps/api/app/routers/etl.py`, `apps/api/worker.py`, `apps/api/app/services/iceberg_service.py`, `apps/api/app/services/task_runner.py`, serviços de materialização legados, serviços Azure existentes e `apps/web/src/pages/Connections/Connections.tsx`.

### Decisões arquiteturais para evitar retrabalho

- **Uma experiência de configuração Azure por ambiente, com fontes selecionáveis.** Pode haver registros internos `SourceConnection` por fonte, pois o pipeline atual vincula Bronze a `code`; a UI os agrupa. Não exigir que o usuário configure dezenas de cards e secrets repetidos.
- **Uma Bronze por fonte lógica e ambiente para novas coletas.** Automation mantém jobs/schedules em fatias da Bronze atual; ARG mantém inventário/órfãos na sua Bronze; PTU preserva suas tabelas especiais. Não migrar histórico apenas para uniformizar nomes.
- **Silver é a camada detalhada e confiável.** Recebe FOCUS, inventário, séries, topologia, preços, perfis, candidatos e decisões detalhadas já tipadas/enriquecidas. Mappings e regras de negócio entram no processamento Bronze → Silver. Preservar a ordem atual de mapping/regras/classificação/clone durante a migração e provar compatibilidade por totais.
- **Gold é a camada final de consumo.** Todos os cubos e materializações de dashboard vivem no namespace `gold`. Gold agrega a Silver por dimensões e medidas; não recebe payload bruto nem série de 15 minutos. Não criar ou manter uma quarta camada chamada Dimon.
- **FOCUS Silver continua sendo o livro financeiro detalhado.** Namespace/workload é uma alocação derivada. Não acrescentar colunas de namespace diretamente ao FOCUS quando uma linha puder alimentar vários consumidores. Usar ponte com pesos, versão e reconciliação; publicar o cubo enriquecido na Gold.
- **Topologia é temporal.** Relacionamentos atuais do ARG/AKS não podem ser aplicados retroativamente a custos antigos sem snapshot válido para o período. Cada nó e aresta carrega `valid_from`, `valid_to` ou `snapshot_at`.
- **PostgreSQL guarda configuração e controle.** Credenciais, seleção, checkpoints, permissões, job, revisão e auditoria ficam em metadata; inventário, séries, preços e fatos volumosos ficam em Iceberg.
- **Read-only e evidência primeiro.** Um dado desconhecido não vira zero nem `VALID`. Análise pode continuar com estado `REQUIRES_VALIDATION` e confiança reduzida.

## 3. Mapa das 75 etapas originais: manter, fundir, adiar

Este mapa é para o harness não voltar a quebrar o trabalho por cada título antigo.

| Etapas antigas | Decisão | Motivo e localização neste plano |
|---|---|---|
| Refatoração Bronze/Silver/Gold e remoção da Dimon | **Executar primeiro** em P1 | Dados detalhados ficam na Silver; cubos finais ficam na Gold; namespace/API/docs Dimon deixam de existir |
| 0–3: arquitetura, onboarding, segurança, registry | **Fundir** em P0/P2 | Onboarding deve estender `SourceConnection` e teste de capacidade; não criar plataforma de conexão paralela |
| 4–6: Cost Management, Price Sheet, Retail | **Separar reuso e extensão** em P3/P7 | Cost Management FOCUS existe. Só Price Sheet/Retail são fontes novas; preço contratado pode ser indisponível |
| 7–11: inventário, VM, SKU, quota, policy | **Fundir por dependência** em P3/P7 | ARG já coleta inventário; SKU/quota/policy são verificações de candidato, não cinco sistemas independentes |
| Topologia AKS por namespace (novo requisito) | **Adicionar** em P4/P10 | FOCUS fornece total financeiro; OpenCost/AKS/Container Insights fornece divisão e relações. A ponte evita duplicação |
| 12–21: Monitor, 90 dias, backfill, qualidade, features | **Fundir** em P5/P6 | Uma série temporal versionada alimenta features; não criar tabelas/engines para cada métrica |
| 22–26: custo/perfil/workload/grafo/Advisor | **Escalonar** em P3/P6/P11 | Perfil de VM vem cedo; grafo profundo e Advisor só para região/ações avançadas |
| 27–47: geração, SKU, região, restrições, replay, TCO, risco | **Dividir por resultado** em P8/P11 | Primeiro candidatos no mesmo local; região exige dados adicionais. Um pipeline de candidato com estratégias, não um engine por estratégia |
| 48–58: Jev, Next Best Action, confiança, auditoria, revisão | **Fundir** em P9/P12 | Decisão determinística completa antes da integração Jev; configuração do provider fica na aba Integrações |
| 59–68: Gold e dez telas/painéis | **Reduzir** em P10 | Um resumo e um detalhe de VM primeiro; expandir por uso real, sem dez dashboards vazios |
| 69–74: cron, hash, Iceberg, retenção, feedback | **Distribuir** entre P5/P13 | Idempotência/partição entram com cada fonte; operação e resultado entram após o fluxo end-to-end |
| 75: 22 PRs | **Substituir** por P0–P13 | Entregas pequenas com entrada/saída/aceite explícitos, organizadas pelo grafo de dependências |

**Fora do MVP:** Spot, ARM, mudança automática de região, x86→ARM, Advisor/Resource Mover e seleção por Jev. São extensões possíveis, condicionadas a suporte real, dados e política; não bloquear P10 por elas. Mudança Intel→AMD entra como candidato exploratório após catálogo/preço, mas só vira recomendação acionável quando compatibilidade e desempenho tiverem evidência suficiente.

## 4. Contrato da fonte e dos códigos

### Experiência de seleção

Em `Connections → Ferramentas Cloud → Azure`, mostrar um grupo por ambiente/tenant/perfil de credencial. O operador escolhe subscriptions e liga/desliga coletas. Para cada fonte: estado `NOT_CONFIGURED`, `AVAILABLE`, `PARTIAL`, `UNAVAILABLE` ou `ERROR`, último sucesso/falha, permissão faltante, linhas, janela, code, Bronze e ação coletar/reprocessar. Testar capacidade por API sem tentar executar coleta completa. Erro de uma fonte não impede as independentes.

Para fontes de telemetria, a própria conexão configura:

- `initial_backfill_days`: **15** no piloto;
- `analysis_window_days`: **15** no piloto, configurável até **90**;
- `retention_days`: **15** no piloto, elevado para **90** após validar tempo, volume e custo;
- `collection_schedule`: frequência incremental;
- `chunk_days`: tamanho das chamadas de backfill;
- `metric_granularity_minutes`: inicialmente 15 quando a métrica suportar.

Depois da validação do piloto, alterar `analysis_window_days` e `retention_days` para 90. Se a API não devolver todo o histórico, preservar o que foi coletado e acumular execuções até atingir a janela. O perfil usa `observed_days = min(dias disponíveis, analysis_window_days)` e mostra esse valor; menos de 90 dias reduz confiança, mas não impede aplicar regras determinísticas solicitadas pelo usuário. Ao atingir a retenção configurada, um job idempotente poda Bronze/Silver de telemetria de alta resolução anterior ao corte somente depois de materializar os perfis/cubos dependentes; metadata da execução e contagens permanecem auditáveis. O limite operacional desta iniciativa é 90 dias. Não apagar FOCUS financeiro, cubos históricos compactos, auditoria ou decisões por essa política.

### Aba Integrações

Adicionar em **Connections** uma aba de primeiro nível chamada **Integrações**, separada de provedores cloud, ferramentas de coleta e marketplace. Integração é um serviço auxiliar que recebe decisões ou participa do workflow; não é uma `SourceConnection` e não cria Bronze por padrão.

Criar metadata própria, conceitualmente `system_integration`: `code`, `name`, `integration_type`, `enabled`, `config_json` sanitizado, referência segura a credencial, timeout, retry, health/status, último teste e auditoria. A API nunca devolve segredo. A UI permite criar, editar, ativar/desativar, testar e consultar o último erro.

Integrações iniciais do catálogo:

- **Jev:** endpoint/base URL, autenticação, provider/model/version, timeout, retries, política de fallback, escopo de dados enviado e flags de habilitação. P12 deve consumir esta configuração; nada fica hardcoded em serviço ou `.env` específico de tenant.
- **ServiceNow:** item futuro do catálogo, preparado para instance URL, OAuth/credential reference, tabela/tipo de registro, assignment group e mapeamento de estados. Não implementar criação de ticket até uma fase autorizá-la; o cadastro/teste pode existir sem envio.

Persistir logs técnicos e auditoria de chamada sem prompt, token ou segredo. Se futuramente uma integração também ingerir dados, registrar uma fonte lógica específica em vez de misturar essa função à configuração de saída.

`source_key` identifica a semântica da fonte; `connection.code` identifica a origem no pipeline; `collection_run` identifica uma execução; `_blob_path` ou chave equivalente identifica uma fatia idempotente. Não usar apenas prefixo textual `azure-` para decidir que uma linha é custo FOCUS. Resolver por metadata ou lista de fontes de custo.

Convenção **somente para novas fontes**: `azure-{environment_slug}-{dataset_slug}`, minúsculo ASCII, até 50 caracteres e sem colisão após a sanitização física de `iceberg_service._bronze_table_id` (hífens/pontos viram `_`). O slug do ambiente é estável. `code` não muda quando o nome visível muda. Uma fonte por tenant/ambiente recebe sua própria Bronze; catálogos globais só podem ser compartilhados se escopo, vigência e proveniência forem demonstrados.

| `source_key` | Existe? | Code 4P/contrato | Destino lógico |
|---|---|---|---|
| `cost_exports` | Sim | `azure-4p` legado; confirmar TI no banco | Bronze atual → Silver FOCUS detalhada → cubos Gold |
| `automation_jobs` | Sim | code da conexão atual | Bronze atual, fatias `automation/jobs/` e `automation/schedules/` |
| `resource_graph` | Sim | code da conexão atual | Bronze atual, fatias `arg/inventory/` e `arg/orphans/` |
| `openai_ptu` | Sim | `azure-4p-ptu` quando semeado | Fluxo PTU especial; fora do MVP |
| `aks_inventory` | Novo | `azure-4p-aks-inventory` | Bronze exclusiva → Silver entidades/arestas Kubernetes |
| `aks_cost_allocation` | Novo, condicionado ao add-on/oferta | `azure-4p-aks-cost-allocation` | Bronze exclusiva → Silver alocação diária reconciliável |
| `container_insights` | Novo, se necessário para consumo runtime | `azure-4p-container-insights` | Bronze exclusiva → Silver métricas de node/pod/container |
| `monitor_vm_metrics` | Novo | `azure-4p-monitor-vm-metrics` | Bronze exclusiva → Silver série 15 min |
| `compute_skus` | Novo | `azure-4p-compute-skus` | Bronze exclusiva → Silver SKU por região |
| `compute_quota` | Novo | `azure-4p-compute-quota` | Bronze exclusiva → Silver quota por subscription/região |
| `customer_price` | Novo, se permitido | `azure-4p-customer-price` | Bronze exclusiva → Silver preço contratado |
| `retail_price` | Novo | `azure-4p-retail-price` | Bronze exclusiva → Silver preço público |
| `region_policy` | Novo, necessário em P11 | `azure-4p-region-policy` | Bronze exclusiva → Silver restrições |
| `advisor`, `resource_mover` | Opcionais | Só criar ao habilitar | Bronze própria se houver valor comprovado |

Para TI, trocar o slug `4p` pelo slug real do ambiente, mantendo o `source_key`. Os códigos de Automation/ARG podem não seguir o padrão novo: **vincular, não renomear**. PTU grava Bronze especializada, inclusive `raw_azure_ptu_metrics`; não forçar `raw_{code}` retroativamente.

### Contrato de cada coletor novo

Definir em registro único: `source_key`, versão de schema, escopo Azure, perfil de credencial, permissões, API, paginação, limites, política de 429/5xx, janela, checkpoint, chave natural, frequência, forma snapshot/upsert, `code`, nome Bronze, transformação Silver e consumidores. Processar pela fila `etl_task`/worker existente; registrar início, contagem, erro e evidência física. Reexecução de mesma fatia substitui/upserta aquela fatia sem duplicar outras. Só marcar `COMPLETED` após escrita validada. Não copiar client secret em cada fonte; a resolução de perfil precisa existir de forma segura antes de criar registros filhos.

`aks_inventory`, `aks_cost_allocation` e `container_insights` aparecem como opções dentro do mesmo grupo Azure, mas são fontes lógicas diferentes e geram Bronzes separadas. O teste de capacidade deve descobrir: clusters AKS, tier, cost analysis habilitado, oferta EA/MCA compatível, Container Insights/workspace, acesso ao Kubernetes API quando adotado e granularidade realmente retornada. A visualização do portal Azure não prova que o mesmo detalhe está disponível no export/API da assinatura; P4 deve executar um spike com payload real e registrar o contrato antes de fixar schema.

### Compatibilidade e migração

1. Inventariar no banco `SourceConnection.code`, tabelas Bronze/Silver/Gold e namespace Dimon legado, mappings, rules, `IngestedBlob`, `etl_task` e schedules reais. Código do repositório indica capacidade, não prova dado coletado.
2. Inventariar igualdade literal e prefixos dos codes em `cloud_origem`, queries, seeds, materializações legadas, dashboard e filtros. Há referências explícitas a `azure-4p`/`azure-ti` e ao namespace Dimon no código atual.
3. Criar associação `source_key` ↔ conexão legada sem alterar code, Bronze ou histórico. Seed/migração idempotente e sem duplicar mappings/regras.
4. Novos codes passam pelo validador de unicidade lógica e física; gerar metadata antes da primeira coleta.
5. Onde a query hoje filtra custos por code, preservar os códigos FOCUS exatos ou usar metadata de fonte de custo. Não incluir métricas no cubo FOCUS por acidente.
6. Comparar linhas/somas FOCUS e cubos Gold migrados, Automation, ARG e PTU antes/depois; rollback de metadata não apaga dados.

## 5. Contratos de dados por camada

### Bronze: origem e auditabilidade

Guardar payload original e proveniência. No serviço genérico, `write_bronze` cria `bronze.raw_{code_sanitizado}`, adiciona `_billing_period`, `_blob_path`, `_ingested_at` e armazena campos de origem como texto. Respeitar esse comportamento; tipos vêm na Silver. Para API sem blob, `_blob_path` é uma chave lógica estável por fonte/subscription/janela/snapshot. Guardar `native_resource_id`, tenant/subscription, `source_key`, versão e instante da coleta sem perder payload. Partitioning por período/tempo e dimensionamento de arquivo; não particionar por VM. Monitorar small files.

### Silver: limpeza, tipos e semântica

Uma família de tabelas por dataset. Não misturar custos, métricas e catálogos. Padronizar UTC, unidade, moeda, tipos, chaves, deduplicação e estado de qualidade. `canonical_resource_id` é forma estável do `native_resource_id` (trim, lowercase, barras de forma consistente); nunca casar nomes iguais de VMs em subscriptions diferentes. Campo desconhecido permanece `NULL` com `quality_status`, jamais vira zero. `source_connection_id`, `code`, `source_key`, `snapshot_at` e período seguem até os derivados.

O destino FOCUS detalhado que hoje vive na Gold deve passar a viver na Silver. Mappings, classificação, clone e regras de negócio continuam produzindo o mesmo resultado financeiro, agora no namespace correto. Não duplicar markup na Silver ou no cálculo de saving. Para métricas/SKUs/preços, usar transformações próprias e aplicar regra de negócio somente quando fizer sentido. Perfis, candidatos e pontes detalhadas também permanecem na Silver; Gold recebe apenas materializações finais.

### Topologia de consumo e ponte FOCUS

A visão semelhante à supply chain do Turbonomic exige combinar fontes, porque nenhuma delas descreve sozinha custo faturado, runtime Kubernetes e infraestrutura Azure:

```text
Tenant → Subscription → Resource Group → AKS Cluster
  → Agent Pool → VMSS → Node/VM → Disk/NIC/Load Balancer
  → Namespace → Workload Controller → Pod → Container
  → Service/Ingress e PVC/PV → Managed Disk
```

- **ARG/ARM:** cluster, agent pool quando exposto, VMSS/VM, disco, NIC, IP, load balancer e IDs Azure.
- **AKS/Kubernetes ou Container Insights:** namespace, Deployment/StatefulSet/DaemonSet/Job, pod, container, service, node, PVC/PV, requests, limits e consumo. ARG não substitui essa fonte lógica.
- **AKS Cost Analysis/OpenCost:** pesos ou custos alocados por cluster/namespace/asset, incluindo classes explícitas `WORKLOAD`, `IDLE`, `SYSTEM`, `SERVICE` e `UNALLOCATED`, conforme dado disponível.
- **FOCUS/Cost exports:** valor financeiro reconciliado e regras atuais de moeda, impostos, markup, classificação e período.

Persistir duas estruturas Silver: `azure_consumption_entity_snapshot` (nós) e `azure_consumption_edge_snapshot` (arestas), ambas com `entity_key`, tipo, `native_id`, cluster, namespace quando aplicável, snapshot/vigência, fonte e confiança. Entidades efêmeras como pods não podem usar somente nome; usar cluster UID + Kubernetes UID quando disponível. Arestas carregam `relationship_type` (`CONTAINS`, `RUNS_ON`, `OWNS`, `USES`, `BACKED_BY`, `ROUTES_TO`, `ALLOCATED_TO`) e evidência.

Persistir também uma ponte de alocação, conceitualmente `azure_focus_consumption_allocation`, no grão mínimo confiável (normalmente dia × cluster × namespace/workload × categoria de ativo). Campos mínimos: chave do grupo de custo FOCUS, entidade consumidora, janela, `allocation_weight`, `allocated_billed_cost`, `allocated_effective_cost`, `allocated_realized_cost`, moeda, classe de alocação, método, fonte, versão e confiança. A chave financeira deve derivar de proveniência estável (`source_connection_id`, período/data, Resource ID/meter e identificador imutável existente); nunca usar número da linha de um DataFrame.

Não sobrescrever nem explodir a tabela FOCUS detalhada da Silver. Persistir a ponte detalhada também na Silver e materializar em Gold o cubo `gold.azure_consumption_relationship` para consulta/drilldown. Uma linha/grupo FOCUS pode virar várias alocações apenas se os pesos do grupo somarem 1. Se a origem fornecer somente consumo, usar o consumo como peso e calcular o custo alocado a partir do total FOCUS. Se fornecer custo reconciliado, comparar antes de persistir. Custos sem relacionamento permanecem `UNALLOCATED`; capacidade ociosa e sistema permanecem visíveis, sem redistribuição silenciosa.

Invariantes por período, cluster, recurso e moeda:

```text
SUM(allocated workload + idle + system + service + unallocated)
  = custo FOCUS elegível, dentro da tolerância configurada

SUM(allocation_weight por grupo financeiro) = 1

custo FOCUS global antes = custo FOCUS global depois
```

Bloquear publicação da materialização se houver duplicação, peso negativo, soma acima da tolerância, moeda misturada ou relacionamento fora da vigência. Registrar diferença, cobertura de alocação e causa. O drilldown pode mostrar custo → recurso Azure → node → namespace → workload, e também o caminho inverso, mas não deve afirmar causalidade quando a ligação for apenas inferida.

### Gold, decisão e cubos finais

A Gold é o último nível do lakehouse e contém somente cubos/materializações prontos para API, dashboard e exportação. A fonte oficial detalhada de custo realizado é o FOCUS da Silver. Tabelas intermediárias sugeridas ficam na Silver: `silver.azure_vm_profile`, `silver.azure_optimization_candidate`, `silver.azure_optimization_decision_detail` e `silver.azure_focus_consumption_allocation`. PostgreSQL guarda definição de estratégia, estado de revisão, job e referência a versões; Iceberg guarda fatos e snapshots. Nunca atualizar uma decisão antiga apagando premissas; criar nova versão/run.

Cubos Gold sugeridos, sujeitos aos padrões confirmados em P0/P1:

- `gold.cost_cube`: substitui as pré-agregações gerais legadas;
- `gold.azure_optimization_cube`: custo atual, saving estimado/validado, ação, risco, confiança e status;
- `gold.azure_consumption_relationship`: cluster/namespace/workload/asset e classes `WORKLOAD/IDLE/SYSTEM/SERVICE/UNALLOCATED`;
- `gold.azure_decision_history_cube`: histórico resumido de recomendações e revisão;
- cubos existentes de PTU, Automation, órfãos e consumo de IA migrados para Gold quando ainda dependerem do namespace legado.

Não usar `realizado` para saving potencial; esse campo conserva sua semântica financeira. Cubos Gold devem declarar dimensões, medidas, fonte Silver, janela e versão. Dashboard consulta Gold para resumo e Silver apenas para detalhe paginado/linha de evidência. A arquitetura alvo não possui namespace, tabela, endpoint, modelo ou documentação Dimon.

## 6. Modelo analítico mínimo

O agente pode adaptar nomes físicos, mas deve preservar estas entidades e chaves:

| Entidade | Grão e chaves | Campos que não podem faltar |
|---|---|---|
| Inventário VM | 1 VM por `snapshot_at` e ambiente | IDs nativo/canônico, SKU, região, zona, tags, SO, estado, fonte, qualidade |
| Relações | 1 aresta VM→recurso por snapshot | IDs, tipo, origem da evidência, `KNOWN/UNKNOWN` |
| Entidade de consumo | 1 entidade Azure/Kubernetes por snapshot/vigência | `entity_key`, tipo, UID/Resource ID, cluster, namespace, labels, fonte, confiança |
| Aresta de consumo | 1 relação provider→consumer por snapshot/vigência | chaves origem/destino, `relationship_type`, método, evidência e confiança |
| Alocação FOCUS | 1 grupo financeiro × entidade × janela × categoria | peso, custos alocados por medida, moeda, classe, método, cobertura e versão |
| Série de métrica | 1 Resource ID × métrica/dimensão × bucket UTC | avg/min/max/total/count quando disponíveis, unidade, origem, instante, cobertura |
| Preço | 1 escopo × SKU/meter × região × vigência × tipo | moeda, unidade, price source, billing profile/termo quando houver, qualidade |
| Perfil VM por janela | 1 VM × `window_start/window_end` × versão | dias solicitados/observados, custo, horas, percentis, gaps, SKU/capacidade, dependências, hash |
| Candidato | 1 perfil × estratégia × configuração alvo | snapshot DE/PARA, status, vetos, premissas, série projetada, custo atual/alvo, saving bruto/líquido |
| Decisão | 1 conjunto de candidatos × versão | ação, justificativa, risco, confianças separadas, provider, revisão, timestamps |

Um Resource ID é VM, **não workload**. Workload de VM é agrupamento configurável por tag/projeto/resource group/dependência com proveniência e confiança. Em AKS, `workload_controller` representa um objeto real como Deployment ou StatefulSet e deve usar UID/tipo/namespace/cluster. Não inventar relação aplicação→VM ou namespace→centro de custo a partir de coincidência de nome.

### Contrato de custo e preço

- Custo observado: agregado FOCUS por Resource ID canônico e intervalo, mantendo billed/effective/realizado, moeda original e regras de conversão. Selecionar uma medida para comparação e documentá-la; não somar billed + effective nem custo compartilhado duas vezes.
- Preço projetado: priorizar Price Sheet contratual **se disponível e aplicável**; depois custo efetivo observado **somente se comparável à configuração candidata**; retail é fallback indicativo. Registrar `price_source`, vigência, moeda, unidade, regra de câmbio/tributos e confiança. Se não existe preço comparável de destino, não inventar saving exato.
- Normalizar custo atual e candidato para a **mesma janela, moeda e unidade de uso**. Usar horas reais/calendário da janela; não fixar 730 horas para todos os meses. Separar compute de disco, licença, rede, backup e compromisso. Não supor que mudar SKU reduz todo o custo da VM.
- Poupança líquida regional inclui, quando quantificáveis, migração, egress, diferenças de licenciamento/compromisso e operação. Se parcelas relevantes forem desconhecidas, status `REQUIRES_VALIDATION`.

### Contrato de métricas e qualidade

Azure Monitor é a fonte inicial. O piloto solicita e retém 15 dias. Depois de medir duração, volume, throttling e custo, a configuração sobe progressivamente até o teto de 90 dias. A retenção oficial de métricas de plataforma é 93 dias e a API batch permite até 50 Resource IDs na mesma região/subscription; validar suporte por métrica/escopo antes de decidir batch. Uma janela de 15 dias a 15 min tem até 1.440 buckets por VM; em 90 dias, até 8.640. São máximos, não garantia de amostras. Chunks menores, checkpoint, paginação, limites e backoff são obrigatórios. Algumas métricas guest dependem do Azure Monitor Agent/Data Collection Rules; memória ausente é `NOT_AVAILABLE`, não estimativa.

Métricas iniciais: CPU (`Percentage CPU`), memória guest quando disponível, IOPS/throughput/latência de disco quando disponíveis e network in/out. Descobrir nomes/unidades/agregações da API em vez de codificar uma lista assumida para todo SKU. Manter dimensão de série relevante para não somar incorretamente discos/NICs. Calcular cobertura por métrica e janela: buckets esperados dentro do intervalo observável, coletados, lacunas, maior lacuna, primeiro/último ponto. Guardar motivo da ausência (sem agente, sem permissão, sem retenção, erro, VM desligada quando distinguível). Percentis calculados da série normalizada, nunca média de percentis diários.

Features: avg, p50/p95/p99/max de CPU e memória; memória usada absoluta e headroom; IOPS/throughput/rede p95/p99; fração acima de limiares; picos recorrentes; comportamento business/night/weekend. Calcular com todos os dados disponíveis dentro de `analysis_window_days`, registrar `requested_days`, `observed_days` e cobertura, e nunca fingir uma janela de 90 dias. Detectar fusos locais por configuração do workload, preservando timestamps UTC. Features sem cobertura mínima configurada podem participar de regras autorizadas, mas recebem confiança menor e motivo visível.

## 7. Motor de decisão: um pipeline, estratégias pequenas

### 7.1 Geração de candidatos

Começar com mesma região e VM ligada de forma estável: tamanhos menores da mesma família, depois família alternativa com capacidade equivalente. Cada estratégia implementa `propose(profile, catalog) → candidates`, sem montar um engine separado. Scheduling usa padrão temporal e evidência de Automation já coletada; não presumir que VM sem tráfego pode ser desligada. AMD/geração/ARM/Spot são variantes que exigem compatibilidade demonstrada, não substituições livres.

Para região, gerar apenas regiões candidatas permitidas pela organização e serviços dependentes. Advisor pode oferecer hipóteses, não é fonte de verdade. Mover pode informar viabilidade, mas ausência dele não equivale a migração viável.

### 7.2 Restrições antes da simulação/decisão

Estados `VALID`, `INVALID`, `REQUIRES_VALIDATION`. Guardas: RAM, CPU/capacidade, disco IOPS/throughput, NIC/rede, SKU/zona disponível à subscription, quota, policy, arquitetura/SO/licença, compromisso e dependências. `INVALID` não chega a Jev nem a Next Best Action. `REQUIRES_VALIDATION` pode aparecer como oportunidade exploratória com motivos, mas não como recomendação pronta para execução. Não converter falta de quota/policy em “aprovado”.

### 7.3 Replay de desempenho

Reaplicar a série de 15 min ao candidato quando o modelo for defensável. Para capacidade proporcional de mesma família/geração, estimar utilização e headroom por bucket e registrar coeficiente e versão. Comparações entre gerações/vendors são aproximações; exigir validação ou benchmark do cliente antes de afirmar risco baixo. Memória usada em GB, IOPS e throughput devem caber nos limites do candidato com folga definida. p95 isolado não prova ausência de picos: considerar p99, máximo, duração e cobertura. Quando não há métrica crítica, simulação deve devolver `INCOMPLETE`.

### 7.4 Finanças, risco e ranking

Simular custo por componente, economia mensal/anual, custo de migração, payback e sensibilidade a preço/câmbio/uso. Separar `observed_cost` de `projected_cost` e `potential_saving` de `realized_saving`. Fatos de risco objetivos incluem cobertura, burst, headroom, disponibilidade, dependências, policy, quota, complexidade operacional e retorno. Um ranking determinístico pode escolher a melhor ação entre candidatos `VALID`, sob política de risco/benefício explícita. `profile_hash` inclui versões de custo, métricas, SKU, preço, inventário e política; reexecutar só quando entradas materiais mudarem.

### 7.5 Jev, Next Best Action e revisão

Jev é um `DecisionProvider` **opcional e isolado**. Recebe candidatos válidos, fatos, scores e política; responde avaliação/probabilidade com versão do modelo. Código revalida todos os vetos e registra entrada/saída resumida. Se Jev falhar, o ranking determinístico permanece disponível e a decisão registra provider/fallback; não preencher probabilidade fictícia. `data_confidence`, `simulation_confidence` e `decision_confidence` são campos distintos.

Next Best Action mostra uma ação principal por VM/workload e alternativas, com economia, preço de origem, impacto, risco, esforço, restrições e motivo. Toda sugestão persiste dois snapshots comparáveis:

- **DE:** SKU/configuração, capacidade, preço/custo, métricas e janela realmente observados;
- **PARA:** SKU/configuração proposta, capacidade, preço projetado, métricas recalculadas pelo replay, saving e premissas.

Ao clicar no objeto recomendado, abrir um detalhe com tabela DE/PARA e gráfico na mesma escala temporal. O gráfico sobrepõe a série observada e a projeção pós-alteração por bucket para CPU, memória, disco, rede e custo quando cada medida existir. Projeção usa traço/legenda distinta e nunca é apresentada como dado real. Mostrar `observed_days`, cobertura e lacunas. Depois que a mudança for confirmada como implementada, acrescentar a série real pós-mudança sem apagar a previsão original.

Estados de revisão: `IDENTIFIED → RECOMMENDED → UNDER_REVIEW → APPROVED/REJECTED → IMPLEMENTED → VALIDATING → REALIZED`. Registrar reviewer, instante, motivo, override e versão; nunca alterar recursos Azure ao mudar estado. `IMPLEMENTED` depende de confirmação humana/integração futura, não de clique em “aprovar”.

## 8. Produto mínimo e expansão

### MVP demonstrável

Para um ambiente Azure, entregar:

- seleção agrupada das fontes e status por fonte;
- inventário de VMs + custo FOCUS conciliado;
- para subscriptions com AKS e dados disponíveis, cadeia cluster/node/namespace/workload e alocação FOCUS reconciliada, com `IDLE/SYSTEM/UNALLOCATED` explícitos;
- carga inicial de 15 dias de telemetria, janela/retention configuráveis até 90 dias, qualidade e preço de destino rastreável;
- catálogo de SKU, quota/policy suficientes para mesmo local;
- candidato simples de rightsizing no mesmo local, com veto e economia indicativa/contratada claramente rotulada;
- detalhe da VM com linha de evidência e revisão humana;
- cubos Gold sem consulta a milhões de linhas FOCUS detalhadas a cada abertura;
- detalhe clicável DE/PARA com série observada, projeção e quantidade de dias disponível.

Este MVP pode gerar “dados insuficientes” em vez de recomendar quando memória, preço ou restrição crítica faltarem. Isso é resultado correto. Não prometer cobertura total no primeiro deploy.

### Expansões após MVP

1. Scheduling com padrão temporal e dados Automation, distinguindo automação existente de nova oportunidade.
2. Outras famílias/AMD quando desempenho e licença puderem ser validados.
3. Mudança de região com grafo, política, latência/residência, TCO e migração.
4. Jev sobre candidatos válidos e calibração de decisão.
5. Feedback antes/depois: saving previsto versus realizado, desempenho, incidentes, rollback e overrides.

Na UI, começar por **um resumo de oportunidades**, **um detalhe DE/PARA da VM** e, quando houver AKS, **uma exploração de consumo** reutilizável. A exploração deve alternar árvore e tabela, navegar nos dois sentidos e exibir custo, consumo, requests/limits, ociosidade, fonte e cobertura em cada nível. Comparador de candidatos, série observada/projetada, estatísticas e what-if são seções do detalhe. Não criar páginas vazias para cada etapa de engenharia.

## 9. Roteiro para o harness

### Regra de execução de cada pacote

O harness executa **P0, P1, …, P13** na ordem. Um pacote só termina quando sua saída e aceite existem. Antes de codificar, ler código e documentação dos pontos de integração; listar em até uma página `entrada → arquivos tocados → saída → testes → risco`. Após codificar, registrar arquivos, migração, teste, resultado e pendências em `docs/ai/azure-finops-execution-log.md` (criar no P0). Não copiar o plano inteiro para esse log. Se o repositório contradisser uma premissa, registrar evidência e ajustar o contrato afetado; não improvisar uma arquitetura paralela. Mudanças do usuário no workspace devem ser preservadas.

Ao concluir **cada pacote**, é obrigatório versionar e subir o resultado no Git:

1. Confirmar critérios de aceite e testes do pacote.
2. Revisar `git status`/diff e incluir somente arquivos pertencentes ao pacote; nunca incluir `.env`, secrets, credenciais, dados locais ou alterações não relacionadas do usuário.
3. Atualizar o execution log com testes e limitações.
4. Criar commit identificável, por exemplo `feat(P5): collect Azure Monitor pilot window`.
5. Fazer push para `origin` na branch de trabalho atual, sem `--force` e sem merge automático na branch principal.
6. Registrar branch e SHA no execution log. Se commit ou push falhar, o pacote permanece incompleto e o agente relata o erro; não avança silenciosamente.

**P0 — Auditoria e baseline (sem código funcional)**

- Entrada: este documento e repositório/banco acessível.
- Fazer: mapear conexões reais, códigos, Bronze/Silver/Gold/namespace Dimon legado, permissões sem exibir secrets, docs versus código, custo/contagens por período fechado, queries que filtram codes, engines reutilizáveis e gaps. Verificar se o ambiente realmente contém 4P/TI/Automation/ARG/PTU e clusters AKS; para AKS, verificar tier, add-on Cost Analysis, Container Insights/workspace e campos reais disponíveis em export/API. Inventariar toda referência Dimon em código, rotas, tabelas, jobs, frontend, README e `docs/ai`.
- Saída: matriz `existe/reusar/estender/criar`, grafo de dependência dos pacotes, baseline reproduzível de linhas/totais e ADR curto de source grouping/códigos.
- Aceite: cada afirmação sobre “já existe” tem caminho ou query de evidência; nenhum código funcional alterado.

**P1 — Finalizar Bronze/Silver/Gold e remover Dimon**

- Entrada: inventário e baseline P0; refatoração já iniciada pelo usuário.
- Fazer: mover destinos detalhados atuais da Gold para Silver; transformar materializações/cubos Dimon em Gold; adaptar writers, readers, SQL Trino, jobs, APIs e frontend; migrar dados com staging/swap e checksums; remover namespace/tabelas Dimon somente após o cutover; remover modelos, serviços, rotas, nomes de tasks, scripts e dependências Dimon sem uso. Atualizar README e toda documentação em `docs/ai` para Bronze → Silver → Gold, removendo instruções legadas.
- Saída: arquitetura com três camadas; Silver detalhada; Gold formada apenas por cubos finais; nenhum consumidor ativo consulta Dimon.
- Aceite: buscas no código executável e na documentação ativa não encontram dependência de `dimon`; apenas este plano/log/artefatos de migração podem citar o nome legado; totais/linhas e endpoints equivalentes batem com o baseline; rollback foi testado antes de excluir o legado; cubos Gold reprocessam pelo worker.

**P2 — Catálogo de fontes Azure e seleção agrupada**

- Entrada: P1 e `SourceConnection` atual.
- Fazer: metadata `source_key`/ambiente/perfil, associação idempotente das fontes legadas, validador de code/tabela, resolução segura de credencial, capabilities/status, API e UI agrupada. Incluir as opções AKS inventory/cost allocation/container insights quando aplicáveis. Criar a aba **Integrações**, metadata `system_integration`, configuração/teste do Jev e item futuro ServiceNow. Preservar endpoints existentes durante migração compatível.
- Saída: operador escolhe fontes por ambiente, vê proveniência/status individual e configura serviços auxiliares separadamente.
- Aceite: 4P/TI, Automation, ARG, PTU aparecem sem duplicação; colisão de code é bloqueada; teste não expõe secret; Jev é configurável/testável pela aba Integrações; ServiceNow aparece como futuro/desabilitado sem enviar dados; coletas legadas seguem funcionando.

**P3 — Identidade de VM e custo atual**

- Entrada: ARG/FOCUS legados.
- Fazer: estender snapshot ARG para campos/relações VM necessários; normalizar IDs; associar custo FOCUS a VM/ambiente/período sem multiplicar linhas; marcar custo não atribuível e anomalias de join.
- Saída: inventário VM + custo observado por recurso + trilha Bronze → Silver → cubo Gold.
- Aceite: total FOCUS e cubos Gold do período fechado não muda; amostra manual de VMs bate com ARG e Silver; join não cruza tenants/subscriptions.

**P4 — Topologia AKS e alocação FOCUS por namespace**

- Entrada: P2/P3, clusters AKS e pelo menos uma fonte lógica de topologia/consumo disponível.
- Fazer: realizar spike de capability/payload; coletar snapshots de entidades/arestas Azure e Kubernetes em Bronzes próprias; construir relação cluster→node→namespace→workload→pod/container→volume/recurso Azure; obter custo ou pesos por namespace/asset via AKS Cost Analysis/OpenCost/Container Insights conforme disponibilidade; construir ponte temporal na Silver sem alterar o FOCUS detalhado; classificar workload/idle/system/service/unallocated; materializar cubo Gold reconciliado.
- Saída: grafo temporal de consumo e custo alocado navegável nos dois sentidos, com método/fonte/confiança.
- Aceite: soma alocada fecha com custo FOCUS elegível dentro da tolerância; nenhum grupo financeiro soma peso diferente de 1; reexecução não duplica; pod efêmero usa UID; custos sem vínculo ficam `UNALLOCATED`; ausência de add-on/API fica `PARTIAL/UNAVAILABLE`, sem inferir namespace por nome.

**P5 — Piloto Azure Monitor com 15 dias**

- Entrada: P2/P3, permissão Monitor.
- Fazer: coletor selecionável no worker; campos de janela/retention na connection; carga inicial de 15 dias; chunks/batch quando suportado; Bronze exclusiva, checkpoint/idempotência, Silver temporal, métricas/unidades descobertas, status por fonte e medição de duração/volume/throttling. Não solicitar 90 dias nesta fase.
- Saída: série temporal consultável por Resource ID e janela.
- Aceite: reexecutar os 15 dias não duplica; 429/5xx retoma; erro parcial não marca sucesso; memória sem agente fica ausente; contagem reconciliada com API; relatório informa tempo, linhas, bytes e estimativa para 90 dias.

**P6 — Qualidade e perfil da janela disponível**

- Entrada: P3/P5.
- Fazer: cobertura por métrica, percentis/limiares/picos, custo por VM, `requested_days`, `observed_days`, perfil versionado e `profile_hash`. Workload simples configurável com proveniência, sem grafo de migração ainda. Calcular com os dias existentes, até o limite configurado de 90.
- Saída: `resource_profile_window` com janela e hipóteses explícitas.
- Aceite: percentis batem com amostra de buckets; lacunas reduzem confiança; perfil não muda sem mudança material de entradas.

**P7 — Catálogos, preço e elegibilidade local**

- Entrada: P2/P6.
- Fazer: fontes separadas de SKU, quota, preço retail e Price Sheet quando permitido; detectar tipo de contrato e permissão; normalizar preço/unidade/vigência; policy local se afetar SKU/zona; documentar fallback.
- Saída: catálogo comparável por subscription/região/SKU e fonte de preço.
- Aceite: candidate SKU proibido/indisponível não é elegível; preço contratual e retail não se misturam; ausência de Price Sheet vira status explícito.

**P8 — Rightsizing determinístico no mesmo local**

- Entrada: P6/P7.
- Fazer: geração de candidatos, hard constraints, replay defensável, finanças por componente, risco/confiança e ranking determinístico. Persistir snapshots DE/PARA e série projetada alinhada à série observada. Começar com mesma família; ampliar por estratégia depois.
- Saída: candidatos `VALID/INVALID/REQUIRES_VALIDATION` com saving, vetos, DE/PARA e referência às séries.
- Aceite: teste com CPU baixa mas RAM/IOPS insuficiente veta candidato; sem preço comparável não mostra saving exato; duas execuções iguais produzem mesma decisão.

**P9 — Histórico e revisão humana**

- Entrada: P8.
- Fazer: versão/run, decisão/Next Best Action, snapshots DE/PARA imutáveis, estado de revisão, auditoria, API e proteção de acesso. Nenhum endpoint altera VM.
- Saída: ação e alternativas persistidas com evidência e reviewer.
- Aceite: veto determinístico sobrevive a qualquer provider; revisão não muda premissas históricas; reexecução cria versão adequada, sem duplicação indevida.

**P10 — Cubos Gold e UI do MVP**

- Entrada: P3/P4/P6/P8/P9.
- Fazer: cubos Gold de custo, oportunidade, decisão e consumo; resumo executivo, detalhe clicável DE/PARA e exploração AKS no padrão React. Mostrar configuração/capacidade/custo DE e PARA, série observada e projeção na mesma escala, dias observados, saving, risco, origem do preço, cobertura e vetos. A árvore AKS usa cubo Gold, sem ler FOCUS Silver inteiro por request.
- Saída: fluxo ponta a ponta de consulta/revisão, gráfico DE/PARA e drilldown de consumo.
- Aceite: total do resumo reconcilia com detalhes sem somar alternativas; cada nível AKS reconcilia ao pai e ao FOCUS Silver; filtros por namespace não duplicam custos; projeção tem estilo distinto de dado observado; saving realizado continua ausente antes de validação; nenhuma consulta usa namespace/API Dimon; regressão dos dashboards migrados passa.

**P11 — Estratégias avançadas e região**

- Entrada: MVP funcionando e dados de dependência/policy.
- Fazer: scheduling apoiado em Automation, outras famílias/vendor, grafo de dependências, região permitida, TCO, latência/residência e viabilidade de mudança. Advisor/Mover somente onde resolvem lacuna concreta.
- Saída: estratégias extras sob o mesmo contrato de candidato.
- Aceite: região proibida, dependência desconhecida crítica ou TCO incompleto não vira ação pronta; workload e premissas aparecem na explicação.

**P12 — Jev opcional e calibração**

- Entrada: candidatos válidos, histórico auditável e integração Jev habilitada em Connections → Integrações.
- Fazer: adapter versionado que resolve `system_integration`, contrato de input/output, fallback determinístico, cache por hash, avaliação de divergência, métricas de calibração e política de confiança. Nunca buscar endpoint/secret em valores hardcoded.
- Saída: decisões assistidas por Jev sem perder reprodutibilidade dos fatos.
- Aceite: Jev não muda `INVALID`/`REQUIRES_VALIDATION` para `VALID`; falha do serviço não apaga candidato; modelo, versão e motivo ficam auditados.

**P13 — Operação e resultado realizado**

- Entrada: fluxo em produção.
- Fazer: agendamento por fonte via scheduler existente; dependência/freshness; elevar connection de 15 para até 90 dias após os resultados do piloto; acumular ou fazer backfill do histórico faltante; aplicar retenção configurada e poda de telemetria de alta resolução; compaction; alertas; comparação antes/depois após implementação manual; taxa de aprovação/rejeição, falso positivo e desvio do saving.
- Saída: operação sustentável e medição de valor real.
- Aceite: fonte atrasada/erro visível, backfill reproduzível, custo de armazenamento medido, nenhuma telemetria de alta resolução além do corte configurado, cubos compactos íntegros e saving realizado derivado de dados posteriores e separado do previsto.

### Gates transversais

Em cada pacote: testes relevantes, migração idempotente, leitura de histórico antigo durante a compatibilidade, segredo não exposto, permissão mínima, dados 4P/TI segregados, Silver/Gold reconciliadas, commit/push e rollback documentado quando houver mutação de schema. Teste de cálculo deve usar exemplos em que a regra **falha** (RAM insuficiente, preço ausente, região proibida), não apenas casos felizes. Reiniciar worker em validações após mudar módulo importado por ele; ele mantém imports entre tasks.

## 10. Orientações técnicas Azure a confirmar na implementação

| Fonte | Verificação obrigatória | Referência oficial |
|---|---|---|
| Azure Monitor | Retenção de métricas de plataforma de 93 dias; batch até 50 recursos da mesma região/subscription; suporte por métrica e guest metrics depende de instrumentação | [Metrics in Azure Monitor](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/data-platform-metrics) |
| ARG | Paginar até esgotar `skipToken`, até 1.000 linhas por resposta; verificar truncamento e mudança de snapshot durante paginação | [Guidance for pagination](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/paging-results) |
| AKS Cost Analysis | Requer add-on habilitado, cluster Standard/Premium, identidade/CSI e oferta suportada; fornece visão reconciliada por cluster/namespace/assets e classes de custo, mas o agente deve validar a interface programática e o payload disponíveis no tenant | [AKS cost analysis](https://learn.microsoft.com/en-us/azure/aks/cost-analysis), [View Kubernetes costs](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/view-kubernetes-costs) |
| Topologia AKS | ARG cobre recursos ARM e algumas configurações; namespace, workload, pod e container exigem fonte runtime. Usar a supply chain do Turbonomic como referência de navegação, sem copiar sua lógica proprietária | [ARG query tables](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/query-language), [Turbonomic supply chain](https://www.ibm.com/docs/en/tarm/8.x?topic=works-turbonomic-supply-chain) |
| Price Sheet | EA e MCA têm escopos/endpoints diferentes; usar versão atual da API, verificar acesso no billing scope e resultado assíncrono; versões `Microsoft.Consumption` antigas têm aposentadoria anunciada | [Migrate EA to MCA APIs](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/migrate-cost-management-api), [Price Sheet API](https://learn.microsoft.com/en-us/rest/api/cost-management/price-sheet/download-by-billing-account?view=rest-cost-management-2025-03-01) |
| Resource SKUs | Conferir capabilities, restrictions e `nextLink`; catálogo global não prova disponibilidade à subscription | [Resource SKUs List](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list?view=rest-compute-2026-03-02) |
| Cost Details | Reusar Exports existentes; só considerar API report se export não cobrir campo necessário. Suporte por tipo de contrato varia | [Manage costs with automation](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/manage-automation) |

Não codificar versão de API, campo ou permissão com base apenas neste quadro. Na etapa da fonte, conferir documentação oficial vigente, comportamento do tenant e testes contra payload sanitizado.

## 11. Primeira resposta exigida da IA executora

Antes de qualquer implementação, produzir **somente o artefato P0**:

1. Mapa real do OpenFinops e divergências deste plano, com caminhos de arquivo.
2. Matriz das fontes `reusar/estender/criar` e `source_key → code real/proposto → Bronze → Silver detalhada → cubo Gold`, incluindo AKS inventory/cost allocation/container insights.
3. Baseline de custo/linhas FOCUS e conexões presentes no ambiente, separando suporte no código de registros existentes no banco, sem mostrar secrets.
4. Escolha concreta de metadata para o grupo Azure, credencial compartilhada e compatibilidade de codes.
5. Dependências de P1–P10, permissões/APIs a validar e riscos de dados; para AKS, incluir árvore de entidades, fontes por nível, grão da ponte FOCUS e prova de reconciliação proposta.
6. Critérios de teste e rollback do primeiro pacote funcional.

Depois, seguir P1 até P10 sem redesenhar a plataforma a cada passo. Abrir P11–P13 conforme suas entradas estiverem prontas. Dúvida sobre detalhe de implementação deve ser resolvida pelo contrato, documentação e código; mudar o plano apenas quando houver evidência de incompatibilidade.
