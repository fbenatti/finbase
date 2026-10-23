# Solicitação de continuidade e revisão final — métricas multicloud

Data da auditoria: 23/10/2026, America/Sao_Paulo.
Destinatário: GPT Luna.
Documento de origem: [planMulticloudMetricsP90P95P99.md](planMulticloudMetricsP90P95P99.md).
Referência Git observada durante a auditoria: `4816e579`; o workspace recebe alterações concorrentes.

## 1. Solicitação do usuário ao executor

**Implemente os requisitos pendentes do PLAN original e corrija as integrações
incompletas abaixo. Antes de dizer que finalizou, revise novamente TODOS os
itens T00–T15 e os critérios finais da seção 15.5 do PLAN.**

Não considere esta iniciativa concluída porque existem arquivos, modelos,
botões ou testes unitários aprovados. Verifique que o caminho real chama esses
componentes, publica nas tabelas corretas e permite consumir os resultados.
Cada requisito precisa de evidência rastreável ou de uma pendência explícita.

Este documento complementa o PLAN; não reduz seu escopo. JEV permanece fora da
entrega. Não implementar resize, desligamento nem savings financeiros sem os
insumos exigidos no plano. Manter o piloto limitado às duas VMs autorizadas e
não ativar agenda global.

Reutilize o que já existe. Caso a implementação atual adote uma alternativa à
arquitetura do PLAN, documente a diferença, a equivalência dos requisitos e a
migração. Uma divergência não documentada não pode ser marcada como concluída.

## 2. Como esta auditoria foi feita e seus limites

Foram lidos o PLAN completo, o relatório do piloto de pesquisa, os componentes
do pacote `infrastructure_metrics`, os modelos Silver, o router, o handler do
worker, os pontos de autorização e as telas Connections/Extraction.
Foram pesquisados os chamadores de perfis, coverage, rollups, retenção,
adaptadores, configuração, tabelas e navegação.

A auditoria foi de código e de testes locais. Não executou coleta cloud,
backfill, purge nem escrita no catálogo real. A criação física da Silver não
foi comprovada nesta revisão. A tentativa do turno anterior havia retornado
`OperationalError`; isso é evidência de indisponibilidade daquele acesso, não
prova de que uma tabela existe ou inexiste no ambiente operacional atual.

Comando reexecutado em `apps/api`:

```bash
python -m pytest -q \
  tests/test_authz_route_coverage.py \
  tests/test_infrastructure_metric_*.py \
  tests/test_oci_infrastructure_metrics_adapter.py \
  tests/test_semantic_normalization.py
```

Resultado: **85 passed**, com aviso de configuração de `pytest-asyncio`.
Este resultado não demonstra integração real Bronze→Silver, concorrência,
retomada, isolamento organizacional nem retenção.

O build web foi aprovado no turno anterior. Não foi reexecutado nesta auditoria,
pois há alterações concorrentes em telas/API de relatórios e dashboards. Na
entrega do Luna, executar novamente sobre o estado final dos arquivos tocados.

Uma reprodução sintética adicional, sem alteração de arquivos ou acesso ao
warehouse, passou a saída de `query_results_to_silver_rows()` diretamente para
`merge_metric_rows()`: **`KeyError: 'cloud_partition'`**. Detalhes em L01.

O piloto de `docs/research/oci-metric-resolution-20261023/report.md` é pesquisa
real anterior à implementação e declara que não escreveu Bronze/Silver/Gold
de produção. Não usá-lo como aceite de T07 ou de uma coleta do worker atual.

## 3. O que já existe e deve ser aproveitado

- Contratos de escopo, descriptor, query e resultado; hashes e catálogo de oito
  métricas OCI em `apps/api/app/services/infrastructure_metrics/`.
- Adaptador Monitoring OCI com resourceId explícito, mean/min/max/count/sum e
  P90/P95/P99; payloads SDK completos preservados em memória.
- Configuração opt-in no bloco `SourceConnection.config_json.infrastructure_metrics`,
  até duas instâncias, compartimento, região e resoluções 60/300/900 segundos.
- Funções de normalização larga, estimativa de chamadas, derivação de taxas de
  counters e cálculo isolado de quantis locais.
- Writer Bronze separado por conexão; merge diário e publicação Silver usando
  os mecanismos existentes de staging/transação.
- Modelos lógicos de série, cobertura, inventário e perfil ampliados.
- Inicializador de schema físico de `resource_metric_series` em
  `publication.ensure_metric_series_table()`, chamado por `service.py` quando
  `persist=True`, antes das consultas OCI. A existência física ainda exige
  execução e inspeção do catálogo real.
- Tipo `infrastructure_metrics_collection` reconhecido na fila/worker, lock
  analítico e reenfileiramento após reinício.
- Três rotas no prefixo real `/custos/metricas-infra`: `POST /planejar`,
  `POST /coletar`, `GET /tarefas/{task_id}`; módulo RBAC registrado.
- Checkbox e orientação de policy em Connections; controles de janela,
  resolução, estimativa, enfileiramento e status em Extraction.

Essa base representa implementação parcial. Os itens seguintes impedem declarar
que o monitoramento multicloud do PLAN está pronto.

## 4. Matriz de aderência T00–T15

| Item | Estado auditado | Evidência e falta principal |
| --- | --- | --- |
| T00 — inventário real | PARCIAL | Pesquisa anterior e código disponíveis; falta checklist operacional atualizado de conexões, tabelas/esquemas reais, dependências e divergências. |
| T01 — contratos/semântica | PARCIAL | Contratos e catálogo OCI existem; faltam identidade completa/versionamento semântico, conversões por descriptor e alinhamento do protocolo/registry. |
| T02 — configuração/segurança | PARCIAL | JSON na conexão, opt-in, RBAC e dry-run de chamadas; faltam metadata/config versão, autorização por conexão/organização, limites persistidos de pontos/bytes/RPS e descoberta real controlada. |
| T03 — Silver | PARCIAL, FÍSICO NÃO COMPROVADO | Modelos ampliados e inicializador da série; faltam schema diff real, evolução das demais tabelas, relações, legado e compatibilidade completa entre produtores e modelos. |
| T04 — publicação | PARCIAL COM BLOQUEIO REPRODUZIDO | Merge e staging existem; saída do normalizador falha no merge. Faltam testes reais de isolamento, transação, concorrência, conflito de duplicatas e rerun. |
| T05 — OCI | PARCIAL | Consulta oito métricas Compute; faltam inventário/capacidade/attachments, capabilities verificadas, semântica de erros e validação viva do adaptador da aplicação. |
| T06 — operação | PARCIAL | Fila e handler existem; faltam checkpoints, cancelamento cooperativo, backoff, orçamento completo, progresso por chunk e retomada comprovada. |
| T07 — piloto aplicado 24h | PENDENTE DE EVIDÊNCIA OPERACIONAL | Existe pesquisa 24h em script; não há relatório de reconciliação do pipeline atual com Bronze/Silver reais e rerun. |
| T08 — 60 dias | NÃO IMPLEMENTADO NO NOVO FLUXO | `retention_days=60` é validação/configuração; não há planner de backfill, cutoff/retention próprios nem lifecycle físico demonstrado. |
| T09 — rollups/perfis | PARCIAL | Quantil linear e taxa de counter existem; faltam rollups, coverage e job que leia Silver, calcule, publique e exponha perfis. |
| T10 — sete dias/ADR | PENDENTE | Comparação de pesquisa 24h existe; falta experimento sete dias, custo/volume Silver real e decisão por categoria. |
| T11 — API/Gold | PARCIAL | Apenas três rotas de controle; faltam leitura de séries/perfis/comparações, config/discovery completos e Gold do módulo. |
| T12 — monitoramento | NÃO IMPLEMENTADO | Há configuração e coleta em duas telas existentes; falta página de monitoramento com filtros, gráfico, cards, gaps, comparação e navegação. |
| T13 — outras clouds | NÃO IMPLEMENTADO NO NOVO PACOTE | Só há `providers/oci.py`. Serviços antigos Azure/PTU não demonstram adaptação ao novo contrato multicloud. |
| T14 — contrato savings futuro | NÃO LOCALIZADO | Campos existem parcialmente; falta contrato publicado/versionado com estados, hashes e requisitos de capacidade/preço. |
| T15 — operação/aceite final | PARCIAL | UI orienta policy mínima de consulta; faltam runbook completo, relatórios por cloud, rollback e reconciliação final T00–T15. |

Nenhuma tarefa deve receber aceite integral apenas a partir desta matriz.
Luna deve atualizar cada linha com evidências após implementar.

## 5. Correções prioritárias antes do piloto operacional

### L01 — Corrigir o contrato normalizador → publicação (T01/T03/T04)

**Prioridade: bloqueia a publicação atual.**

Em `publication.py`, `_row_key()` usa `row[field]` para todos os campos de
`_KEY_FIELDS`, incluindo `cloud_partition` e `sub_account_id`. O normalizador
atual não inclui essas duas chaves. Elas são opcionais no conjunto de campos
obrigatórios, mas o acesso continua obrigatório. O primeiro merge de uma linha
normalizada gera `KeyError: 'cloud_partition'`.

Os testes atuais de publicação montam manualmente essas chaves como `None`;
o teste de serviço substitui o publisher por fake. Por isso os 85 testes passam.

Implementar um contrato único de linha/identidade que trate opcionais de forma
consistente. Não resolver mascarando ausência de campos realmente obrigatórios.
Adicionar teste que use a saída do normalizador real no merge real e depois um
teste Iceberg de publicação com esses mesmos dados.

Reprodução executável a partir da raiz, sem cloud nem warehouse:

```bash
PYTHONPATH=apps/api python - <<'PY'
from datetime import datetime, timedelta, timezone
from app.services.infrastructure_metrics.catalog import OCI_METRICS
from app.services.infrastructure_metrics.contracts import (
    MetricScope, QueryPlan, QueryResult, MetricPoint,
)
from app.services.infrastructure_metrics.normalization import query_results_to_silver_rows
from app.services.infrastructure_metrics.publication import merge_metric_rows
start = datetime(2026, 10, 23, tzinfo=timezone.utc)
scope = MetricScope('oci', 4, 'synthetic-account', 'synthetic-region',
                    ('synthetic-vm',), enabled=True)
plan = QueryPlan(OCI_METRICS[0], scope, start, start + timedelta(minutes=1),
                 60, ('mean',), request_id='synthetic-request')
result = QueryResult(plan, 200, points=(MetricPoint(
    start, {'resourceId': 'synthetic-vm'}, {'mean': 10.0}),))
rows = query_results_to_silver_rows((result,),
    source_owner='oci_infrastructure_metrics', collection_run_id='audit-synthetic')
print(len(merge_metric_rows([], rows)))
PY
```

Também corrigir/verificar:

- Duplicatas conflitantes no lote incoming: hoje podem sobrescrever valores
  silenciosamente; distinguir atualização autorizada de colisão no mesmo lote.
- Não misturar séries por falta de namespace, scope, owner, cloud_partition ou
  sub_account na identidade/hash. Hoje `identity.series_id()` não recebe vários
  desses componentes previstos pelo PLAN. Versionar a mudança e seus leitores.
- Taxas derivadas preservam `source_metric_name`; conferir sua identidade física
  com parent/metric/origin antes de introduzir outros tipos de derivação.
- Descritores devem declarar quais dimensões identificam a série; não deixar
  alteração de um label descritivo criar uma série nova sem política explícita.

**Aceite:** normalizador real → merge real → Iceberg real funciona, preserva
as outras VMs/conexões/resoluções e rejeita colisões ambíguas.

### L02 — Completar a linhagem Bronze e preservar aquisição bruta (T04/T05/T07)

`normalization.py` grava `payload_hash=None`, `bronze_path=None` e usa o request
ID lógico do plano para todas as estatísticas. `bronze.py` calcula hash/caminho
e guarda request ID do provedor, mas esses dados não voltam para a Silver.
`_bronze_blob_path` não é preenchido pelo normalizador.

Além disso, `service.py` normaliza e deriva counters antes de persistir Bronze:
uma falha de normalização perde a oportunidade de preservar as respostas já
adquiridas. A Bronze precede a publicação Silver, mas ainda não precede o
processamento derivado que pode falhar.

Implementar manifest de aquisição e persistência raw antes da normalização
persistida, com lookup estável por recurso/descriptor/estatística/chunk/página.
Preencher na Silver a referência exata, hash e request ID de cada estatística.
Definir imutabilidade/versionamento na retomada: hoje a chave Bronze contém run
ID e `replace_bronze_slice()` pode substituir uma resposta do mesmo run.

**Aceite:** dado um `native_p99` ou `value_average` de Silver, recuperar o payload
correspondente e validar o hash. Falha no normalizador mantém raw reprocessável;
nenhum retry altera uma evidência anterior sem registrar nova versão.

### L03 — Entregar e verificar o esquema físico Silver (T03)

O inicializador atual só cobre `resource_metric_series` e só é chamado em uma
coleta `persist=True`. Salvar a conexão ou habilitar o checkbox não cria a tabela.
Não há aceite físico do catálogo registrado nesta entrega.

Fornecer rotina idempotente de preparação/migração e forma operacional clara de
executá-la. Verificar também `resource_metric_coverage`, inventário, perfis e
relações. Criar `resource_relationship_snapshot` caso não exista equivalente.
Não criar tabelas vazias apenas para simular que seus produtores existem.

Registrar schema diff antes/depois: colunas, tipos, nulabilidade, field IDs,
particionamento, owner/conexão e namespace real. Evoluir de forma aditiva e
validar dados legados; marcar semântica/resolução antigas como
`LEGACY_UNVERIFIED` quando não houver prova. Tratar tipos incompatíveis sem DROP
da tabela canônica e sem alegar sucesso apenas porque os nomes já existem.

Usar os locks da operação também na inicialização/evolução, que atualmente
acontece fora do lock de `publish_metric_rows()`.

**Aceite:** inspeção real do catálogo e leitura Trino/Arrow confirmam esquema e
compatibilidade; se o ambiente estiver indisponível, registrar bloqueio e deixar
esse aceite pendente, sem afirmar que a tabela física foi criada.

### L04 — Impedir publicação após cancelamento e suportar retomada (T06)

O handler em `worker.py` chama uma coleta síncrona completa sem `should_abort`.
Não consulta `_is_etl_task_cancelled()` entre métricas/chunks/publicações e
atribui `COMPLETED`/`FAILED` ao final sem preservar um cancelamento concorrente.
O botão Parar existente na UI não prova que a coleta parou.

Implementar cancelamento cooperativo e rechecagem antes de publicar cada fatia.
Preservar CANCELLED; não iniciar novos chunks após o sinal. Persistir checkpoints
somente após commit das fatias e retomar pelo manifest, não repetir o run inteiro.
Congelar escopo, versão de configuração, `as_of_utc` e entradas da execução:
atualmente o worker relê a configuração mutável da conexão.

Garantir que o resultado não substitua os únicos parâmetros necessários à
rastreabilidade/retomada. Persistir progresso real para o supervisor durante o
trabalho, além da atualização inicial/final.

**Aceite:** cancelar no meio mantém fatias concluídas, interrompe as futuras e
permanece CANCELLED; reinício retoma sem duplicar nem trocar allowlist/janela.

### L05 — Completar autorização e segurança da configuração (T02/T11)

`require('infrastructure_metrics', ...)` aplica permissão de módulo. As rotas
buscam a conexão/tarefa por ID, sem validação explícita do acesso àquela
conexão/organização. Não há teste específico desse isolamento no pacote.

Resolver o escopo autorizado no servidor, tanto para enfileirar quanto para
consultar tarefa/séries/perfis. Revisar também os endpoints genéricos de fonte e
tarefas usados pela UI, e a relação entre permissões de configuração, coleta,
leitura e cancelamento. Falta de um modelo de isolamento existente é trabalho
pendente, não autorização implícita para todas as conexões.

Implementar configuração versionada prevista no PLAN ou documentar alternativa
metadata equivalente com auditoria, budgets e snapshot por run. Validar no
backend ao salvar; não depender só do formulário. Preservar configurações
avançadas ao editar pela UI e permitir desativação explícita sem perder defaults.

A busca de tarefa ativa e o insert não são atômicos; testar duas solicitações
simultâneas. Se retornar tarefa existente, retornar o plano daquela tarefa:
hoje é possível devolver task ID antigo junto do plano recém-solicitado.

**Aceite:** usuário com acesso à conexão A não consulta/dispara/cancela B;
configuração auditada; clique concorrente não duplica trabalho.

### L06 — Backoff, limites e métricas de execução corretos (T02/T05/T06)

O cliente OCI desliga retry SDK, mas não há backoff próprio. Cada exceção é
registrada e o loop segue. Faltam jitter/Retry-After, classificação de erros,
pacing/RPS e limites de pontos/bytes por run. O teto de 128 chamadas existe no
router, não é uma política persistida aplicada em todos os caminhos de execução.

Implementar budgets e tentativas no orquestrador. Validar capabilities e os
limites de duração/histórico/paginação aplicáveis a cada endpoint; não tratar o
contador `pages` atual como prova de paginação. O protocolo do registry declara
`plan_queries`, mas o adaptador OCI não o implementa; `describe_metrics` não está
exposto e o registry não dirige a coleta atual. Unificar o contrato operacional.

Corrigir contagens: `completed_query_count=len(results)` conta descriptors;
`failed_query_count` conta resultados agregados com erro. Não são as 128 consultas
HTTP possíveis. `rows_raw` e UI chamam esses valores de consultas. Latência e
streams agregados por descriptor são repetidos em cada registro raw. Medir
chamadas HTTP/tentativas, erros, pontos, bytes e tempo na granularidade correta.

**Aceite:** 429 respeita Retry-After e budget; 400 permanente não repete cegamente;
estimativas e observações ficam diferenciadas; custo monetário é estimado com
premissas ou UNKNOWN, nunca inferido como zero pelo tamanho do piloto.

## 6. Funcionalidades restantes do PLAN

### L07 — Inventário OCI, capacidades e relações (T05/T03)

Implementar descoberta paginada restrita e capacidades Compute/shape/RAM,
volumes/boot/attachments/VNICs; fanout limitado e truncamento visível.
O `discover()` atual apenas devolve os IDs recebidos e não consulta recursos.

Persistir snapshots com vigência e evidência, relações e motivos de ausência.
Descobrir métricas/unidades/cadência realmente emitidas por recurso e
estatística. O `source_resolution_verified` atual apenas testa pertencimento à
lista de resoluções do descriptor; validar cadência efetiva, não só suporte
configurado. Verificar alinhamento epoch UTC e qualidade parcial das estatísticas;
hoje uma linha pode manter `quality_status='OK'` com consulta parcial agregada.

Correspondência OCPU/vCPU deve depender de arquitetura/shape. Não chamar CPU%
de vCPU consumida sem capacidade válida. Separar bytes provisionados de uso de
filesystem. Atualizar orientação de policies conforme as novas chamadas reais:
a policy de Monitoring apresentada na UI atual cobre apenas consulta de métricas.

**Aceite:** duas VMs comuns, sem dependência de GPU/GenAI, com snapshots e
capabilities reconciliáveis; AI/GPU existente continua funcional.

### L08 — Executar piloto real Bronze→Silver de 24h (T07)

Após L01–L07, executar no worker atual a allowlist aprovada, com teto/orçamento
revisável, 1m e estatísticas suportadas. Não usar o script de pesquisa como
substituto do pipeline aplicado.

Medir arquivos/bytes reais Bronze e Silver larga, reconciliar buckets por
série/dia/estatística e amostrar a linhagem. Repetir o mesmo input e verificar
contagem lógica inalterada. Não confundir número de linhas da fatia inteira
retornado pelo merge com quantidade de novas linhas daquela execução.

**Saída:** relatório sanitizado com manifests, versões, consultas, erros, gaps,
esquema físico e reconciliação. Payloads e identificadores privados não vão ao Git.

### L09 — Histórico efetivo e retenção de 60 dias (T08)

Implementar planner de chunks, sondagens 1/7/14/30/45/59 dias, checkpoint e
fallback explícito por capability. Congelar `[as_of_utc-60d, as_of_utc)`; permitir
que o job processe essa janela em requisições pequenas. Não remover o limite
seguro por requisição para enviar 60 dias de uma vez.

Adicionar incremental com settling delay/overlap e prioridade compatível com o
backfill. Agenda deve ser implementada como enqueue e permanecer desabilitada
até o aceite do piloto. Campo `retention_days=60` não armazena nem expira dados.

Implementar cutoff lógico de leitura e retenção por owner/conexão nas camadas
previstas; snapshots/órfãos com dry-run, grace e locks. Preservar snapshot de
capacidade anterior ao cutoff quando necessário. Separar bytes físicos/lógicos.

**Aceite:** histórico pedido/observado demonstrado, lacunas explícitas, sem
interpolação; purge não toca billing, AI/GPU ou outra fonte.

### L10 — Coverage e motivos de ausência (T03/T09)

Criar produtor/writer de `resource_metric_coverage`, hoje apenas modelo lógico.
Publicar cobertura mesmo sem amostras. Calcular janela pedida/efetiva, vida
conhecida do recurso, cadência observada, duplicatas, missing, primeiro/último,
gaps internos e nas bordas, estatísticas suportadas/retornadas e códigos de motivo.

Não interpretar HTTP 404 isolado como inexistência ou autorização resolvida;
não usar RESOURCE_STOPPED sem história. Não preencher faltantes com zero nem
transformar fonte de 5m em fonte de 1m por configuração. Versionar política de
qualidade que limite uso de perfis incompletos em capacidade.

### L11 — Rollups, taxas entre chunks e perfis persistidos (T09)

Criar rollups 5m/15m da base 1m. Guardar origem/linhagem, máximo/min e
denominadores corretos; não combinar percentis nativos como percentil global.
Não consultar as três resoluções para toda a frota como política padrão.

`build_window_profiles()` só é chamado pelos testes no estado auditado. Criar
job que leia Silver publicada, selecione uma base/resolução e grave perfis
versionados. Completar episódios/tempo acima de limiares, duração, headroom
quando capacidade válida e hash dos snapshots de entrada.

Alinhar produtor e modelo: o helper devolve `expected_buckets`/`quantile_method`
que não constam no modelo diário, não fornece os campos obrigatórios legados
`window_from_day`/`window_to_day` e devolve `profile_built_at` datetime onde o
modelo declara varchar. Resolver também primary_measure/time_field legados do
perfil, preservando compatibilidade com seus leitores.

A taxa de counter hoje deriva somente dos buckets do lote recebido. Buscar o
predecessor persistido ao atravessar chunks/dias; não transformar cada início
de chunk em lacuna artificial. Segregar resoluções/origens e revalidar resets.
O cálculo atual de `largest_gap_seconds` do perfil usa distância entre buckets,
inclusive intervalo normal, e ignora bordas: definir e testar semântica de lacuna.

**Aceite:** fixture 59×10% + 1×100%, constantes, zero legítimo, gaps/bordas,
count fracionário, reset, mudança de capacidade e perfil parcial. Mesmas entradas
produzem mesmo hash; cada resultado é rastreável sem JEV.

### L12 — Experimento sete dias e decisão de resolução (T10)

Comparar a mesma janela/escopo/semântica em 1/5/15m, medindo quantis, máximos,
episódios, duração, volume físico Silver, consultas, latência e custo com
premissas. Diferenciar derivado de nativo. Não extrapolar Parquet de pesquisa
por estatística em linhas para a Silver larga.

Produzir ADR com critérios da seção 12 do PLAN, limites e exceções por categoria.
Sem sete dias disponíveis, registrar janela efetiva e manter o aceite pendente;
não concluir decisão global apenas com duas VMs/24h.

### L13 — APIs de dados, Gold e monitoramento (T11/T12)

Completar o prefixo existente `/custos/metricas-infra`, sem criar namespace
paralelo de rotas equivalentes. Implementar configurações, descoberta, séries,
perfis e comparação; execução inclui manifest/coverage/progresso sanitizados.
Usar filtros autorizados, parâmetros e allowlists, pruning, paginação e teto de
pontos; leitura suporta até 60 dias com cutoff. Não chamar cloud ao abrir gráfico.

Criar Gold de resumo e seu materializador determinístico a partir da Silver.
Detalhe permanece Silver. Preparar leitura/exportação controlada dos dados
Silver conforme fluxo de Extração/Exploração, sem SQL livre no browser.

Criar página `apps/web/src/pages/InfrastructureMetrics/` e menu autorizado:
provider/conexão/recurso/métrica/janela/resolução, P90/P95/P99/máximo/cobertura,
gráfico com pico, tabela de gaps, comparação e custos medidos/estimados. Rótulos
precisam distinguir P95 das médias de 1m de P95 nativo por bucket.

Preservar o checkbox e o painel de coleta existentes. Corrigir divergências de
UX: a tela menciona 60 dias, mas só há coleta manual de até 24h; distinguir
política configurada de histórico realmente disponível. Tratar poll/reload/troca
de conexão para respostas antigas não aparecerem na nova seleção. Reconciliar
status após reload e permissões de Parar/consultar.

**Aceite:** usuário vê dado efetivo, base do percentil, método, cobertura,
telemetria ausente e comparações no mesmo escopo. Monitoramento não se resume
a formulário de conexão e botão de enfileirar.

### L14 — Adaptadores Azure/AWS/GCP e contrato futuro (T13/T14)

Implementar cada provider em incremento independente, reaproveitando os mesmos
contratos, Silver, coverage, perfis e identidade. Descobrir capacidades,
unidades, labels e estatísticas reais; declarar memória guest e percentis não
suportados como tal. Integrar registry/credenciais com a coleta operacional.

Ausência de credencial permite `IMPLEMENTED_NOT_LIVE_VALIDATED` para código
implementado ou `NOT_CONFIGURED` para a conexão ausente. Não usar essas etiquetas
para encobrir um adaptador que sequer existe. Não retornar dados simulados.
Executar fixtures contratuais comuns e validação viva limitada quando houver
credencial autorizada; falta de outra cloud não impede concluir o piloto OCI.

Documentar contrato versionado de perfis/capacidades/cobertura para futuro motor,
com hashes e estados READY_FOR_CAPACITY_ANALYSIS, INCOMPLETE_TELEMETRY,
MISSING_CAPACITY e MISSING_COMPARABLE_PRICE. Sem JEV nem savings inventado.

### L15 — Runbook e evidências de entrega (T15)

Documentar setup/migração de tabelas, policies conforme chamadas reais,
configuração/versionamento, budgets, incremental/backfill, cancelamento,
checkpoints/recovery, retenção/grace, custos/UNKNOWN, troubleshooting e rollback.

Manter relatório por cloud e uma matriz requisito→código→teste→evidência real.
Atualizar status do PLAN em registro de acompanhamento, preservando o conteúdo
original e as evidências históricas. Não reescrever o escopo para fazer o status
parecer completo.

## 7. Sequência sugerida de execução

1. Revalidar workspace/ambiente e criar checklist T00 atualizado.
2. Corrigir L01–L06, com integração de publicação e segurança antes de dados reais.
3. Completar inventário/capabilities OCI e executar T07/L08.
4. Completar coverage, planner/checkpoints, histórico/retention e perfis/rollups.
5. Executar comparação de sete dias e ADR com volume/custo reais da aplicação.
6. Entregar APIs de leitura, Gold e tela de monitoramento.
7. Completar adapters restantes, contrato futuro e runbook.
8. Fazer a revisão final obrigatória abaixo e corrigir o que ela revelar.

Ajustar dependências pequenas quando necessário, mantendo o piloto limitado.
Não instalar agentes nem ampliar permissões ou escopo cloud por conta própria.

## 8. Revisão obrigatória antes de declarar conclusão

Luna, execute esta revisão sobre o código FINAL, depois dos últimos commits:

- [ ] Ler novamente o PLAN inteiro, este documento e as solicitações posteriores
  do usuário (checkbox, policies, Extração e Silver física).
- [ ] Atualizar TODOS os T00–T15: COMPLETO_COM_EVIDÊNCIA, PARCIAL,
  NÃO_IMPLEMENTADO ou BLOQUEADO_POR_DEPENDÊNCIA_EXTERNA. Para conectores, adicionar
  IMPLEMENTED_NOT_LIVE_VALIDATED quando cabível. Não marcar completo por intenção.
- [ ] Para cada item, registrar arquivos/funções, commit, teste executado,
  resultado e evidência do ambiente que confirma o aceite.
- [ ] Verificar caminhos completos: salvar config → planejar → fila → worker →
  raw Bronze → normalizador real → merge real → commit Silver → coverage/perfis →
  Gold/API → UI. Mock de publisher não prova escrita Silver.
- [ ] Reproduzir L01 corrigido e publicar em catálogo de integração; testar
  rerun, A/B/conexão2/base/rollup, falha antes do commit e concorrência.
- [ ] Inspecionar as tabelas físicas e seus schemas no ambiente-alvo. Registrar
  catálogo/namespace, tipos, partições e linhas consultáveis, sem expor secrets.
- [ ] Demonstrar rastreabilidade por estatística com payload/hash/request real.
- [ ] Testar cancelamento/retomada, budgets e throttling; conferir que CANCELLED
  não vira COMPLETED por sobrescrita do worker.
- [ ] Demonstrar isolamento de conexão/organização e ausência de SQL livre.
- [ ] Demonstrar 60 dias pedidos versus observados, cutoff e lifecycle seguro;
  configuração `60` sozinha não serve como evidência de retenção.
- [ ] Demonstrar quantis e rollups corretos e persistidos, sem média de percentis
  nem mistura da base com seus derivados; capacidades têm vigência.
- [ ] Demonstrar comparação 1/5/15, custo/volume e ADR; distinguir pesquisa
  anterior de medição do pipeline operacional.
- [ ] Verificar página/menu/filtros, método/UTC/local, vazio/parcial/erro,
  ausência de memória, histórico efetivo e navegação resumo→detalhe.
- [ ] Conferir compatibilidade dos consumidores Silver e fluxo AI/GPU tocados.
- [ ] Executar testes adequados e build web sobre o estado final. Reexecutar
  quando uma correção posterior alterar o caminho verificado; guardar resultados.
- [ ] Revisar o diff final e confirmar que nenhum arquivo/hunk alheio foi incluído.
- [ ] Revisar o próprio relatório final procurando afirmações maiores que as
  evidências: criado/validado/completo/60 dias/multicloud/P95 operacional.
- [ ] Se a revisão revelar pendência implementável, continuar implementando e
  repetir a revisão relevante antes de responder que terminou.

**Regra de encerramento:** se existe requisito obrigatório pendente ou aceite
real bloqueado, informar claramente `ENTREGA PARCIAL` e listar o que falta,
impacto e dependência. Não dizer “tudo implementado”, “finalizado” ou “pronto para
produção”. Ausência de cloud configurada permite status honesto de validação;
não dispensa a implementação dos adaptadores exigidos.

## 9. Formato obrigatório da resposta final do executor

```text
Estado geral: COMPLETO COM EVIDÊNCIAS | ENTREGA PARCIAL

Revisão T00–T15: tabela de todos os itens, sem omitir os pendentes.
Para cada item concluído: código/commit + testes + evidência do aceite.

Validação real por cloud:
- implementado / configurado / validado ao vivo;
- escopo sanitizado, janela pedida/efetiva, resolução e cobertura;
- tabelas físicas e reconciliação Bronze→Silver→perfis/Gold;
- custos/volumes e premissas.

Pendências e bloqueios restantes: requisito, impacto, causa e próximo passo.
Revisão final: quais checks foram executados depois do último ajuste.
Commits desta tarefa e limitações materiais.
```

## 10. Cuidados de workspace e commits

A instrução do usuário permanece obrigatória: **logo após cada alteração de
código, fazer o commit no Git**. Inspecionar diff, selecionar somente o incremento
desta iniciativa e commitar imediatamente. Correções posteriores exigem novo
commit. Não usar `git add .`, reset/clean nem sobrescrever trabalho concorrente.

Na auditoria havia modificações locais de menu, API de análise, App, componentes
Pivot, páginas de Analysis, API client e novos arquivos de dashboards/relatórios.
Essa lista é apenas um retrato: executar `git status --short --branch` de novo
antes de editar. Não presumir autoria nem incorporar essas alterações ao pacote
de métricas. Não iniciar outro chat nem enviar mensagens a terceiros a partir
deste documento; ele é o pedido de implementação/revisão a ser entregue ao Luna.

## 11. Registro de execução — 23/10/2026

Executor desta continuidade: Codex, na branch `main`. Este registro não substitui
nem reduz o aceite T00–T15. O estado continua **ENTREGA PARCIAL**.

### Incrementos implementados e commits

| Incremento | Commit | Evidência local |
| --- | --- | --- |
| Chaves de identidade opcionais usam NULL canônico no merge; teste passa a saída real do normalizador ao merge. | `ef4fce5f`, `3e631f60` | `test_infrastructure_metric_publication.py` |
| Persistência Bronze acontece antes da normalização/derivação. | `ba8f16d8` | Revisão do fluxo em `service.py`; sem escrita em catálogo real nesta sessão. |
| Identidade de série v2 incorpora namespace, escopo, owner e campos opcionais, mantendo hash/schema de dimensões v1 para não dividir a chave de bucket; lote rejeita valores conflitantes; Silver recebe lineage por estatística com hash/request/path Bronze. | `b92a6718`, `32d33c2b`, `99e8a998`, `4025b664`, `0cf4f4c2`, `bd2ac3af`, `81e17d65`, `f60735f8`, `c2567643`, `40c98f78`, `fd2ef8a2`, `4170be02` | Fixtures verificam colisão e hash/path por estatística; ainda sem compatibilidade operacional de consumidores legados e publicação física em catálogo real. |
| Contagens de execução usam status das chamadas registradas, não o número de descriptors; fallback para adapters agregados. | `9366989c`, `26d33f05`, `bcd38a99` | Suíte de métricas abaixo. |
| Introduzido cancelamento cooperativo entre requisições OCI, antes da publicação diária e preservação do estado CANCELLED no worker. | `21b89f4a`, `b4c8da15`, `3298a66f`, `a4c2734d`, `2aee0349` | Teste de cancelamento do adapter; não houve teste integrado de worker concorrente/retomada. |
| Perfis calculam lacunas ausentes incluindo bordas e emitem dias/perfil serializáveis; modelo declara expected_buckets e quantile_method. | `a7ea546b`, `f9733e38`, `022fb0ca` | `test_infrastructure_metric_profiles.py` |
| Bucket com resposta agregada parcialmente falha recebe `PARTIAL` e motivo. | `f7953bed` | Suíte de métricas abaixo. |
| Router verifica RBAC por `connection:<id>` para leitura/criação, mantém grant global e serializa enqueue com advisory lock; tarefas novas guardam snapshot do plano. | `307e674b`, `2fab3455`, `ba2895c6`, `ea646c03` | Testes unitários de permissão; concorrência do endpoint ainda sem integração de banco. |
| Worker preserva snapshot do pedido e usa allowlist/região/métricas congeladas no enqueue, sem gravar credenciais no job. | `94d4f40d`, `ddcdbed5`, `dfa4d879`, `b7e6ba09`, `5f7c0c59` | Teste de serviço edita a conexão depois do snapshot; escopo do worker usa a configuração original. |
| Inicialização/evolução da tabela de séries usa o lock de escrita compartilhado. | `da89ce14` | Teste de schema com catálogo fake; sem inspeção física. |

Commits de implementação foram feitos logo após cada incremento de código,
conforme instrução do usuário. As alterações simultâneas observadas em telas de
Pivot/Analysis/Extraction, `main.py` e `pivot_view.py` foram deixadas fora destes
commits.

### Validação após os incrementos

Executado em `apps/api`:

```bash
python -m pytest -q \
  tests/test_infrastructure_metric_*.py \
  tests/test_oci_infrastructure_metrics_adapter.py \
  tests/test_semantic_normalization.py
```

Resultado inicial após os primeiros incrementos: **85 passed**. Após identidade v2,
conflitos de lote, lineage por estatística, autorização, fila e config congelada:
**93 passed**. Também passou `compileall` do router/worker/pacote. Permanece aviso
de configuração de `pytest-asyncio`.
Não foi executada build web nesta continuidade porque nenhuma tela web de
métricas foi implementada. Não foram chamadas APIs OCI nem feitas consultas ou
escritas no catálogo real.

### Reavaliação honesta T00–T15 após esta execução

| Item | Estado atual | O que ainda impede o aceite |
| --- | --- | --- |
| T00 | PARCIAL | Inventário operacional de conexões, catálogo físico, dependências e divergências ainda não foi produzido. |
| T01 | PARCIAL | Identidade v2 contempla namespace/scope/owner e campos opcionais, preservando a chave de bucket baseada em dimensões v1; faltam conversões por descriptor, integração completa do protocolo e compatibilidade de leitores que guardaram series_id v1. |
| T02 | PARCIAL | RBAC por connection id, lock de enqueue e snapshot da configuração implementados; falta autorização de organização, histórico de versões/auditoria e budgets persistidos, além de teste integrado de concorrência. |
| T03 | PARCIAL — físico não comprovado | Só o initializer da série existe; falta preparar/evoluir todos os modelos, relações, schema diff real e compatibilidade legado. |
| T04 | PARCIAL | Corrigidos chaves opcionais e conflitos de valores duplicados no lote; falta isolamento/concorrência/rerun e publicação Iceberg real. |
| T05 | PARCIAL | Adapter ainda cobre consulta de métricas; sem inventário/capacity/attachments/capabilities nem validação viva. |
| T06 | PARCIAL | Cancelamento cooperativo, preservação de CANCELLED e snapshot de escopo implementados; faltam checkpoints persistidos, retomada, progresso durante chunks, retry/backoff, budgets de pontos/bytes/RPS e teste concorrente integrado. |
| T07 | PENDENTE — evidência operacional | Nenhum piloto do worker atual com Bronze/Silver reais foi executado nesta sessão. |
| T08 | NÃO IMPLEMENTADO no fluxo novo | Ainda sem backfill de 60 dias, planner/chunks, cutoff/lifecycle e reconciliação real. |
| T09 | PARCIAL | Cálculo isolado de perfil/gaps melhorado; ainda sem coverage writer, rollups, job leitor/publicador, schema/consumidor integrado e taxa entre chunks. |
| T10 | PENDENTE | Sem experimento operacional de sete dias ou ADR derivado dos bytes/custos reais da aplicação. |
| T11 | PARCIAL | Permanecem rotas de controle; faltam APIs de séries/perfis/comparação, Gold e leitura/exportação autorizada. |
| T12 | NÃO IMPLEMENTADO | Falta página/menu de monitoramento dedicada. |
| T13 | NÃO IMPLEMENTADO | Adaptadores Azure/AWS/GCP não foram criados neste incremento. |
| T14 | NÃO IMPLEMENTADO | Falta contrato versionado de readiness para futura análise de capacidade/savings. |
| T15 | PARCIAL | Este registro documenta commits/testes e bloqueios locais; faltam runbook, relatório por cloud, rollback e reconciliação final. |

### Correções técnicas ainda necessárias antes de prosseguir com dados reais

- A linhagem Silver agora carrega hash/caminho/request por estatística quando
  `bronze_persisted=True`; falta comprovar isso em Bronze/Silver físicos e tornar
  evidências imutáveis/versionadas nos retries.
- A identidade `v2` substituiu o hash do normalizador; falta migração e
  compatibilidade dos leitores com séries legadas `v1` antes de misturar dados.
- O novo teste normalizador→merge valida o merge em memória; não é teste de
  publicação Iceberg. A tabela física e seus field IDs continuam sem evidência.
- O cancelamento não fornece checkpoints nem retomada; a operação inteira ainda
  não foi integrada com progresso real e concorrência do worker.
- O snapshot da configuração evita mudança de allowlist/região entre enqueue e
  execução, mas ainda não guarda histórico de versões no banco nem mede todos os
  budgets exigidos pelo PLAN.
- O perfil corrigido segue sendo função sem consumidor operacional; ausência de
  amostras ainda não gera linha de coverage.

### Nota de concorrência de Git

O commit `074aeed4` foi criado enquanto outros arquivos já estavam staged no
índice compartilhado; além do teste deste módulo, incluiu alterações de
`apps/api/app/routers/analysis.py`, `apps/web/src/components/PivotAnalysis.tsx`
e `apps/web/src/services/api.ts`. Não foi feito reset/rebase/revert automático
porque isso reescreveria estado compartilhado; esses arquivos não foram incluídos
nos commits posteriores desta iniciativa e devem ser revisados pelo dono dessas
alterações antes do merge da branch.

**Conclusão desta continuidade: ENTREGA PARCIAL.** Antes de declarar concluído,
reler este documento, o PLAN completo e as solicitações de UI/Extração/Silver;
executar a revisão da seção 8 inteira e manter explicitamente pendentes os
aceites que dependem de catálogo, credenciais e medição operacional reais.

### Atualização incremental — perfis persistidos e API (23/10/2026)

Depois do registro acima, foram adicionados:

| Incremento | Commit | Evidência local | Limite restante |
| --- | --- | --- | --- |
| Inicializador/evolução da tabela lógica `resource_profile_daily_window` com partição por dia final e conexão, e writer idempotente por chave natural do perfil. | `ad2675a9`, correção de posicionamento em `f4a257c9` | Teste de bootstrap do schema com catálogo fake em `test_infrastructure_metric_publication.py`. | Sem catálogo real; publicação real, field IDs legados e concorrência permanecem sem comprovação. |
| `POST /custos/metricas-infra/perfis/gerar` lê uma série limitada da Silver, exige conexão autorizada, allowlist de recurso/métrica, seleciona resolução/estatística e persiste perfil versionado; `GET /perfis` lê perfis com janela limitada. | `6096021d`, validação de allowlist e correção de origem `NATIVE` em `98381fc8` | Rotas carregam com a suíte de RBAC e de métricas. | Sem teste de integração da rota com Iceberg real; sem paginação além do limite fixo, Gold ou tela de monitoramento. |
| Perfil passou a emitir P50 junto de P90/P95/P99; bootstrap de tabela diária ganhou teste dedicado. | `12d6d683`, `d2d3d326`, `1edb4ffd` | Suíte atual: **99 passed**; `py_compile` do router e publisher passou. | Teste de perfil usa fixture local; não atesta o perfil operacional na tabela real. |

O perfil produzido usa `WINDOW_OF_BUCKET_VALUES` e o método linear. O endpoint
aceita somente `origin_kind=NATIVE`, uma resolução e uma estatística explícitas;
portanto o P95 é calculado sobre os valores médios (ou outra estatística
selecionada) dos buckets guardados na Silver. Isso não representa P95 nativo
OCI por bucket. O perfil não preenche custos, capacidade/headroom ou duração de
limiares: esses campos ficam nulos até existirem fontes válidas.

### Situação revisada T00–T15 após API de perfis

| Item | Estado | Evidência/pendência atual |
| --- | --- | --- |
| T00 | PARCIAL | Sem inventário do ambiente operacional e schemas físicos. |
| T01 | PARCIAL | Identidade v2 e contratos OCI; falta conversão semântica por descriptor, migração e consumidores legados. |
| T02 | PARCIAL | Allowlist validada para coleta/perfil e RBAC por conexão; faltam isolamento organizacional, auditoria de versões e budgets completos persistidos. |
| T03 | PARCIAL — físico não comprovado | Inicializadores lógicos existem para série, coverage e perfil; faltam inventário/relacionamentos, schema diff e inspeção no catálogo real. |
| T04 | PARCIAL | Normalizador→merge em memória coberto; falta publicação/reconciliação Iceberg real, concorrência e teste completo de rerun. |
| T05 | PARCIAL | Adaptador OCI de métricas existe; inventário, capacidades, relações e validação viva pendentes. |
| T06 | PARCIAL | Cancelamento cooperativo e config congelada; checkpoints/retomada, backoff, budgets e progresso por chunk pendentes. |
| T07 | PENDENTE — evidência operacional | Sem piloto real do worker em 24h. |
| T08 | NÃO IMPLEMENTADO | Sem planner/backfill efetivo de 60 dias, cutoff ou lifecycle. |
| T09 | PARCIAL | Coverage e rollups 5m/15m integrados na coleta; cálculo/persistência de perfil via API agora conectado. Faltam predecessor de counter entre chunks, maturidade da qualidade, limiares/capacidade e validação física. |
| T10 | PENDENTE | Sem experimento operacional de sete dias e ADR com custos/volume da Silver real. |
| T11 | PARCIAL | Rotas de controle, séries, coverage e perfis disponíveis; faltam comparação, config/discovery completas, Gold, fluxo de Extração e integração de ponta a ponta em catálogo real. |
| T12 | PARCIAL | Página e item de menu criados para OCI: filtros de conexão/recurso/métrica/janela/resolução, série, cobertura e cálculo/consulta de perfil. Faltam comparação 1/5/15, tabela detalhada de gaps por bucket, custos, estados de execução e validação de acesso/menu no ambiente conectado. |
| T13 | NÃO IMPLEMENTADO | Adaptadores AWS/Azure/GCP não foram adicionados. |
| T14 | NÃO IMPLEMENTADO | Contrato versionado de readiness para análise de capacidade futura continua ausente. |
| T15 | PARCIAL | Registro incrementado e testes locais registrados; faltam runbook e reconciliação de ambiente/rollback. |

Resultado de teste após os commits de API/perfis:

```bash
cd apps/api
python -m py_compile app/routers/infrastructure_metrics.py app/services/infrastructure_metrics/publication.py
python -m pytest -q tests/test_infrastructure_metric_*.py tests/test_oci_infrastructure_metrics_adapter.py tests/test_semantic_normalization.py tests/test_authz_route_coverage.py
```

Resultado: **99 passed**. A compilação Python também passou. A advertência do
`pytest-asyncio` sobre configuração de escopo do event loop permanece.

### Incremento seguinte — página inicial de monitoramento

- `d38de0de`: nova página `apps/web/src/pages/InfrastructureMetrics/InfrastructureMetrics.tsx`, com filtros OCI, gráfico de série, resumo de cobertura e perfil, e explicação de que P95 da janela difere do P95 nativo OCI.
- `60b17d9c`: renderer lazy e item de navegação `dados_metricas_infra` protegido pelo módulo `infrastructure_metrics`.
- `f8a4b529`: compatibilidade com o alvo de compilação TypeScript.
- `40561bfc`: filtro de métrica baseado em `source_metric_name` e linhagem dessa métrica no perfil.
- `ab81ae90`: cálculo/persistência do perfil exige permissão `create` por conexão.

Após estes incrementos, a mesma suíte da API passou: **99 passed**. `npm run
build` passou (`tsc -b` + Vite); o bundle registra o módulo
`InfrastructureMetrics`. Vite reportou aviso de chunks já grandes, incluindo o
bundle de Plotly existente. A tela não usa dados simulados. O menu se origina do
catálogo JSON, porém sua sincronização/permissão no banco do ambiente não foi
validada.

Não houve chamada OCI, consulta/gravação em catálogo real nem exportação por
Extração. Não houve materialização Gold, comparação 1/5/15 ou validação visual em
sessão autenticada com dados. Portanto, cobertura operacional, Silver física e
acesso de usuário seguem sem aceite. Estado geral permanece **ENTREGA PARCIAL**.
