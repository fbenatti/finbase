# Plano de métricas multicloud, P90/P95/P99 e histórico de 60 dias

Data: 23/10/2026. Destinatário da implementação: GPT Luna.
Estado: plano apoiado em piloto OCI real; implementação de produção ainda não iniciada.

## 1. Pedido e decisões já autorizadas

Construir um módulo de monitoramento de infraestrutura que preserve séries de
consumo e permita calcular P90, P95 e P99 sobre janelas auditáveis. A Silver deve
ser compartilhada por OCI, Azure, AWS e GCP. Manter 60 dias de histórico próprio.
Comparar 1, 5 e 15 minutos antes de definir a resolução definitiva por categoria
de recurso. Começar com poucas instâncias; não habilitar uma coleta da tenancy
inteira por consequência de uma descoberta de recursos.

O JEV fica para uma etapa futura. Não invocar o serviço, criar perguntas,
produzir probabilidades nem modificar sua integração nesta iniciativa.

Entregar nesta etapa: coleta, Bronze, Silver ampliada, qualidade, perfis
estatísticos, comparação de resolução, API e tela de monitoramento. Preparar os
fatos para um futuro motor determinístico de savings. Não implementar resize,
desligamento, migração nem recomendações financeiras sem catálogo/preço e
restrições comprovados. A falta desses insumos não bloqueia o monitoramento.

Este plano é específico da iniciativa. As propostas antigas de retenção de
15/90 dias e de integração JEV de `planDataCollector.md` não são o contrato desta
entrega. Preservar os outros objetivos e alterações existentes nesses arquivos.

## 2. Instruções obrigatórias para o executor

1. Leia este arquivo inteiro e o relatório de evidências indicado na seção 3.
2. Leia `AGENTS.md` aplicável antes de editar cada diretório. A instrução do
   usuário exige commit imediatamente após cada alteração de código.
3. Trabalhe em incrementos pequenos: editar um incremento, inspecionar seu diff
   e fazer commit apenas dos arquivos daquele incremento. Executar os testes
   correspondentes depois do commit; qualquer correção recebe novo commit.
4. Comece por `git status --short --branch`. Existem mudanças de outras tarefas
   no workspace. Não executar `git add .`, `reset`, `checkout --`, `clean` ou
   incluir alterações alheias. Se precisar editar um arquivo já modificado,
   preservar as mudanças existentes e selecionar apenas os hunks desta tarefa.
5. Não assumir a branch atual: ela mudou durante a investigação por trabalho
   concorrente. Não mover, renomear ou reiniciar a branch de outra tarefa.
6. Implementar em ordem T00–T15. Registrar tarefa concluída, commit, testes e
   evidência. Não marcar um conector como validado com apenas teste simulado.
7. Não instalar agentes nem alterar configuração ou infraestrutura de cloud.
   As chamadas de descoberta/coleta desta entrega são somente leitura.
8. Não imprimir chaves, tokens, `config_json` completo ou payload de autenticação.
   Identidades completas e respostas brutas ficam na Bronze/artefatos privados.
9. PostgreSQL guarda configuração, fila, checkpoints e manifests. Amostras,
   inventários históricos e perfis detalhados ficam no Iceberg.
10. A indisponibilidade de credenciais de uma cloud não impede concluir o OCI;
    deve resultar em `NOT_CONFIGURED`, nunca em dados ou validação fictícios.

## 3. Evidências reais obtidas antes de escrever o plano

Artefatos versionados:

- `scripts/pilot_oci_metrics_resolution.py`: piloto limitado, somente leitura.
- `docs/research/oci-metric-resolution-20261023/report.md`: análise medida.
- `docs/research/oci-metric-resolution-20261023/summary.json`: medidas completas.
- `docs/research/oci-metric-resolution-20261023/history_probes.json`: sondagens antigas.

Artefatos locais, ignorados pelo Git:
`apps/data/metric-pilot/oci-resolution-20261023/`. Contêm respostas SDK completas,
inventário privado, chamadas e Parquet. Não copiar essa pasta para Git. Nenhuma
tabela de produção foi escrita pelo piloto.

### 3.1 Escopo medido

- Banco local: uma conexão `oci`; nenhuma conexão Azure/AWS/GCP cadastrada.
- Região: `sa-saopaulo-1`; um compartment; duas VMs RUNNING com CPU publicada.
- Janela comum: `[2026-10-22T11:30:00Z, 2026-10-23T11:30:00Z)`.
- Oito métricas: CPU, memória, I/O de leitura/escrita, bytes de disco e rede.
- Oito estatísticas: mean, min, max, count, sum, P90, P95 e P99.
- Três intervalos/resoluções explícitos: 1, 5 e 15 minutos, sem janelas sobrepostas.
- 384 consultas comparativas: zero erros; 233.472 valores estatísticos retidos.
- Mais quatro sondagens históricas no script e quinze sondagens complementares.
- Total: 403 chamadas `SummarizeMetricsData`, fora descoberta e consultas anteriores.
- Cobertura das séries de médias no piloto: 100% dos buckets esperados.

### 3.2 Resultado de volume e latência

| Medida para as duas VMs/24h | 1 minuto | 5 minutos | 15 minutos |
| --- | ---: | ---: | ---: |
| Consultas comparativas | 128 | 128 | 128 |
| Valores após filtro `[start,end)` | 184.320 | 36.864 | 12.288 |
| JSON serializado local, bytes | 11.863.882 | 2.464.380 | 895.258 |
| Parquet ZSTD local, bytes | 761.826 | 208.384 | 84.561 |
| Soma do tempo nas APIs, segundos | 15,04 | 11,60 | 10,39 |

JSON não é medição de bytes de rede. Parquet é um arquivo experimental com oito
estatísticas em linhas separadas; a Silver proposta reúne essas estatísticas em
colunas. Não extrapolar esses tamanhos como benchmark definitivo de Iceberg.
As chamadas foram sequenciais, com pacing; latências não são um teste de carga.

### 3.3 Evidência de perda de picos

VM anonimizada `vm-1413c3cbc3`, CPU:

| Estatística sobre a série de médias | 1 minuto | 5 minutos | 15 minutos |
| --- | ---: | ---: | ---: |
| P90 (%) | 28,31 | 28,25 | 28,01 |
| P95 (%) | 30,88 | 30,00 | 30,20 |
| P99 (%) | 45,20 | 38,32 | 35,29 |
| Maior média (%) | 94,67 | 75,88 | 51,48 |
| Maior máximo nativo (%) | 97,31 | 97,31 | 97,31 |

O P99 das médias perdeu 15,23% em 5m e 21,92% em 15m. O maior valor da série
de médias perdeu 19,84% e 45,62%. Guardar máximo preservou a altura do maior
pico, mas não sua distribuição, instante sub-bucket ou duração exata.

Conclusão provisória: manter 1m como referência de informação no piloto OCI;
derivar 5m/15m localmente quando possível. Não escolher 15m global apenas por
economia de armazenamento. Duas VMs/um dia não validam sazonalidade nem frota.

### 3.4 Evidência histórica que muda o planejamento

Nesta conta, uma consulta de CPU com intervalo/resolução de 1m em uma janela de
uma hora de **59 dias atrás** retornou 60 valores distintos; `count()` retornou
seis observações por minuto. P90/P95/P99 nativos e memória foram sondados nessa
mesma data, com resultados no arquivo de evidência.

Consultas de 5m com 31 dias e de 15m com 31/35/59 dias falharam com HTTP 400,
`InvalidParameter`, indicando intervalo de 300s no erro. Não é falha de IAM.

Não transformar a tabela resumida da documentação OCI em um corte universal
de idade. Separar duração máxima de uma requisição, idade do ponto, resolução
solicitada e resolução efetivamente observada. Também não concluir que todas
as métricas/VMs têm 60 dias de 1m a partir dessas sondagens curtas. O backfill
deve consultar chunks pequenos, validar timestamps/cadência e registrar lacunas.

## 4. Estado do repositório e pontos de integração

| Local existente | Estado observado e uso planejado |
| --- | --- |
| `apps/api/app/services/silver_models.py` | `METRIC_SERIES`, inventário e perfis já declarados; ampliar, não criar uma tabela por cloud. |
| `apps/api/app/services/silver_normalizer.py` | `SourceMapping` declarativo; neste checkout não há mapping ativo de métricas de VM. Criar mappings e transformações semânticas versionadas. |
| `apps/api/app/services/oci_operational_monitoring.py` | AI/GPU; coleta Compute limitada a shapes GPU e persiste em destino próprio. Manter esse fluxo; usar novo source_key de infraestrutura. |
| `apps/api/app/services/azure_source_registry.py` | Catálogo Azure restrito neste checkout; `is_telemetry()` retorna false. Não presumir que o plano antigo de Azure está implementado. |
| `apps/api/app/services/iceberg_service.py` | `write_silver()` faz append. Há staging/publicação por fatia; validar escopo e nomes das colunas antes de reutilizar. |
| `apps/api/app/services/destination_slice.py` | Replace por período/conexão, sem toda a identidade de série. Não chamar cegamente para uma carga parcial de métricas. |
| `apps/api/app/services/metric_window.py` | Contrato UTC de dias; reutilizar com configuração de 60 dias sem mudar defaults de outros módulos. |
| `apps/api/app/services/lakehouse_layers.py` | Resolver namespaces por esse módulo; catálogo Trino depende do ambiente. |
| `apps/api/app/models/etl_task.py`, `worker.py`, `services/task_runner.py` | Fila, progresso, cancelamento/recovery. Integrar tarefa tipada, evitando novo loop em memória na API. |
| `apps/api/app/services/scheduler_service.py` | Scheduler somente enfileira o novo job; não executar backfill dentro do processo HTTP. |
| `apps/api/app/core/modules.py`, middlewares de autorização/auditoria | Registrar `infrastructure_metrics` e escopo de conexão/organização. |
| `apps/web/src/services/api.ts`, páginas Connections/Extraction | Reutilizar padrões de configuração, progresso e navegação; preservar trabalho concorrente. |

Os modelos de perfil não provam que exista motor de cálculo operacional neste
checkout. Confirmar serviços, rotas e tabelas reais em T00, sem restaurar código
removido apenas porque ele é citado em documentação antiga.

## 5. Arquitetura e fronteiras

```text
Credenciais existentes + configuração/allowlist de infraestrutura
  -> descoberta limitada + catálogo de métricas/capabilities
  -> planejamento de chamadas + orçamento + fila/checkpoint
  -> adaptador OCI/Azure/AWS/GCP
  -> Bronze imutável (payload + requisição sanitizada + proveniência)
  -> normalização tipada e publication atômica
  -> Silver compartilhada:
       resource_metric_series
       resource_inventory + capacidades/relações com vigência
       resource_metric_coverage
       resource_profile_daily_window ampliado
  -> Gold: resumo de cobertura e capacidade por janela
  -> API/tela: consumo, P90/P95/P99, picos, qualidade e comparação
```

APIs não consultam a cloud a cada abertura de dashboard. Séries detalhadas
ficam na Silver; Gold guarda resumos finais, sem duplicar a série detalhada.
Catálogo de métricas por provider é configuração de aquisição e semântica; não
é um modelo Silver diferente para cada provedor.

## 6. Contrato dos percentis e preservação de informação

### 6.1 Três resultados distintos

1. **Percentil nativo por bucket:** retorno do provedor dentro de um intervalo.
   Guardar `native_p90`, `native_p95`, `native_p99`, intervalo, resolução,
   definição, suporte e referência da resposta bruta. Pode ser estimado.
2. **Percentil da série por janela:** cálculo local de P90/P95/P99 dos valores
   canônicos em buckets regulares, com resolução explicitada. Exemplo de rótulo:
   `P95 das médias de CPU de 1 minuto nos últimos 30 dias`.
3. **Percentil da distribuição de observações:** somente se houver observações
   individuais ou histogramas/sketches combináveis adequados. Registrar método,
   versão, esquema de buckets/erro e o fato de ser uma estimativa quando aplicável.

Proibido: média de percentis; P95 de P95 tratado como P95 global; usar `max()` de
P95 como percentil da janela; dar à estatística sobre médias o rótulo de
percentil exato das observações originais.

Para pequenos conjuntos, usar quantil linear sobre a série regular e registrar
`quantile_method=linear`, implementação/versão e entradas ordenadas. Para escala,
avaliar método aproximado com tolerância e fixtures antes de adotá-lo. Não mudar
silenciosamente de método entre recursos ou execuções.

P90/P95/P99 nativos opcionais ficam nulos com motivo quando não suportados.
Os percentis locais continuam disponíveis sobre uma série observada válida.
`native_p95` e `window_p95` não são campos intercambiáveis.

### 6.2 Resoluções e rollup

- Aquisição preferencial provisória: 1m onde suportado e aprovado pelo orçamento.
- Derivar 5m e 15m a partir da base; não consultar as três resoluções para toda a
  frota. As consultas triplas foram experimentais.
- Guardar separadamente `source_sample_period_seconds`, intervalo de agregação
  e passo da série (`resolution_seconds`). Na OCI, seis observações por minuto
  não significam seis observações brutas recuperáveis do payload de médias.
- Alinhar buckets por epoch UTC e usar `[start,end)`. Filtrar pontos extras da
  resposta sem somar o mesmo bucket em páginas/chunks sobrepostos.
- Média combinada: `sum(value_total)/sum(sample_count)` somente quando ambos
  representam as mesmas observações e o catálogo declara essa relação válida.
  Sem denominador confiável, usar média ponderada pelo tempo com rótulo próprio.
- `min=min(min)`, `max=max(max)`; contagens/totais só somam quando aditivos e
  não sobrepostos. `sum()` de um percentual não é consumo acumulado.
- Percentis dos buckets maiores podem ser calculados localmente sobre médias
  menores, com essa base identificada; percentis nativos maiores não podem ser
  reconstruídos exatamente a partir de percentis nativos menores.
- Armazenar base, estatísticas nativas e derivados com `origin_kind`, linhagem,
  política de resolução e versão. Perfis devem escolher uma única série base;
  não misturar 1m com seus rollups como novas amostras.
- Históricos de resolução mista geram segmentos/perfis separados. Perfil misto
  opcional precisa ponderação temporal explícita e não se compara ao de 1m.

## 7. Ampliação da Silver compartilhada

### 7.1 `resource_metric_series`: uma linha larga por série/bucket

Manter os campos atuais, acrescentar colunas de forma aditiva e migrar leitores.
Não fazer DROP/recriação da tabela canônica. Tipos abaixo são do modelo lógico;
converter para tipos Arrow/Iceberg explicitamente, com UTC e precisão definida.

| Grupo | Campos a manter/adicionar | Regra |
| --- | --- | --- |
| Proveniência | `_source_connection_id`, `_silver_owner`, `collection_run_id`, `request_id`, `_bronze_blob_path`, `payload_hash`, `statistic_lineage_json`, `mapping_version`, `schema_version` | Sempre rastrear a fonte e o payload; request ID não substitui a chave da série. |
| Identidade cloud | `cloud_provider`, `cloud_partition`, `provider_account_id`, `sub_account_id`, `region_id`, `resource_id`, `resource_type` | Providers enumerados; IDs conservados na Bronze e normalizados por regras do provider. |
| Identidade métrica | `metric`, `metric_namespace`, `source_metric_name`, `metric_scope`, `dimensions_json`, `dimensions_hash`, `dimension_schema_version` | CPU da VM, disco por volume e agregados da VM são escopos diferentes. |
| Tempo | `bucket_start_utc`, `bucket_end_utc`, `aggregation_interval_seconds`, `resolution_seconds`, `source_sample_period_seconds`, `observed_at_utc` | Não inferir resolução apenas pelo timestamp ou pela partição. |
| Valores básicos | `value`, `value_average`, `value_minimum`, `value_maximum`, `value_total`, `sample_count`, `value_rate` | Campos não suportados são NULL; `sample_count` continua double. |
| Percentis nativos | `native_p90`, `native_p95`, `native_p99`, `native_quantile_method`, `native_quantile_basis` | Mesma janela e série dos valores básicos; estimativa sinalizada. |
| Semântica | `metric_unit`, `source_unit`, `unit_scale`, `metric_kind`, `canonical_value_statistic`, `semantic_version`, `source_descriptor_hash` | Gauge/delta/cumulative e bytes/bytes_s/GiB/percent/fração não se misturam. |
| Qualidade | `quality_status`, `quality_reason`, `statistics_status_json`, `source_resolution_verified`, `origin_kind` | Suporte por estatística; erro de uma consulta de P99 não apaga a média válida. |
| Distribuição opcional | `distribution_json`, `distribution_schema_hash`, `quantile_sketch_format`, `quantile_sketch_version` | Só preencher se veio da fonte ou foi produzido por método implementado/auditado. |
| Linhagem/partição | `parent_series_id`, `parent_resolution_seconds`, `capacity_snapshot_id`, `_billing_period` | Para métricas, período diário `YYYYMMDD`; calendário financeiro continua distinto. |

Chave física/lógica proposta:

```text
(_source_connection_id, _silver_owner, cloud_provider, cloud_partition,
 provider_account_id, sub_account_id, region_id, resource_id, metric_namespace, source_metric_name,
 metric_scope, dimensions_hash, dimension_schema_version,
 aggregation_interval_seconds, resolution_seconds, origin_kind, bucket_start_utc)
```

`series_id` é SHA-256 da identidade sem `bucket_start_utc`, com serialização
canônica versionada. Não usar nome de exibição. `dimensions_hash` usa chaves
identificadoras declaradas pelo descriptor; o JSON completo preserva também os
rótulos descritivos. Não remover dimensões de disco/NIC para reduzir cardinalidade.
Quando a identidade do provider for desconhecida, preservar todas as dimensões
e marcar o descriptor para revisão, em vez de fundir streams distintos.

`statistic_lineage_json` relaciona cada coluna estatística com request ID,
payload hash e caminho Bronze que a produziu. Como a OCI exige consultas
distintas para as oito estatísticas, uma linha larga não pode descartar sete
requisições de proveniência. Guardar o cabeçalho de unidade e os campos adicionais
do stream no payload Bronze, mesmo quando ainda não mapeados para a Silver.

Usar `double` nas medidas contínuas já retornadas como float, `bigint` em
resoluções/versões inteiras, JSON canônico nos atributos e timestamps UTC com
precisão declarada. Valores inteiros/decimais de origem que exigem precisão maior
devem conservar representação tipada/textual no raw e conversão sem truncamento.
Não converter indiscriminadamente contadores grandes para float intermediário.

`provider_account_id` identifica a conta/tenancy/projeto relevante da API;
`sub_account_id` deve seguir o mapeamento financeiro realmente existente. Na OCI,
conferir tenancy/compartment contra o FOCUS local antes de associar custo. Manter
compartment/project/subscription e identidades organizacionais em campos ou
atributos explícitos; não inferir sua equivalência por nomes parecidos.

Conexões diferentes permanecem isoladas fisicamente. Se duas credenciais
enxergam o mesmo recurso, uma regra explícita escolhe a fonte canônica para a
análise; nunca contar ambas como carga adicional.

### 7.2 Capacidades e relações com vigência

Ampliar `resource_inventory` com CPU/OCPU/vCPU, arquitetura, RAM bytes, limites
de IOPS/throughput/rede quando documentados, tamanho de volumes e proveniência.
Manter `valid_from`, acrescentar `valid_to`/snapshot ID e `raw_attributes_json`.
Uma fotografia atual não prova capacidade existente há 59 dias.

Criar `resource_relationship_snapshot` somente se não houver modelo equivalente
após T00: origem, destino, tipo `ATTACHED_TO`, região/AD, vigência e evidência.
Servirá para ligar VM–volume–VNIC. Relação ausente não vira inferência por nome.
Filesystem usado/livre depende de telemetria guest; volume provisionado não
informa filesystem ocupado.

CPU em % não é vCPU consumida. Converter para núcleos equivalentes somente com
capacidade válida no instante e compatibilidade de definição. OCPU→vCPU depende
da arquitetura/shape; não multiplicar tudo por dois. Memória usada deve informar
se inclui cache/buffers; não unificar definições diferentes sem marcação.

### 7.3 `resource_metric_coverage`

Tabela Silver por conexão/série/dia UTC/resolução/origem. Campos mínimos:
janela pedida/efetiva, buckets esperados, observados, duplicados, ausentes,
primeiro/último ponto, maior lacuna, gap spans, estatísticas disponíveis, estado,
motivos e run ID. Usar linhas de cobertura mesmo quando não existe amostra.

Estados de motivo: `NOT_CONFIGURED`, `NOT_AUTHORIZED`, `NOT_SUPPORTED`,
`NOT_EMITTED`, `OUTSIDE_AVAILABLE_HISTORY`, `THROTTLED`, `SOURCE_ERROR`,
`SOURCE_EMPTY_UNKNOWN`, `RESOURCE_STOPPED`, `PARTIAL_WINDOW`, `UNKNOWN_SEMANTICS`.
Só usar `RESOURCE_STOPPED` com evidência de estado no período. HTTP 404 da OCI
não distingue sozinho permissão de inexistência. Não converter ausência em zero.

Não gerar bilhões de linhas artificiais GAP para cada minuto vazio; cobertura
e intervalos de lacunas preservam o diagnóstico com volume controlado.

Denominador: intervalo esperado dentro da janela efetiva e da vida conhecida do
recurso, segundo a cadência disponível da fonte. Fonte que só publica 5m não
passa a ter lacunas de 80% por ser consultada em 1m: registrar resolução nativa
e não promover sua qualidade para 1m. `sample_count` do provedor não substitui
quantidade de buckets nem duração observada.

Na ausência de história de criação/estado confiável, mostrar cobertura da janela
pedida e cobertura do intervalo observável separadamente. Perfis podem ser
exibidos com dados parciais, mas devem carregar restrição de uso em análise de
capacidade. Definir limites de cobertura/máxima lacuna por política, versionados,
antes de permitir que perfis alimentem sugestões acionáveis.

### 7.4 `resource_profile_daily_window` ampliado

Reutilizar o modelo existente para perfis multicloud, sem depender de custo.
Adicionar provider/account, `series_id`, dimensões, escopo, resolução,
estatística/base analisada, início/fim exatos, P90, P95, P99, min/max/avg, método,
amostras, duração observada, cobertura, maior lacuna, percentuais/tempos acima
de limiares, headroom quando houver capacidade, versões e `profile_hash`.

Chave deve incluir conexão/fonte, série, resolução, base estatística, janela,
versão de algoritmo e política. O hash inclui versões dos snapshots de entrada;
timestamp de execução não participa da decisão. Custos ficam NULL quando não
associados e não são pré-requisito de perfil. Não usar chaves mensais antigas
para representar duas janelas distintas do mesmo mês.

## 8. Contrato dos adaptadores multicloud

Criar `apps/api/app/services/infrastructure_metrics/` com contratos pequenos:

```text
contracts.py           # DiscoveryResult, MetricDescriptor, QueryPlan, QueryResult
registry.py            # associação provider -> adaptador/capabilities
credentials.py         # referência a SourceConnection e resolução segura existente
catalog.py             # catálogo declarativo de métricas, unidades e dimensões
planner.py             # janela, chunk, orçamento e precedência de resolução
collector.py           # orquestração/checkpoint; sem código de provider
normalization.py       # semântica e mapeamentos que alimentam o normalizador comum
publication.py         # merge/publicação atômica e isolamento
coverage.py            # denominadores, lacunas e motivos
rollups.py             # agregações 5m/15m com linhagem
profiles.py            # P90/P95/P99 e fatos de capacidade
retention.py           # expiração restrita aos dados desta iniciativa
providers/oci.py       # primeiro adaptador validado ao vivo
providers/azure.py     # só marcar operacional após teste com credencial real
providers/aws.py
providers/gcp.py
```

Não criar arquivos vazios para satisfazer a lista; contratos e implementação
devem surgir na tarefa correspondente. Não criar um engine por nuvem.

Interface mínima:

```python
discover(scope) -> DiscoveryResult
describe_metrics(resource_ids) -> list[MetricDescriptor]
plan_queries(scope, descriptors, window, policy) -> list[QueryPlan]
fetch(query) -> QueryResult
```

`MetricDescriptor`: nome/namespace, unidade, kind, dimensões, cadência,
agregações/percentis suportados, escopo, limites conhecidos de consulta/histórico,
data de verificação e origem documental/empírica. `QueryResult` inclui requisição
sanitizada, cabeçalhos permitidos, status, páginas, tentativas, latência, volume,
contagem de streams e payload completo, sem segredo.

### 8.1 Matriz de aquisição

| Provider | Inventário e métricas | Cuidados obrigatórios |
| --- | --- | --- |
| OCI | Compute/Blockstorage/VNIC + Monitoring `oci_computeagent`, `oci_blockstore`, `oci_vcn` | Paginação; region/compartment/AD; intervalo e resolution explícitos; boot volume e attachment podem exigir permissões adicionais; testar API correta no ComputeClient para attachments. |
| Azure | Resource Graph/Compute + Azure Monitor Metrics | Consultar metricDefinitions e agregações realmente suportadas; API Metrics usual retorna Avg/Min/Max/Total/Count, sem assumir percentil arbitrário; percentis locais das séries. |
| AWS | EC2/EBS + CloudWatch | Basic monitoring pode ser 5m; 1m não é garantido. Descobrir dimensões. Percentis nativos dependem da publicação/statistic sets e valores elegíveis. Memória guest depende de agente ou fonte adicional existente. |
| GCP | Compute + Cloud Monitoring | Preservar metric/resource labels e intervalo start/end; gauge/delta/cumulative; não confundir aligner temporal com reducer entre recursos; preservar distribuições/histogramas quando fornecidos. |

OCI: os nomes corretos publicados no piloto são `NetworksBytesIn` e
`NetworksBytesOut`, com **Networks**. O coletor AI/GPU existente usa variantes
diferentes; não copiá-las para o catálogo de infraestrutura sem descriptor.

IOPS/bytes da VM são agregados de recursos associados; `VolumeReadOps` e
`VolumeWriteOps` descrevem o volume. Não somar os dois escopos. Contadores
cumulativos exigem delta/rate com tratamento de reset e lacunas; a semântica
vem do descriptor documentado e verificado, não apenas do nome da métrica.

### 8.2 Permissões e ativação

OCI já validou leitura de metrics/instances/volumes/volume-attachments. Descobrir
boot volumes, shapes e VNICs separadamente e reportar permissões faltantes. Não
pedir `manage all-resources`. Azure usa permissões de leitura/monitoramento e
inventário do escopo; AWS lista explicitamente ações necessárias; GCP usa
Monitoring Viewer e Compute Viewer no escopo. A política final de cada adaptador
deve corresponder às chamadas implementadas, sem ampliar escopo automaticamente.

## 9. Configuração e controle da coleta

Criar metadata `infrastructure_metric_collection_config`, referenciando a
`SourceConnection` existente; não duplicar credenciais em outra conexão.
Campos mínimos:

```json
{
  "enabled": false,
  "provider": "oci",
  "source_connection_id": "ID real, resolvido pela API",
  "source_key": "oci_infrastructure_metrics",
  "regions": ["sa-saopaulo-1"],
  "resource_allowlist": ["IDs completos reais, guardados só em metadata privada"],
  "max_resources": 2,
  "history_days": 60,
  "acquisition_resolution_seconds": 60,
  "derived_resolutions_seconds": [300, 900],
  "native_percentiles": [0.90, 0.95, 0.99],
  "statistics": ["average", "minimum", "maximum", "sum", "count"],
  "poll_interval_minutes": 15,
  "settling_delay_minutes": 15,
  "overlap_minutes": 60,
  "max_workers": 2,
  "max_requests_per_second": 4,
  "max_attempts": 4,
  "schedule_enabled": false
}
```

São defaults do piloto a validar, não limites universais dos provedores. Os
campos `max_api_requests_per_run`, `max_returned_points_per_run`,
`max_response_bytes_per_run` e orçamento monetário opcional devem ser obrigatórios
na ativação; calcular proposta de orçamento no dry-run e persistir os valores.
Não interpretar ausência de preço monetário como orçamento ilimitado de API.

Allowlist vazia deve impedir execução até o operador selecionar recursos.
Descoberta pode listar candidatos paginados, mas não altera a allowlist. Limitar
também o fanout de volumes/VNICs: no piloto, até um volume adicional por VM e
um teto explícito de recursos filhos, com estado truncado visível.

Ativação gradual: duas VMs OCI -> sete dias de comparação -> até dez VMs por
cloud com credencial -> decisão de expansão documentada. Não iniciar expansão
apenas porque as policies abrangem toda a tenancy.

## 10. Janelas, backfill e retenção de 60 dias

### 10.1 Definição de histórico

`history_days=60` significa janela de negócio `[as_of_utc - 60d, as_of_utc)`;
`as_of_utc` é congelado no manifest do run. Partições são dias UTC, mas o filtro
final usa timestamps exatos. UI mostra `requested_days`, `observed_days`,
resoluções e cobertura. Nunca substituir uma janela ausente por zeros.

Disponibilidade retroativa é diferente da nossa retenção. Podemos acumular
60 dias próprios em 1m quando o provedor só mantém parte disso acessível pela
API. Não é permitido recriar minutos antigos por interpolação e marcá-los como
observados. Backfill não garante 60 dias de dados para um recurso criado ontem.

### 10.2 Backfill OCI inicial

1. Congelar allowlist de duas VMs e janela de 60 dias no run.
2. Descobrir descritores, limites e suporte por estatística.
3. Sondar dias 1, 7, 14, 30, 45 e 59 com chamadas curtas, antes de varrer tudo.
4. Consultar dados por dia UTC; subdividir para horas quando houver limite de
   pontos/duração/bytes. Nunca uma chamada cega com 60 dias em 1m.
5. Priorizar capturar o histórico fino próximo de expirar; dar também prioridade
   ao incremental recente para não criar novas lacunas durante o backfill.
6. Resolver incompatibilidade por regra explícita de QueryPlan: reduzir chunk,
   testar resolução disponível e registrar o fallback. Manter os dados de 1m
   antigos já encontrados; não substituí-los por uma heurística de retenção.
7. Publicar cada fatia validada e checkpoint após commit da fatia; retomar sem
   duplicação. Comparar primeiro/último ponto e cadência real com a requisição.
8. Fazer reconciliação por série/dia/estatística e manifest de conclusão parcial.

Não coletar esse backfill agora só para produzir o plano. Ele é T08 da execução.

### 10.3 Retenção em todas as camadas

- Bronze: reter payload necessário para reprocessar os 60 dias; expirar chunks
  cujo conteúdo inteiro esteja fora da janela. Para chunks mistos, estratégia
  de particionamento/regravação deve preservar o que permanece na janela.
- Silver: reter base, estatísticas nativas, derivados e cobertura da janela.
- Gold: materializar apenas janelas cujas entradas ainda têm rastreabilidade;
  snapshots antigos de análise têm versão e política de retenção explícita.
- Inventário: preservar o último snapshot anterior ao início da janela quando
  necessário para determinar a capacidade válida no primeiro bucket.
- Iceberg: DELETE lógico não remove arquivo antigo referenciado por snapshot.
  Implementar expiração de snapshots e limpeza de órfãos após grace operacional,
  com locks, dry-run e confirmação de que nenhuma escrita os referencia.
- O cutoff lógico é obrigatório na API. Limpeza física pode ter grace de
  recuperação explicitada no manifest; não prometer purge instantâneo.
- Limpeza filtra owner/conexão/tabelas da iniciativa; não apagar custo FOCUS,
  snapshots de outras fontes ou histórico AI/GPU. Nunca apagar o diretório
  inteiro de warehouse para executar uma retenção.

## 11. Confiabilidade de execução e publicação

Usar `etl_task` com tipo `infrastructure_metrics`, parâmetros validados e
checkpoint por conexão/provider/recurso/descriptor/estatística/janela/página.
O endpoint retorna task ID; scheduler só enfileira. Persistir progresso real,
tentativas, bytes, pontos e fase para o supervisor existente.

429/5xx/timeouts: exponential backoff com jitter, respeitar Retry-After e teto
do run. 400 semântico, 401/403 e 404 persistente não entram em retry cego.
Cancelar entre chunks, preservando fatias concluídas; retomada usa o manifest.
Não usar retries do SDK somados aos do orquestrador sem contabilizá-los.

Publicação precisa ser atômica no escopo correto. O normalizador atual faz
delete e append e não basta para garantir integridade desta coleta. Adaptar o
staging/merge existente depois de verificar esquema e isolamento.

Para carga parcial, construir a união do conteúdo já publicado e da nova fatia,
deduplicar pela chave completa e substituir atomicamente apenas a fatia
conexão/owner/dia/partição sob lock; ou implementar merge transacional equivalente
suportado pelo runtime. Não apagar um dia inteiro ao atualizar uma única VM,
resolução ou estatística. Staging inválido/erro mantém a versão anterior.

Caso obrigatório de concorrência: atualização da VM A, com B no mesmo dia,
mantém B; outra conexão do mesmo provider mantém seus dados; rollup não apaga
base; duas escritas simultâneas não perdem atualização.

## 12. Como medir e escolher a resolução

Rodar a mesma janela, o mesmo recurso e a mesma definição por resolução.
No piloto estendido, usar 7 dias com as duas VMs, e depois até dez recursos de
cada cloud configurada. Incluir pelo menos um recurso com picos, um estável e
um caso de telemetria incompleta quando houver essa diversidade disponível.

Medir por provider/recurso/métrica/resolução:

- chamadas lógicas e HTTP, páginas, retries, 429, latência e tempo total;
- pontos retornados/retidos, streams e dimensões, JSON serializado e bytes de
  rede quando realmente observáveis; não confundir essas medidas;
- Bronze real, Silver real e Parquet, codec, row groups, arquivos pequenos,
  snapshots e custo de compaction. Separar dados de overhead fixo;
- P90/P95/P99 das médias, máximos nativos, erro absoluto/relativo versus 1m,
  minutos acima de limiares e perda de episódios de pico;
- cobertura, maior lacuna e custo da materialização/consulta de 7/30/60 dias;
- estimativa monetária com unidade de cobrança, preço/data/moeda/origem,
  franquia restante desconhecida e hipótese de uso compartilhado da conta.

Número de chamadas não equivale a custo. No OCI, comparar unidades de datapoints
conforme cobrança; na AWS, avaliar métricas requisitadas/chamadas por endpoint;
na Azure, distinguir API e batch; no GCP, aplicar a modalidade de cobrança do
endpoint. Se billing real não estiver disponível, apresentar custo estimado ou
`UNKNOWN`, nunca `0` apenas porque o piloto é pequeno.

Fórmula de capacidade para uma base larga com 8 métricas por VM:

```text
rows_60d = VMs * 8 * 60 * 1440 / resolução_em_minutos
1.000 VMs: 691.200.000 linhas em 1m;
           138.240.000 linhas em 5m;
            46.080.000 linhas em 15m.
```

Não multiplicar por oito estatísticas se elas são colunas da mesma linha.
Multiplicar pela cardinalidade efetiva de dimensões/volumes/NICs e contar
rollups separadamente. Histogramas, raw e snapshots aumentam o armazenamento.

Critério provisório para avaliar 5m/15m: erro relativo de P90/P95/P99 até 5% em
cada série elegível, preservação do máximo por coluna max e nenhuma perda de
episódio sustentado que altere uma futura guarda de capacidade. Esse 5% é um
critério de experimento, não garantia universal de segurança. Se a referência é
próxima de zero, comparar erro absoluto e marcar relativo como não aplicável.
As séries OCI medidas já falham nesse critério de P99 em 5m e 15m.

## 13. API, RBAC e experiência do módulo

Prefixo proposto `/infra/metricas`, módulo RBAC `infrastructure_metrics`.
Adequar nomes ao roteamento efetivo em T00 sem criar duas rotas equivalentes.

| Endpoint | Responsabilidade |
| --- | --- |
| `GET /configuracoes` | Configurações e status por conexão/provider autorizados. |
| `PUT /configuracoes/{id}` | Allowlist, resolução, 60 dias, limites e agenda. |
| `POST /descobrir` | Lista paginada de recursos/capabilities; não habilita coleta. |
| `POST /planejar` | Dry-run: recursos, chamadas/pontos/bytes esperados e limitações. |
| `POST /coletar` | Enfileira escopo validado, retorna task ID. |
| `GET /execucoes/{id}` | Progresso, cobertura, erros e manifest sanitizado. |
| `GET /series` | Silver detalhada, filtrada, paginada e com teto de pontos. |
| `GET /perfis` | P90/P95/P99, máximos, cobertura e método por janela. |
| `GET /comparar-resolucoes` | Mesmo escopo/janela, erros e volume de cada resolução. |

Obrigatório filtrar conexão/organização no servidor; não confiar em ID recebido
sem autorização. Datas parametrizadas, nomes de métricas/agregações em allowlist,
intervalo máximo de 60 dias, sem SQL livre do browser. Não hardcodar conexão 1.

Tela única inicial: filtro provider/conexão/recurso/métrica/janela/resolução;
cards P90/P95/P99/máximo e cobertura; gráfico de consumo com máximo; tabela de
gaps; comparação 1/5/15; custos da coleta medidos/estimados separados. Mostrar
`P95 das médias de 1m` versus `P95 nativo por bucket`. Resolução insuficiente,
telemetria ausente e janela parcial precisam ser visíveis.

O dashboard resume Gold e abre detalhe Silver paginado. Não executar agregações
de toda a frota em cada interação. Não criar abas vazias de JEV/savings.

## 14. Plano de implementação em tarefas pequenas para GPT Luna

### T00 — Revalidar o estado real e inventariar dependências

**Entrada:** este plano, relatório, `git status`, instruções aplicáveis.
**Fazer:** listar conexões sem secrets; inspecionar modelos/catálogos/mappings,
rotas e queue handlers citados na seção 4; verificar tabelas físicas e esquema
por metadados, sem scan completo. Registrar fontes realmente ativas, branch e
arquivos alterados por terceiros. Confirmar catálogo Trino por configuração.
**Saída:** checklist do estado inicial com divergências do plano.
**Aceite:** zero escrita de cloud/lakehouse; nenhuma suposição de Azure existente.

### T01 — Fixar semântica, identidades e contratos de aquisição

**Arquivos:** novo pacote `infrastructure_metrics/contracts.py`, `catalog.py`,
`registry.py`; modelos existentes lidos, ainda sem migração destrutiva.
**Fazer:** enums/DTOs tipados, registry de métricas OCI do piloto, algoritmo
versionado de series_id/dimensions_hash e regras de unidade/kind/escopo.
**Teste necessário:** fixtures de duas NICs/discos não colidem; ordem de labels
não muda hash; providers/conexões/regiões distintos não colidem; percentuais e
frações convertem por descriptor; unidade desconhecida não recebe conversão.
**Aceite:** oito métricas OCI têm definição e base canônica explícitas.
**Commit sugerido:** `Define multicloud infrastructure metric contracts`.

### T02 — Configuração, segurança e dry-run de escopo

**Arquivos:** novo model de configuração, migration no mecanismo real do projeto,
schemas/rotas do módulo, registro RBAC e resolução de credenciais existente.
**Fazer:** vínculo com SourceConnection; allowlist/tetos; enabled/schedule false;
auditoria de alteração; descobrir provider por metadata explícita; dry-run.
**Teste:** allowlist vazia rejeitada; conexão não autorizada rejeitada; secrets
não retornados; descoberta não amplia escopo; provider inexistente é erro tipado.
**Aceite:** configuração para duas VMs é revisável sem iniciar coleta.
**Commit:** dividir modelo/configuração e rotas/RBAC em incrementos separados.

### T03 — Evolução aditiva dos modelos Silver

**Arquivos:** `silver_models.py`, mappings, migration/registro de tabelas.
**Fazer:** campos/chaves da seção 7; coverage; relações/capacidades; evolução
física e lógica com relatório de schema diff. Mapear legado como
`LEGACY_UNVERIFIED` quando resolução/base não puder ser provada.
**Teste:** leitura das linhas antigas permanece possível; novas colunas
preservadas no Arrow; count fracionário não é truncado; nenhuma tabela por nuvem.
**Aceite:** contrato e esquema físico concordam sem apagar histórico.
**Commit:** cada modelo/migração é um incremento verificável.

### T04 — Publicação atômica e idempotência antes de dados reais

**Arquivos:** `infrastructure_metrics/publication.py`; helpers existentes apenas
quando necessário e sem alterar consumidores que usam outro contrato.
**Fazer:** staging, validação, locks e replace/merge na chave completa; preservar
fatias não tocadas. Rejeitar duplicatas conflitantes e liberar staging seguro.
**Teste de integração:** A/B/conexão2/1m/5m no mesmo dia; falha antes do commit;
duas escritas concorrentes; rerun da mesma resposta; P99 ausente não apaga média.
**Aceite:** falha mantém versão anterior e execução repetida não muda contagem.
**Commit:** `Publish metric slices atomically with source isolation`.

### T05 — Adaptador OCI de infraestrutura

**Arquivos:** `providers/oci.py`, registry, mappings declarativos OCI.
**Fazer:** descobrir apenas allowlist; paginação; snapshots de capacidade e
attachments; queries com intervalo/resolution explícitos e resourceId; preservar
streams/dimensões/estatísticas. Capacidades de volume/boot/VNIC opcionais com
status. Nenhuma dependência de generative_ai_compartment_ids ou shape GPU.
**Teste:** SDK fixtures para paginação/429/404/400; NetworksBytes*; múltiplos
streams; endpoint de attachment correto; coleta de VM CPU comum.
**Aceite vivo:** duas VMs, janela pequena, oito métricas no que estiver emitido,
percentis suportados preservados. Fluxo AI/GPU continua funcional.
**Commit:** implementar aquisição e mapeamento em incrementos separados.

### T06 — Fila, checkpoints, orçamento e observabilidade

**Arquivos:** collector/planner, task_runner/worker/scheduler e processing logs.
**Fazer:** tipo de tarefa, limites, backoff, cancelamento, dry-run quantitativo,
checkpoint só após publicação; task status distingue partial/failed/completed.
**Teste:** cancelamento/retomada não duplica; budget interrompe com motivo e
retoma; 400 permanente não repete quatro vezes; 429 respeita Retry-After.
**Aceite:** reiniciar worker no meio do piloto preserva fatias e progresso.
**Commit:** separar integração de fila da integração de agenda.

### T07 — Piloto Bronze→Silver restrito de 24h

**Entrada:** T01–T06 concluídas; mesma allowlist de duas VMs resolvida privadamente.
**Fazer:** coletar 24h em 1m com estatísticas suportadas, registrar bytes reais
Bronze/Silver e manifests; comparar com script de pesquisa; verificar nomes,
unidades, tempos, contagens, gaps e raw lineage. Sem ativar agenda global.
**Teste:** amostrar de volta Bronze e Silver e reconciliar todas as identidades;
rerun mantém mesma quantidade lógica de buckets.
**Aceite:** relatório reproduzível e nenhuma conexão/recurso fora do escopo.
**Commit:** versionar só configuração de exemplo e relatório sanitizado.

### T08 — Backfill e retenção de 60 dias

**Arquivos:** planner/retention/checkpoints + manutenção Iceberg restrita.
**Fazer:** sondar histórico conforme seção 10; backfill das duas VMs em chunks
limitados, sem bloquear incremental; implementar cutoff lógico e dry-run de
purge; lifecycle de snapshots/órfãos com grace e lock.
**Teste:** recurso criado recentemente; buraco de 7 dias; 1m antigo disponível;
400 em 15m; cutoff atravessa mês/ano; purge não toca custo nem outra conexão.
**Aceite:** 60 dias pedidos, dias efetivos declarados e qualidade por segmento;
nenhuma interpolação inventada; espaço físico reportado separadamente do lógico.
**Commit:** backfill e retenção em alterações separadas.

### T09 — Rollups e perfis P90/P95/P99

**Arquivos:** rollups/profiles/coverage, `RESOURCE_PROFILE_DAILY_WINDOW` e mappings.
**Fazer:** 5m/15m derivados; percentis locais com base/algoritmo explícitos;
max/min/sum/count; duração/episódios; headroom quando capacidade válida.
**Teste:** pico de um minuto; série constante; zeros legítimos; gaps; janela
parcial; count fracionário; counter reset; mudanças de capacidade; série de
5m com 1m derivado ilegítimo; percentis nativos não combináveis.
**Aceite:** P90<=P95<=P99 dentro da mesma distribuição; mesmo input/hash produz
mesmo perfil; nenhum perfil considera 1m e rollup como amostras adicionais.
**Commit:** rollup e perfil têm commits/testes separados.

### T10 — Validação de sete dias e decisão de resolução

**Fazer:** ampliar a janela das mesmas duas VMs para sete dias observados quando
disponíveis; consultar 5/15m nativos apenas no escopo comparativo necessário;
medir erro e custos conforme seção 12, incluindo tamanho Silver largo real.
**Saída:** ADR com resolução por categoria, exceções, orçamento e informação
perdida. Proposta atual de 1m só vira default amplo com esse relatório.
**Aceite:** reprovação de um critério mantém resolução fina naquele escopo;
nenhuma escolha global deduzida apenas de uma VM estável.

### T11 — API e Gold do módulo

**Arquivos:** router `infrastructure_metrics.py`, registro em main, schemas,
materializador Gold e autorização/auditoria nos padrões atuais.
**Fazer:** rotas da seção 13, queries parametrizadas com pruning e limites,
reuso de cache/filtros existentes quando compatível. Gold usa Silver publicada.
**Teste:** isolamento de organização/conexão, limite 60 dias, método/resolução no
retorno, paginação sem perda silenciosa, materialização determinística.
**Aceite:** detalhe devolve série Silver com proveniência; resumo não chama cloud.

### T12 — Tela de monitoramento e comparação

**Arquivos:** `apps/web/src/pages/InfrastructureMetrics/`, api service e menu.
**Fazer:** tela da seção 13 com componentes existentes; vazio/erro/parcial têm
mensagens úteis; distinguir custo da coleta de consumo financeiro do recurso.
**Teste:** filtros preservados, fuso UTC versus exibição local, comparação das
três resoluções; navegação entre resumo e série mantém o mesmo escopo.
**Aceite:** usuário identifica percentil/base, janela efetiva e ausência de memória.

### T13 — Adaptadores Azure, AWS e GCP sem duplicar a Silver

**Entrada:** contrato e OCI estabilizados; executar cada provider como tarefa
independente e commit independente. Implementar T01/T05 específicos do provider.
**Fazer:** descritores/capabilities, credencial por referência, inventário,
métricas e mappings para a mesma Silver. Adicionar dependências somente se
necessárias ao provider, seguindo requirements/lock do projeto.
**Teste contratual comum:** mesmos campos, isolamento, contadores, unidades,
labels, native percentiles unsupported, paginação/throttle e história parcial.
**Validação viva:** até duas VMs por cloud configurada, 24h e depois 7d. Sem
credencial, status é `IMPLEMENTED_NOT_LIVE_VALIDATED` ou `NOT_CONFIGURED`.
**Aceite:** nenhum indicador de produção verde baseado só em mocks; cloud
ausente não quebra outras; não escrever adapter que retorna dados simulados.

### T14 — Contrato de dados para futuro motor de savings

**Fazer:** publicar/documentar leitura dos perfis/capacidades/cobertura com
versão e hash. Fatos disponíveis: demanda, picos, limites, gaps e vigência.
Reutilizar `resource_cost`/price catalog como dependências opcionais futuras;
não inventar saving a partir de percentual ocioso. Inventário atual não prova
capacidade histórica; CPU equivalente entre arquiteturas exige evidência.
**Saída:** contrato de entrada do futuro motor com estados
`READY_FOR_CAPACITY_ANALYSIS`, `INCOMPLETE_TELEMETRY`, `MISSING_CAPACITY`,
`MISSING_COMPARABLE_PRICE`. Não implementar JEV nesta tarefa.
**Aceite:** é possível explicar qualquer P95 sem serviço externo e rastrear os
inputs. Não há número financeiro quando preço/custo comparável não existe.

### T15 — Operação, reconciliação e entrega

**Fazer:** runbook de configuração/policies mínimas, backfill, agenda,
cancelamento, recovery, retenção, custos e troubleshooting; relatório por cloud
com o que foi validado ao vivo; rollback por configuração/versão de mapping.
Executar testes de integração relevantes e controles de compatibilidade dos
fluxos tocados, incluindo AI/GPU e consumidores legados de Silver.
**Aceite:** allowlist efetiva documentada, 60 dias configurados, JEV ausente da
execução, commits rastreáveis e nenhum arquivo alheio incluído.

## 15. Comandos, testes e exemplos para evitar ambiguidades

### 15.1 Repetir a pesquisa local, sem produzir carga da aplicação

```bash
python3 scripts/pilot_oci_metrics_resolution.py \
  --config env/oci_config_preview.conf \
  --compartment tivit --instances 2 --hours 24 \
  --output apps/data/metric-pilot/oci-resolution-NOVO-RUN
```

A pasta precisa ser nova. O argumento de compartment é apenas para a repetição
local da pesquisa; a configuração de produção usa IDs/allowlist em metadata,
sem hardcode desse cliente ou região. O script não é o worker de produção.

### 15.2 Exemplos OCI de consulta

```text
CpuUtilization[1m]{resourceId = "<ID permitido>"}.mean()
CpuUtilization[1m]{resourceId = "<ID permitido>"}.max()
CpuUtilization[1m]{resourceId = "<ID permitido>"}.percentile(0.90)
CpuUtilization[1m]{resourceId = "<ID permitido>"}.percentile(0.95)
CpuUtilization[1m]{resourceId = "<ID permitido>"}.percentile(0.99)
```

Definir `resolution="1m"`, start/end UTC e compartment correto. Na comparação,
usar 5m/15m tanto no intervalo quanto na resolução. Estatísticas diferentes
requerem consultas diferentes neste endpoint; juntá-las pela chave completa,
não por posição na lista de resposta.

### 15.3 Conjunto mínimo de testes a criar conforme a implementação

Sugestão de arquivos, ainda não existentes por este plano:

```text
apps/api/tests/test_infrastructure_metric_contracts.py
apps/api/tests/test_infrastructure_metric_publication.py
apps/api/tests/test_infrastructure_metric_providers.py
apps/api/tests/test_infrastructure_metric_jobs.py
apps/api/tests/test_infrastructure_metric_rollups.py
apps/api/tests/test_infrastructure_metric_profiles.py
apps/api/tests/test_infrastructure_metric_retention.py
apps/api/tests/test_infrastructure_metric_authorization.py
```

Rodar com o Python do ambiente válido e `PYTHONPATH=apps/api`, por exemplo:

```bash
PYTHONPATH=apps/api .venv/bin/python -m pytest \
  apps/api/tests/test_infrastructure_metric_contracts.py \
  apps/api/tests/test_infrastructure_metric_profiles.py -q
```

Adaptar o comando se o venv local não contiver as dependências. Não declarar
testes passados sem execução. Checks de frontend seguem os scripts reais de
`apps/web/package.json`, sem inventar comando `test` inexistente.

### 15.4 Cenário sintético obrigatório de pico

Construir uma série com 60 buckets de 1m: 59 em 10%, um em 100%. Confirmar que
rollup de 15m reduz o maior valor médio e preserva 100% na coluna max; o tempo
exato acima de 80% é conhecido na série de 1m e não pode ser reconstruído só
com max de 15m. Acrescentar gaps e verificar que nenhuma ausência vira 0%.

### 15.5 Critério final de conclusão desta iniciativa

- Séries multicloud com identidade suficiente e dados brutos rastreáveis.
- Coleta OCI real restrita e conectores adicionais com status honesto.
- P90/P95/P99 nativos e locais diferenciados, sem agregação incorreta.
- 60 dias próprios com retenção e cobertura efetiva demonstráveis.
- Resolução escolhida por evidência, custo e preservação dos picos.
- Publicação idempotente/atômica, cancelamento e retomada comprovados.
- API e UI exibem método, resolução, período observado e lacunas.
- Capacidade/custo ausente impede conclusão indevida de savings; JEV fica futuro.

## 16. Referências oficiais para revalidação durante a execução

Links consultados em 23/10/2026; limites e preços podem mudar. A matriz de
capabilities deve registrar a data da consulta e as divergências medidas.

- [OCI MQL e estatísticas](https://docs.oracle.com/en-us/iaas/Content/Monitoring/Reference/mql.htm)
- [OCI intervalo e resolução](https://docs.oracle.com/en-us/iaas/Content/Monitoring/Tasks/query-metric-resolution.htm)
- [OCI campos da requisição](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/monitoring/models/oci.monitoring.models.SummarizeMetricsDataDetails.html)
- [OCI métricas Compute](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computemetrics.htm)
- [OCI métricas Block Volume](https://docs.oracle.com/en-us/iaas/Content/Block/References/volumemetrics-reference.htm)
- [OCI preços de Monitoring](https://www.oracle.com/manageability/pricing/)
- [Azure métricas e retenção](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/data-platform-metrics)
- [Azure API Metrics List](https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list)
- [Azure preços de Monitor](https://azure.microsoft.com/en-us/pricing/details/monitor/)
- [AWS conceitos, percentis e retenção](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html)
- [AWS preços de CloudWatch](https://aws.amazon.com/cloudwatch/pricing/)
- [GCP quotas e retenção](https://docs.cloud.google.com/monitoring/quotas)
- [GCP distribuições e percentis](https://docs.cloud.google.com/monitoring/api/v3/distribution-metrics)
- [GCP aligners e reducers](https://docs.cloud.google.com/monitoring/api/ref_v3/rpc/google.monitoring.v3)
- [GCP preços de observabilidade](https://cloud.google.com/stackdriver/pricing)
