# Plano de execucao - Gold orientada a decisao e novos dashboards FinOps

> Instrucao para uma IA executora trabalhando em harness por etapas. Este plano parte do sistema e dos dados que existem em 23/09/2026. Ele nao autoriza refazer coletores, criar outra arquitetura analitica ou substituir as tabelas Silver canonicas. O objetivo e transformar os dados ja coletados em cubos Gold reconciliados e em telas que conduzam decisoes.

## 1. Resultado esperado

Construir um **Centro de Decisao FinOps** que responda, com numero reconciliado e caminho ate o detalhe:

1. Quanto gastamos, onde, com quem e em qual periodo confiavel?
2. O que mudou, quem explica a variacao e qual valor merece investigacao?
3. Quanto do custo esta corretamente classificado, alocado e coberto por dados operacionais?
4. Onde existem oportunidades acionaveis, qual o DE/PARA, qual a economia estimada e qual a confianca?
5. Qual e o estado das decisoes: identificada, validada, aprovada, executada, medida ou descartada?
6. Em AKS, quanto custa cada cluster, node pool, namespace e workload, incluindo idle, system e unallocated?
7. Em Azure OpenAI PTU, qual capacidade foi contratada, quanto foi usada, onde ha ociosidade ou saturacao e qual o impacto financeiro?
8. Quais dados estao atrasados, incompletos ou sem classificacao e quanto dinheiro fica afetado?

O produto final deve ter:

- uma camada Gold pequena, estavel e documentada;
- uma definicao unica para cada KPI;
- APIs que consultam Gold para resumos e Silver apenas para detalhes sob demanda;
- filtros globais coerentes entre telas;
- navegacao do indicador ate o recurso, namespace ou decisao que o explica;
- estados de qualidade visiveis ao lado do valor;
- nenhuma economia estimada apresentada como economia realizada.

## 2. Fonte de verdade e limites

### 2.1 Ordem das fontes de verdade

Quando houver divergencia, usar esta ordem:

1. tabelas Iceberg e registros PostgreSQL do ambiente em execucao;
2. contratos em `apps/api/app/services/silver_models.py`;
3. servicos que realmente escrevem ou consultam esses dados;
4. testes ativos;
5. este plano;
6. documentacao anterior.

Registrar qualquer divergencia no log de execucao antes de alterar o contrato.

### 2.2 Arquitetura obrigatoria

```text
Bronze por fonte
  -> Silver canonica, detalhada e auditavel
  -> Gold agregada por assunto e grao declarado
  -> API de consulta
  -> Centro de Decisao FinOps
```

- Bronze preserva o payload e a proveniencia.
- Silver continua sendo o livro detalhado de custo, inventario, metricas, perfis, candidatos e decisoes.
- Gold contem fatos derivados, agregados e prontos para consumo.
- PostgreSQL contem configuracao, workflow, revisao e metadata de execucao.
- Nao criar uma quarta camada analitica.
- Nao copiar 13 milhoes de linhas para um suposto cubo enriquecido linha a linha.
- Nao fazer joins volumosos a cada request de dashboard.
- Nao implementar coletor novo para resolver uma falta que pode ser expressa como cobertura indisponivel.

### 2.3 Escopo deste plano

Incluido:

- saneamento semantico necessario para medir corretamente;
- contratos dos novos cubos Gold;
- materializacao governada, dependencias e reconciliacao;
- endpoints de leitura e drilldown;
- substituicao das telas de custo e saving fragmentadas por uma experiencia integrada;
- dashboards de custo, drivers, eficiencia, oportunidades, AKS, PTU e qualidade;
- preparacao do funil de saving realizado sem inventar dados ainda inexistentes.

Fora do escopo imediato:

- novos providers ou novas APIs Azure;
- execucao automatica de resize, desligamento ou alteracao de cloud;
- criacao automatica de tickets ServiceNow;
- previsao de desempenho por LLM;
- mostrar saving realizado antes de existir data de execucao e janela posterior comparavel.

## 3. Baseline real verificado em 23/09/2026

### 3.1 Silver existente

| Modelo | Linhas | Cobertura relevante |
|---|---:|---|
| `resource_cost` | 13.519.512 | 17 competencias presentes, duas conexoes de custo |
| `resource_metric_series` | 3.080.297 | 15 dias, CPU, memoria, disco e rede |
| `resource_inventory` | 39.343 | 20.160 recursos distintos, 199 tipos, snapshots de 22 e 23/09 |
| `price_catalog` | 3.946.787 | preco contratado, observado e fallback conforme a origem |
| `compute_sku_catalog` | 1.523.324 | SKU, capacidade, disponibilidade e restricoes |
| `resource_quota` | 23.200 | 50 contas, 2 regioes, 232 quotas, snapshot atual |
| `region_policy` | 5.527 | politicas e regioes permitidas/restritas |
| `resource_allocation` | 242 | 46 pais; somente nivel `node_pool`, status `PARTIAL` |
| `container_metric_series` | 9.177 | 46 clusters avaliados; 5 disponiveis e 41 indisponiveis; namespaces em 4 clusters |
| `ptu_capacity_series` | 6.660 | 2 recursos, 4 deployments, mai-ago/2026 |
| `resource_profile_window` | 1.746 | 194 VMs por metrica, janela maxima atual de 15 dias |
| `rightsizing_candidate` | 193 | 23 validos, 104 requerem validacao, 66 invalidos |
| `rightsizing_decision` | 127 | 23 validas e 104 requerem validacao; todas pendentes |

`focus_enriched` esta declarado no catalogo de modelos, mas nao existe fisicamente. Nao depender dele e nao tentar materializa-lo no grao do FOCUS. Se o modelo nao tiver consumidor legitimo ao fim de G1, remover a declaracao obsoleta.

### 3.2 Qualidade que altera a leitura financeira

`resource_cost` nao representa uma serie mensal homogenea:

- `azure-4p` possui 12.970.906 linhas e R$ 35.368.613,01, de 202504 a 202607;
- `azure-ti` possui 548.606 linhas e R$ 1.987.748,46, somente em 202609;
- no 4P, 202604 tem 51 linhas, 202605 e 202606 tem 1 linha cada e 202607 tem 3 linhas;
- 202504 e 202505 possuem ausencia massiva de servico, regiao e tags;
- `Microsoft.Compute` e `microsoft.compute` aparecem separados por caixa;
- o dashboard atual soma 17 competencias e chama isso de custo total, mesmo com escopos e completudes diferentes.

Consequencia obrigatoria: todo KPI temporal deve selecionar **periodos comparaveis e completos por conexao**, mostrar o escopo e carregar um status de qualidade. O ultimo texto `YYYYMM` nao e automaticamente o ultimo mes comparavel.

### 3.3 Cobertura operacional

- CPU com qualidade `OK`: 194 recursos; outros 101 possuem gaps.
- Os perfis atuais sao de `microsoft.compute/virtualmachines`; VMSS pode existir na serie, mas ainda nao aparece como perfil distinto materializado.
- Em cada metrica principal, aproximadamente 160 VMs possuem confianca alta; as demais possuem confianca media ou baixa.
- A janela observada e 15 dias. Nunca rotular esse resultado como analise de 90 dias.
- Das 193 alternativas, somente 23 sao `VALID` com preco comparavel e saving mensal total de R$ 6.050,24.
- Nenhuma decisao esta aprovada. Portanto, saving aprovado e realizado sao zero/ausentes, e nao R$ 6.050,24.

### 3.4 AKS e PTU

- A Silver de containers encontrou namespaces e performance, mas `resource_allocation` publica apenas pesos de `node_pool`.
- O cubo Gold `mvp_aks_consumo` replica 242 pesos; ele ainda nao e um cubo de custo por namespace.
- PTU tem completude media de 99,87%, utilizacao media de 3,69% e p95 medio de 8,44% no conjunto observado. Esses numeros indicam uma investigacao de ociosidade, mas nao provam saving financeiro sem associacao de custo e vigencia de capacidade.

### 3.5 Gold atual

Existem cubos legados de custo organizacional, quatro cubos MVP e cubos especializados. Os quatro MVP atuais sao:

| Tabela | Linhas | Limite atual |
|---|---:|---|
| `dest_dm_cost_mvp_custo_recurso` | 1.354.962 | nao traz servico, categoria, tipo, moeda nem flag de completude |
| `dest_dm_cost_mvp_oportunidade` | 20 | agregado demais; nao sustenta funil nem detalhe completo |
| `dest_dm_cost_mvp_decisao` | 3 | perde granularidade da decisao e o estado real de revisao vive no PostgreSQL |
| `dest_dm_cost_mvp_aks_consumo` | 242 | pesos por node pool, sem custo alocado por namespace |

Ha ainda uma tabela temporaria `_tmp_vm_monthly` dentro de Gold. Tabela temporaria nao pode permanecer como contrato publico.

### 3.6 Diagnostico do dashboard atual

Hoje existem experiencias concorrentes:

- `GoldDashboard` e amplo para custo organizacional, mas consulta um cubo legado e calcula varios indicadores no navegador;
- `SavingActions` consulta diretamente Silver e chama a soma de todas as competencias de custo total;
- `Rightsizing` usa cubos MVP no resumo e Silver no workflow;
- os nomes de cliente ainda incluem `queryDimonTable`/`getDimonInfo`, embora a arquitetura atual seja Gold;
- custo, eficiencia, recomendacao, qualidade e cobertura nao compartilham um contrato semantico.

O problema principal nao e falta de graficos. E falta de grao, definicao, reconciliacao e contexto de qualidade.

## 4. Regras semanticas obrigatorias

### 4.1 Dimensoes conformadas

Normalizar na construcao Gold, preservando os valores originais na Silver:

- `provider`: minusculo e vocabulario controlado;
- `connection_code` e `connection_name`: identificam o escopo real;
- `sub_account_id` e nome resolvido quando disponivel;
- `canonical_resource_id`: `trim(lower(resource_id))` para Azure;
- `resource_type_key`: `trim(lower(resource_type))`;
- `service_key`: `trim(lower(service_name))`; manter `service_name_display` deterministico;
- `region_key`: normalizada, sem misturar nulo com `global`;
- `billing_period`: exatamente `YYYYMM`;
- `currency`: obrigatoria para somar dinheiro; nao somar BRL e USD na mesma medida;
- hierarquia organizacional e centro de custo conforme regras existentes;
- tags prioritarias extraidas por dicionario configuravel, sem promover todo JSON a colunas.

Nunca usar apenas `resource_id` para join entre tenants ou providers. A chave segura deve incluir provider, conta e ID canonico quando esses dados existirem.

### 4.2 Competencia comparavel

Criar uma classificacao de cobertura por `connection_code + billing_period + dataset`:

- `COMPLETE`: carga concluida e reconciliada;
- `PARTIAL`: ha dados, mas abaixo do limite de completude ou com erro parcial;
- `EMPTY`: carga valida com zero linhas;
- `MISSING`: nenhuma carga esperada;
- `CURRENT_OPEN`: competencia em andamento;
- `NOT_APPLICABLE`: fonte nao deveria produzir aquele periodo.

O limite nao deve depender somente da quantidade de linhas. Usar em conjunto:

- status da execucao;
- primeira e ultima data de consumo;
- dias cobertos versus dias esperados;
- variacao de linhas e custo em relacao ao historico da mesma conexao;
- reconciliacao Bronze -> Silver;
- atraso desde a ultima atualizacao.

Comparacoes MoM, run rate, anomalia e forecast usam apenas pares com escopo igual e status permitido. A UI pode mostrar periodo parcial, mas deve rotula-lo e nao usa-lo silenciosamente como base de tendencia.

### 4.3 Medidas financeiras

Definicoes iniciais:

| Metrica | Formula | Regra |
|---|---|---|
| custo efetivo | `SUM(effective_cost)` | moeda e escopo unicos |
| custo faturado | `SUM(billed_cost)` | nao usar como sinonimo de efetivo |
| desconto contratado | `SUM(list_cost - contracted_cost)` | somente quando ambos comparaveis |
| economia identificada | soma da melhor decisao `VALID` vigente por recurso | uma alternativa por recurso |
| economia pendente | identificada com revisao `PENDING` | nao e compromisso financeiro |
| economia aprovada | identificada com revisao `APPROVED` | ainda e estimativa |
| economia realizada | baseline ajustado menos custo posterior ajustado | somente apos execucao registrada |
| custo nao alocado | custo elegivel sem consumidor confiavel | fica explicito, nunca desaparece |

Para oportunidade, escolher uma unica decisao vigente por recurso e versao. Nunca somar candidatos alternativos para o mesmo recurso.

### 4.4 Qualidade como medida financeira

Toda agregacao Gold de custo deve carregar:

- `data_status`;
- `freshness_at`;
- `source_row_count`;
- `missing_dimension_cost` para dimensoes criticas;
- `allocation_coverage_pct` quando aplicavel;
- `telemetry_coverage_pct` quando aplicavel;
- `reconciliation_delta` e `reconciliation_status`.

Ausencia nao vira zero. Nulo, indisponivel e zero observado sao estados diferentes.

## 5. Contratos dos cubos Gold

Os nomes abaixo sao contratos novos. A IA pode ajustar o prefixo fisico exigido pelo materializador, mas nao pode mudar grao ou semantica sem registrar uma ADR.

### C1 - `finops_cost_monthly`

**Pergunta:** quanto custou cada escopo por mes e quais dimensoes explicam esse custo?

**Grao:** uma linha por `billing_period + provider + connection_code + sub_account_id + service_key + service_category + region_key + charge_category + currency + hierarquia organizacional`.

**Fonte Silver:** `resource_cost`, mais classificacao organizacional existente quando ela nao estiver disponivel no modelo canonico.

**Dimensoes minimas:** periodo, provider, conexao, conta, servico, categoria, regiao, categoria de cobranca, moeda, niveis organizacionais e centro de custo.

**Medidas:** custo efetivo, faturado, contratado, lista, quantidade consumida, linhas de origem, recursos distintos e custo sem resource ID/tag/classificacao.

**Invariantes:**

- soma por periodo/conexao/moeda reconcilia com Silver dentro de R$ 0,01;
- nomes normalizados nao dividem `Microsoft.Compute` por caixa;
- nenhuma moeda e convertida implicitamente;
- `data_status` impede uso silencioso de meses incompletos.

### C2 - `finops_cost_resource_monthly`

**Pergunta:** quais recursos dirigem o custo e qual e o contexto de cada um?

**Grao:** uma linha por `billing_period + provider + connection_code + sub_account_id + canonical_resource_id + charge_category + currency`.

**Fonte Silver:** `resource_cost`, enriquecida por snapshot temporal de `resource_inventory` apenas depois de garantir join 1:1.

**Dimensoes:** recurso/nome/tipo/SKU, resource group, conta, regiao, servico/categoria, estado, SO e tags prioritarias.

**Medidas:** custos, quantidade, dias de cobranca, primeira/ultima cobranca e quantidade de linhas.

**Uso:** top recursos, drilldown de custo, ligacao com eficiencia e DE/PARA. O endpoint deve paginar; nao retornar o cubo inteiro.

### C3 - `finops_cost_change_driver`

**Pergunta:** o que explica a mudanca entre duas competencias comparaveis?

**Grao:** uma linha por `period_current + period_previous + connection_code + driver_type + driver_key`.

**Fonte:** C1 e C2.

**Medidas:** custo atual, anterior, delta absoluto, delta percentual, contribuicao para delta total, participacao atual, flag `new`, `removed`, `increased` ou `decreased`.

**Regras:** comparar mesmo escopo/moeda; excluir ou sinalizar periodo parcial; calcular drivers de servico, conta, regiao, organizacao e recurso em materializacoes separadas ou por `driver_type`.

### C4 - `finops_efficiency_resource`

**Pergunta:** quais recursos combinam custo material e baixa utilizacao, e com qual cobertura?

**Grao:** uma linha por `profile_hash + resource_id + metric_set + analysis_window_end`.

**Fonte:** `resource_profile_window`, C2, `resource_inventory` e `resource_metric_series` somente durante materializacao.

**Medidas/dimensoes:** custo da janela e mensalizado, CPU/memoria p50/p95/p99, IO e rede, dias solicitados/observados, cobertura, confianca, SKU, capacidade, estado e quadrante `HIGH_COST_LOW_USE`, `HIGH_COST_HIGH_USE`, `LOW_COST_LOW_USE`, `LOW_COST_HIGH_USE`.

**Regra:** o quadrante usa limites configurados e versionados. Nao declarar desperdicio somente por CPU baixa quando memoria/IO sao desconhecidos.

### C5 - `finops_optimization_portfolio`

**Pergunta:** qual e a carteira atual de decisoes e qual valor esta em cada estado?

**Grao:** uma linha por `evidence_hash` da decisao vigente.

**Fonte:** `rightsizing_decision`, perfil e inventario; estado de revisao atual do PostgreSQL incorporado no momento da materializacao ou sobreposto por uma view/API de workflow com contrato explicito.

**Campos obrigatorios:** recurso, tipo, conta, regiao, estrategia, estado deterministico, revisao, confianca, dias observados, preco comparavel, DE/PARA de SKU/vCPU/memoria/custo, saving mensal/anual, fonte de preco, veto, run, timestamps e responsavel.

**Invariantes:**

- um `evidence_hash` aparece uma vez;
- carteira soma somente a decisao escolhida por recurso;
- `REQUIRES_VALIDATION` sem preco nao contribui para saving;
- revisao humana nao altera veredito deterministico;
- saving identificado, aprovado e realizado sao colunas distintas.

### C6 - `finops_optimization_funnel_daily`

**Pergunta:** a operacao FinOps esta convertendo achados em resultado?

**Grao:** `snapshot_date + connection_code + strategy + resource_type + confidence`.

**Fonte:** C5, historico de runs e revisoes PostgreSQL.

**Medidas:** recursos e saving nos estados identificado, requer validacao, valido, pendente, aprovado, rejeitado, deferido, executado e medido; idade media; tempo ate revisao; taxa de conversao.

**Regra:** usar snapshot diario para preservar funil historico. Nao reconstruir o passado a partir do estado atual.

### C7 - `finops_optimization_projection_daily`

**Pergunta:** como o recurso se comportou e como ficaria depois da alteracao?

**Grao:** `evidence_hash + metric + day + series_type`, sendo `series_type = OBSERVED | PROJECTED`.

**Fonte:** serie Silver e snapshot do candidato.

**Medidas:** p50, p95, maximo, amostras, custo diario observado/projetado e cobertura.

**Uso:** modal DE/PARA. A projecao precisa de estilo e legenda distintos. Nao fabricar pontos quando a simulacao nao existir.

### C8 - `finops_aks_allocation_daily`

**Pergunta:** como o custo elegivel de AKS percorre a cadeia de consumo?

**Grao:** `day + cluster + allocation_level + allocation_key + cost_component + currency`.

**Hierarquia:** cluster -> node pool/node -> namespace -> controller/workload -> pod/container; volume e recurso Azure associado entram como relacoes laterais, sem duplicar o custo.

**Fonte:** `resource_cost`, `resource_allocation`, `container_metric_series` e inventario.

**Medidas:** custo original elegivel, custo alocado, peso, CPU/memoria requests/usage quando disponiveis, pods, nodes, idle, system, shared e unallocated.

**Invariantes:**

- cada nivel fecha com o pai dentro da tolerancia;
- pesos do grupo somam 1 ou o restante vira `UNALLOCATED`;
- nivel `node_pool` atual nao pode ser rotulado como namespace;
- se a ponte namespace nao existir, publicar cobertura parcial e manter o custo no nivel conhecido;
- um filtro de namespace nao multiplica o custo do cluster.

### C9 - `finops_ptu_efficiency_hourly`

**Pergunta:** a capacidade PTU esta ociosa, saudavel ou saturada?

**Grao:** `metric_hour + resource_id + deployment_name`.

**Fonte:** `ptu_capacity_series` e, quando reconciliavel, C2 para custo/capacidade.

**Medidas:** PTUs provisionadas, utilizacao media/p95/max, tokens, requests, erros, rate limit, spillover, latencia, completude e custo atribuido.

**Classificacao:** `UNDERUTILIZED`, `HEALTHY`, `SATURATED`, `INSUFFICIENT_DATA`, com limites configurados e versao da regra.

**Regra:** oportunidade financeira fica nula enquanto custo e vigencia nao forem comparaveis. Utilizacao baixa por si so e sinal operacional.

### C10 - `finops_commitment_exposure`

**Pergunta:** onde ha compras/compromissos, desconto observado e risco de baixa utilizacao?

**Grao:** `billing_period + connection_code + sub_account_id + service + commitment_key + currency`.

**Fonte:** `resource_cost` e `price_catalog`.

**Medidas:** custo de compra, custo coberto, quantidade, preco lista/contratado, desconto e exposicao sem associacao.

**Condicao:** construir apenas se os campos reais distinguirem compromisso, compra e uso coberto. Se o contrato de dados nao permitir utilization/coverage corretos, publicar somente exposicao e desconto, com nome claro. Nao inferir cobertura de reserva por diferenca de preco.

### C11 - `finops_data_quality_daily`

**Pergunta:** quais limitacoes de dados afetam decisoes e quanto custo esta exposto?

**Grao:** `snapshot_date + connection_code + dataset + quality_rule + severity`.

**Fonte:** metadata das cargas, Silver e resultados de reconciliacao Gold.

**Medidas:** linhas, custo afetado, recursos afetados, completude, freshness, delta de reconciliacao e dias em falha.

**Regras iniciais:** fonte atrasada; periodo parcial; dimensao critica ausente; moeda ausente; ID nao canonico; join multiplicativo; telemetria insuficiente; preco nao comparavel; AKS nao alocado; Gold desatualizada.

### C12 - `finops_savings_realized_monthly` (contrato preparado, dado futuro)

**Pergunta:** quanto da economia aprovada foi efetivamente realizado?

**Grao:** `action_id + billing_period`.

**Fonte:** registro de execucao, C2 e baseline congelado.

**Medidas:** baseline ajustado, custo esperado, custo observado, saving previsto, realizado, desvio, dias validos e impacto operacional.

**Gate:** nao materializar valores antes de existir `executed_at`, baseline, janela posterior completa e metodologia versionada. Ate la, a UI mostra `ainda nao mensuravel`.

## 6. Governanca da materializacao Gold

### 6.1 Um unico caminho

Todo cubo novo deve ser registrado e executado pela governanca `gold_rule` + `gold_rule_source` + `gold_materialization`. Nao criar um segundo servico de CTAS direto.

Antes de G2:

- confirmar se `gold_cubes_service.py` e `gold_builder.py` ainda possuem consumidores ativos;
- migrar o que for legitimo para o materializador governado;
- remover ou marcar explicitamente qualquer caminho morto;
- corrigir docstrings de `GoldMartRule`, que ainda descrevem fontes como tabelas Gold;
- renomear clientes `queryDimonTable` e `getDimonInfo` para nomes Gold, mantendo alias temporario apenas durante migracao;
- retirar `_tmp_vm_monthly` do namespace publico apos substituir seu consumidor.

### 6.2 Manifesto de execucao

Cada materializacao precisa registrar:

- `cube_code` e versao do contrato;
- run e timestamps;
- snapshots/periodos das fontes;
- SQL/template hash;
- linhas lidas e escritas;
- totais financeiros de entrada e saida;
- delta e tolerancia;
- freshness;
- status `SUCCESS`, `PARTIAL`, `FAILED`;
- erro sanitizado;
- tabela anterior para rollback.

### 6.3 Ordem e publicacao atomica

```text
classificacao de periodos e qualidade
  -> C1 custo mensal
  -> C2 custo por recurso
  -> C3 drivers
  -> C4 eficiencia
  -> C5 carteira
  -> C6 funil + C7 projecao
  -> C8 AKS + C9 PTU + C10 compromissos
  -> C11 qualidade
  -> APIs e dashboards
```

Construir em staging, validar e trocar atomicamente. Falha preserva a versao publicada anterior. Cubo dependente nao publica com fonte mais antiga que seu limite de freshness.

### 6.4 Performance

- particionar fatos mensais por `billing_period`;
- particionar fatos diarios por data quando o volume justificar;
- evitar `COUNT(DISTINCT)` em request quando ele puder ser precomputado;
- API deve limitar cardinalidade, paginar detalhe e impor timeout;
- drilldown de recurso pode consultar C2/C4/C5 e buscar serie Silver apenas para um recurso;
- nenhuma tela deve baixar milhares de linhas para calcular KPI global no navegador;
- metas iniciais: p95 ate 2 s para resumo filtrado e ate 5 s para drilldown pesado no ambiente local de referencia.

## 7. Centro de Decisao FinOps

Criar uma rota principal, por exemplo `/finops-center`, com filtros persistentes de periodo, provider, conexao, conta, organizacao, servico, regiao e moeda. As abas usam o mesmo escopo e mostram a data de atualizacao e qualidade.

### 7.1 Aba Resumo executivo

Perguntas e componentes:

- custo efetivo da ultima competencia completa e comparavel;
- variacao MoM com valor e percentual;
- run rate anualizado, sempre informando quantidade de meses usados;
- custo em periodo parcial separado;
- saving identificado, aprovado e realizado em cards separados;
- custo com classificacao incompleta;
- cobertura de telemetria e de alocacao AKS;
- waterfall de variacao por principais drivers;
- top cinco acoes por saving, com confianca e idade;
- painel `Decisoes requeridas` com links para investigar/revisar.

O card deve responder `valor + comparacao + escopo + qualidade + acao`. Um valor isolado nao e suficiente.

### 7.2 Aba Custo e drivers

- serie mensal apenas com escopo comparavel;
- decomposicao por organizacao, conta, servico, categoria e regiao;
- Pareto e concentracao;
- tabela de drivers com atual, anterior, delta e contribuicao;
- recursos novos, removidos e com maior aumento;
- drilldown hierarquico ate recurso;
- exportacao do resultado filtrado, sem exportar a tabela Silver inteira.

### 7.3 Aba Eficiencia

- matriz custo x utilizacao;
- distribuicao por quadrante, confianca e cobertura;
- top recursos de alto custo e baixo uso;
- filtros por tipo, SKU, regiao, conta e quantidade de dias observados;
- tooltip que diferencia CPU, memoria, disco e rede ausentes;
- clique abre o detalhe com serie observada e recomendacao associada.

### 7.4 Aba Oportunidades e workflow

- funil: detectadas -> validas -> pendentes -> aprovadas -> executadas -> medidas;
- valores mensal e anual sem misturar estados;
- aging e SLA de revisao;
- carteira por estrategia, tipo de recurso, confianca e owner;
- tabela acionavel com DE/PARA, custo, saving, cobertura, veto e preco;
- comparacao entre alternativas sem soma-las;
- revisao humana existente permanece aqui;
- modal do recurso exibe observada x projetada, premissas e evidencias.

### 7.5 Aba AKS economics

- custo por cluster e sua cobertura de alocacao;
- navegacao cluster -> node pool -> namespace -> workload -> pod/container;
- custos workload, idle, system, shared e unallocated;
- eficiencia por requests/usage quando disponivel;
- namespaces de maior custo e maior ociosidade;
- seletor de snapshot/data;
- breadcrumb e reconciliacao visivel em cada nivel;
- clusters sem Container Insights aparecem com causa e custo afetado.

Enquanto a ponte estiver apenas em node pool, a tela deve parar nesse nivel e mostrar `cobertura parcial`; nao distribuir custo igualmente por namespace.

### 7.6 Aba AI/PTU

- capacidade provisionada e utilizacao media/p95/max;
- tokens e requests por deployment/modelo;
- erro, throttling, spillover e latencia;
- heatmap hora x dia;
- periodos ociosos e saturados;
- completude da telemetria;
- custo e oportunidade somente quando reconciliados.

### 7.7 Aba Qualidade e cobertura

- score baseado em regras versionadas, acompanhado dos componentes;
- freshness por fonte/cubo;
- periodos completos/parciais/ausentes;
- custo sem servico, regiao, tag ou organizacao;
- cobertura de join custo-inventario, custo-telemetria e custo-alocacao;
- delta Silver x Gold;
- incidentes de pipeline e link para a conexao afetada.

O score nao pode mascarar materialidade: sempre mostrar percentual e valor financeiro afetado.

### 7.8 Regras de visualizacao

- linha para tendencia; barras para ranking; waterfall para variacao; scatter para custo x uso; heatmap para tempo x intensidade; tabela para acao;
- evitar pizza quando houver muitas categorias;
- usar paleta consistente: custo, risco, saving estimado, aprovado e realizado possuem cores distintas;
- formatar moeda a partir da coluna `currency`;
- exibir denominador e periodo em percentuais;
- estados vazios explicam dado ausente e proxima acao;
- acessibilidade: legenda textual, contraste e tabela equivalente para informacao essencial;
- desktop primeiro, mantendo cards e tabelas utilizaveis em largura menor;
- nenhum grafico deve depender apenas de cor.

## 8. API e contrato de consulta

Criar endpoints por produto, sem expor SQL generico ao frontend:

```text
GET /finops/summary
GET /finops/cost/trend
GET /finops/cost/drivers
GET /finops/cost/resources
GET /finops/efficiency/resources
GET /finops/optimization/portfolio
GET /finops/optimization/funnel
GET /finops/optimization/{evidence_hash}/comparison
GET /finops/aks/tree
GET /finops/ptu/efficiency
GET /finops/data-quality
```

Contrato comum de resposta:

```json
{
  "scope": {},
  "as_of": "timestamp",
  "currency": "BRL",
  "data_status": "COMPLETE",
  "quality": {"coverage_pct": 0, "warnings": []},
  "data": {},
  "page": null
}
```

Regras:

- validar filtros por allowlist;
- separar endpoint resumido de detalhe paginado;
- retornar definicao/versao da metrica quando necessario;
- cachear por cubo, snapshot e conjunto de filtros;
- invalidar cache somente depois da publicacao atomica;
- manter endpoints antigos durante um ciclo de compatibilidade e medir uso antes de remove-los.

## 9. Roteiro para o harness

### Regra de execucao de cada pacote

Executar `G0` a `G10` na ordem. Antes de editar, a IA deve produzir no log:

```text
entrada -> premissas verificadas -> arquivos -> saida -> testes -> risco -> rollback
```

Ao concluir cada pacote:

1. executar os testes e reconciliacoes do pacote;
2. revisar `git status` e incluir somente arquivos do pacote;
3. atualizar `docs/ai/gold-decision-dashboard-execution-log.md`;
4. criar commit como `feat(G3): materialize cost decision cubes`;
5. fazer push para `origin` na branch atual, sem `--force`;
6. registrar branch, SHA, comandos e resultados;
7. nao avancar se a reconciliacao financeira falhar.

Preservar mudancas do usuario e nunca incluir `.env`, segredo, dump ou dado local.

### G0 - Auditoria reproduzivel e dicionario de metricas

**Fazer:** reproduzir o baseline deste plano; listar tabelas, schemas, regras Gold, jobs e consumidores; consultar os registros `gold_rule`; identificar caminhos diretos e legados; criar dicionario com nome, formula, grao, moeda, filtros, owner e limitacoes de cada KPI.

**Saidas:** log inicial, inventario `reusar/alterar/remover`, dicionario versionado e matriz tela -> KPI -> cubo.

**Aceite:** numeros deste plano foram confirmados ou divergencias explicadas; nenhuma mudanca funcional; nenhum secret no log.

### G1 - Fundacao semantica e qualidade de periodo

**Fazer:** implementar normalizadores compartilhados, chaves canonicas, display names e classificacao de competencia; identificar cargas completas por conexao; eliminar dependencia de `focus_enriched`; definir moeda e tolerancia.

**Saidas:** funcoes/servico testado e dataset de qualidade que alimentara C11.

**Aceite:** `Microsoft.Compute` e variante de caixa agregam juntas; abril-julho/2026 do 4P nao viram base silenciosa de MoM; cada periodo tem motivo e status; IDs canonicos nao multiplicam custo.

### G2 - Unificar governanca Gold

**Fazer:** consolidar o caminho de materializacao em `gold_rule`; implementar manifesto, dependencias, staging/swap e rollback; corrigir nomes/docstrings legados; tratar `_tmp_vm_monthly` e servicos CTAS paralelos.

**Saidas:** um unico executor governado e observavel.

**Aceite:** falha conserva cubo anterior; manifesto prova fontes/totais; busca no codigo nao encontra novo consumidor do caminho desativado; testes de materializacao e rollback passam.

### G3 - Cubos financeiros C1, C2 e C3

**Fazer:** materializar custo mensal, recurso mensal e drivers; implementar incremental por periodo; reconciliar por conexao/moeda; criar APIs de custo.

**Saidas:** custo confiavel, navegavel e comparavel.

**Aceite:** totais C1/C2 batem com `resource_cost`; driver fecha no delta total; nao ha soma entre moedas; query p95 atende a meta; periodos parciais aparecem sinalizados.

### G4 - Cubos de eficiencia e otimizacao C4 a C7

**Fazer:** combinar custo, perfil e decisao sem join multiplicativo; incorporar estado atual de revisao; congelar snapshots diarios do funil; publicar serie DE/PARA.

**Saidas:** carteira unica e detalhe explicavel.

**Aceite:** 23 decisoes validas reconciliam com R$ 6.050,24 no baseline; aprovadas continuam zero enquanto nao revisadas; uma alternativa por recurso entra no total; cobertura/dias/preco/veto estao disponiveis.

### G5 - AKS C8

**Fazer:** verificar granularidade real; construir alocacao diaria somente nos niveis sustentados; relacionar namespaces onde houver ponte valida; publicar idle/system/unallocated; criar API de arvore.

**Saidas:** drilldown reconciliado com cobertura explicita.

**Aceite:** 46 pais e 242 pesos atuais continuam reconciliados; 41 clusters indisponiveis aparecem como gap; custo fecha em cada nivel; namespace nao recebe custo inferido por nome ou divisao uniforme.

### G6 - PTU e compromissos C9/C10

**Fazer:** materializar eficiencia PTU; associar custo somente com chave e vigencia comprovadas; verificar se dados de compromisso suportam as metricas antes de publicar.

**Saidas:** visao operacional PTU e exposicao financeira suportada pelos dados.

**Aceite:** 2 recursos/4 deployments e completude reconciliam; classificacao e versionada; custo/oportunidade permanecem nulos quando o join nao for defensavel; nenhum KPI de reserva e inventado.

### G7 - Qualidade C11 e contrato de realized savings C12

**Fazer:** consolidar freshness, completude, classificacao, joins, telemetria e reconciliacao; criar schema e workflow de execucao da acao; nao preencher realized saving sem evidencia.

**Saidas:** painel de qualidade material e base auditavel para resultado futuro.

**Aceite:** toda falha informa recursos e custo afetado; score e decomponivel; `realized` esta ausente no baseline e a UI explica por que.

### G8 - API do Centro de Decisao

**Fazer:** implementar endpoints tipados, filtros conformados, paginacao, cache e autorizacao; remover calculos globais do navegador; manter compatibilidade temporaria.

**Saidas:** contrato estavel para todas as abas.

**Aceite:** testes de contrato, autorizacao e filtros; mesmo escopo retorna totais iguais entre resumo e detalhe; sem SQL livre; p95 dentro da meta.

### G9 - Frontend integrado

**Fazer:** criar rota e abas da secao 7; reutilizar padrao visual existente; ligar cada widget ao endpoint/cubo declarado; migrar workflow de revisao; implementar drilldowns e estados de qualidade.

**Saidas:** Centro de Decisao utilizavel por executivo, FinOps e engenharia.

**Aceite:** nenhum card usa soma de periodos heterogeneos sem aviso; cada KPI tem tooltip de definicao; filtros persistem; clique chega ao detalhe; DE/PARA diferencia observado e projetado; AKS para no ultimo nivel suportado; PTU nao promete saving sem custo.

### G10 - Cutover, regressao e documentacao

**Fazer:** comparar tela antiga/nova por escopos validos; medir consultas; migrar menu; remover duplicacao somente apos confirmar consumidores; atualizar `docs/ai`, README e diagramas; definir agenda e SLO dos cubos.

**Saidas:** nova experiencia como entrada principal e legado desativado com seguranca.

**Aceite:** smoke test ponta a ponta; reconciliacoes arquivadas; rotas antigas sem consumidores ou com deprecacao documentada; sem referencias ativas a terminologia de camada removida; rollback testado; commit e push concluidos.

## 10. Testes obrigatorios

### 10.1 Reconciliacao

- Silver x C1 por periodo, conexao e moeda;
- C1 x C2 para dimensoes comuns;
- delta C3 x diferenca entre competencias;
- C5 x decisoes vigentes e revisoes PostgreSQL;
- C8 x custo AKS elegivel e cada nivel pai/filho;
- C9 x serie PTU;
- soma de resumo x detalhe com os mesmos filtros.

### 10.2 Casos negativos

- periodo mais recente parcial;
- servico com caixa diferente;
- duas conexoes em meses diferentes;
- recurso sem inventario;
- metrica ausente versus zero;
- duas alternativas para o mesmo recurso;
- preco nao comparavel;
- revisao pendente;
- cluster sem Container Insights;
- pesos AKS abaixo/acima de 1;
- PTU sem custo associado;
- materializacao falhando antes do swap.

### 10.3 Produto

- filtros consistentes entre abas;
- deep link preserva escopo;
- pagina de detalhe nao trava com cardinalidade alta;
- vazio, parcial, erro e desatualizado possuem mensagens diferentes;
- moeda e periodo aparecem em todos os KPIs financeiros;
- tabela e grafico mostram os mesmos totais;
- acesso e exportacao respeitam autorizacao.

## 11. Criterio de pronto global

O trabalho termina quando um usuario consegue:

1. abrir o Centro de Decisao e saber qual competencia e confiavel;
2. entender a variacao de custo e chegar aos recursos que a explicam;
3. ver cobertura e valor financeiro dos gaps;
4. priorizar uma oportunidade por saving, risco, confianca e idade;
5. abrir seu DE/PARA com serie e premissas;
6. revisar a decisao sem alterar a evidencia deterministica;
7. navegar no custo AKS ate o nivel realmente suportado e reconciliado;
8. identificar capacidade PTU ociosa/saturada sem confundir sinal com economia;
9. distinguir saving identificado, aprovado e realizado;
10. obter o mesmo total no resumo, no detalhe e na query de reconciliacao.

## 12. Primeira resposta exigida da IA executora

Antes de implementar codigo, produzir **somente G0** em `docs/ai/gold-decision-dashboard-execution-log.md`:

1. baseline reproduzido com data, comandos sanitizados e divergencias;
2. inventario de `gold_rule`, tabelas Gold e consumidores reais;
3. dicionario inicial das metricas da secao 4;
4. matriz dos cubos C1-C12 com `reusar/alterar/criar/aguardar dado`;
5. desenho concreto da classificacao de competencia completa;
6. mapa widget -> endpoint -> cubo para as sete abas;
7. arquivos previstos para G1 e riscos de migracao;
8. `git status` inicial, preservando mudancas do usuario.

Nao codificar G1 na mesma execucao de G0. Se algum numero divergir, atualizar primeiro o log e indicar se a divergencia muda o contrato ou apenas o baseline.
