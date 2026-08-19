# 03 — Regras de Negócio

Este é o documento central do domínio. Todas as regras são **metadata-driven**:
configuradas pelo usuário, persistidas no PostgreSQL, versionadas e aplicadas pelo
pipeline. Nenhuma regra financeira está "chumbada" no código.

## 3.1 Mapeamento origem→destino

`SourceDestinationMapping` (+ `_apply_mapping_sync` em `task_runner.py`):

| transform_type | Comportamento |
|---|---|
| `RENAME` | Renomeia coluna origem → destino |
| `COPY` | Copia valor para nova coluna (não sobrescreve existente) |
| `FORMULA` | `df.eval(source_column)` → coluna destino (falhas são ignoradas silenciosamente) |

## 3.2 Business Rules (`business_rule` + `expression_engine.py` + `_apply_business_rule_sync`)

Princípios:

- **"First match wins"**: regras aplicadas em ordem de `priority`; linhas já preenchidas
  por regra de maior prioridade NUNCA são sobrescritas (checagem de `isna()`/string vazia).
- **Vigência**: `valid_from`/`valid_until` (YYYYMM) comparam com o billing period do blob.
- **Resolução case-insensitive de colunas**: regras são configuradas com nomes lógicos,
  mas o schema físico da Gold pode ter casing diferente — `_apply_business_rule_sync`
  constrói mapa `by_lower` e reescreve a expressão/condições para os nomes físicos
  (regex com word boundaries, nomes mais longos primeiro para evitar substituições parciais).

### Tipos de regra

| rule_type | Comportamento |
|---|---|
| `EXPRESSION` | Avalia `expression` aritmética via `pandas.eval` (engine python), ex.: `unitPrice * quantity * 13.83` → `target_column`. Colunas são coerced para numérico (NaN→0). Constantes globais via `GLOBAL_CONSTANTS` |
| `CONDITIONAL` | Lista de condições if/then: `{field, op (==, !=, contains, in), value, logic (AND/OR), then_field, then_value}`. `then_value` pode ser literal numérico, texto ou expressão calculada (ex.: `billedcost * 0.1383`) |
| `PTAX_BRL`, `TAX_BASE`, `TAX_MARKETPLACE`, `MARKUP` | Multiplicação simples: `target = source_column * rate` (config_json) |

## 3.3 Engine de Classificação (`classification_engine.py`)

Atribui dimensões organizacionais (diretoria, departamento, centro de custo, ambiente)
às linhas de custo.

### Normalização canônica `_canon()`

Strip de acentos (NFKD→ASCII), lowercase, remove tudo não-alfanumérico. Toda comparação
de classificação é feita sobre valores canônicos (tanto o valor da linha quanto o padrão
da regra). Idêntico ao `finopsNew core/references.py`.

### Sintaxe multi-condição

```
meterCategory=azuredefender|microsoftdefenderforcloud ; subscriptionName~Sandbox
```

- `=` — igualdade canônica exata
- `~` — contains canônico
- `!=` — diferente
- `|` — OR entre valores do mesmo campo
- `;` — AND entre campos diferentes

Formato canônico: `conditions` JSONB `[{field, operator, values, logic}]` em colunas **destino** (FOCUS/Gold).

`normalize_rule_conditions()` deriva condições de registros legados (`source_field` + `pattern` + `pattern_type`) apenas para leitura/migração. Regras sem condição utilizável **não casam nenhuma linha**.

Campos legados como `resourceGroupName` são mapeados para `source_resource_group_name` na normalização (Azure).
Regras GCP devem casar em `SubAccountId` (project id); aliases legados `projectId`/`projectName` normalizam para `SubAccountId`/`SubAccountName`.

### Semântica de aplicação (`apply_classification_rules` / `_apply_classifications_sync`)

1. Regras ativas ordenadas por `priority`, filtradas por `destination_table_id` e `cloud_scope`.
2. **Regra sem `department_id` é ignorada** — classificação sem departamento não é alocação organizacional válida.
3. First-match-wins por linha: primeira regra que casa classifica; as demais não tocam linhas já preenchidas.
4. Ao casar: preenche `target_field` com `match_value` e herda os campos do departamento.
5. **Não há fallback em runtime** via `project.subscription_rg` — toda alocação deve existir como `ClassificationRule` editável na UI.

### Herança hierárquica de departamentos (`_build_department_path`)

Sobe a árvore de `department` até a raiz (máx. 5 níveis) e aplica **da raiz para baixo**
(filho sobrescreve pai):

- `department_name` → nome do departamento mais profundo
- `department_path` → `"Raiz > Nível2 > Nível3"`
- `centro_custo`, `ambiente` → herdados, filho tem precedência
- `custom_fields` (JSON) → merge, chave normalizada para `lower_snake_case`

Performance: a versão sync (`_apply_classifications_sync`) pré-carrega departamentos,
projetos e hierarquias em dicionários (zero queries no loop) e usa `_evaluate_multi_condition`
vetorizado com cache de colunas canônicas.

## 3.4 Clone Engine (`clone_engine.py`) — estornos On-Demand

Gera linhas sintéticas ("clones" negativos) para representar devolução/estorno de
custos que apareceram com valor zerado de billing (ex.: reservas/Savings Plans onde
o uso real precisa ser precificado a On-Demand e depois estornado).

### Pipeline por regra (ordenadas por `priority`, respeitando vigência)

1. **Seleção de escopo** (`_matches`): condições `{field, op, value}` com operadores
   `= / IN`, `!= / NOT_IN`, `CONTAINS`, `NOT_CONTAINS`, `EMPTY`, `NOT_EMPTY`, `>`, `<`.
   Filtro adicional por `provider` (prefixo de `cloud_origem`) e **somente linhas com
   billed cost = 0** são elegíveis.
2. **Preço de referência** (`_prime_prices`): constrói mapa `(cloud_origem, meter_id) →
   (período, unit_price)` a partir de linhas cujo `pricing_model` = `on_demand_value`
   (default "OnDemand"), preferindo o período mais recente. Acumulado entre períodos.
3. **Cálculo**: `amount = |price × quantity × exchange_rate|` (zero → descartado).
4. **Linha original**: recebe `billed/effective = amount`, `is_synthetic=false`,
   `tipo='realizado'` — ela passa a representar o custo real On-Demand.
5. **Clone (estorno)**: cópia com `billed/effective = -amount`, `is_synthetic=true`,
   `tipo='realizado'`, e auditoria: `clone_source_record_id`, `clone_rule_id`,
   `clone_rule_version`, `ondemand_reference_price`, `ondemand_reference_period`.
6. **Pendência**: se não há preço On-Demand de referência, registra em
   `clone_price_pendency` (visível na UI para ação do usuário) em vez de falhar.

### Vocabulário de transformações (`_apply_changes`)

Aplicável a `original_changes` e `clone_changes`. **Nenhuma expressão/SQL/código do
usuário é executado** — apenas operações declarativas:

`SET` (default para valores legados), `ZERO`, `NEGATE`, `ABS`, `COPY_COLUMN`,
`HISTORICAL_ON_DEMAND_COST` (usa o amount calculado).

Templates FOCUS são semeados no startup (`ensure_focus_templates` em `clone_rules.py`).

## 3.5 Períodos (`period_service.py`)

`period_status` controla fechamento contábil: `ensure_period`, `can_process_period`,
`close_period`, `reopen_period`, `auto_close_unchanged_periods` (fecha períodos sem
mudanças), `get_periods_report`. Período tem formato `YYYYMM`.

## 3.6 Forecast (motor completo — ver também `FORECAST.md`)

`forecast_service.py`. **Nunca carrega a Gold detalhada em pandas** — a agregação
diária (`SUM(measure) GROUP BY dimensões, data`) roda no Trino.

### Contrato de dados

- Somente linhas `tipo='realizado'` (ou NULL, retrocompatibilidade) alimentam o histórico.
- Se a medida configurada não existir ou estiver vazia → execução BLOQUEADA (sem fallback silencioso).
- Datas: ISO estrito via TRY_CAST; formatos com barra (`MM/DD/YYYY` primário do Azure,
  `DD/MM/YYYY`) são desambiguados contra o billing period (ex.: `08/01/2026` no período
  202608 = 1º de agosto). Parsers sempre com `TRY`.

### Cutoff (D-n)

Com `cutoff_days=2` e execução no dia 12: dias 1–10 são realizado; 11 até fim do mês são
forecast. Nunca há sobreposição realizado/forecast. `effective_cutoff = cutoff - cutoff_days`.

### Métodos matemáticos

- **Série alinhada ao calendário**: cada série é reindexada diariamente até o cutoff com
  `fill_value=0` — dias sem consumo são zero genuíno (evita que uma compra única antiga
  vire baseline diário gigante).
- **Candidatos**: `MEAN_7D/14D/28D` (conforme `enabled_windows`, exigindo
  `minimum_daily_points`) e `WEIGHTED = recent_weight × média_janela_curta +
  (1−recent_weight) × média_janela_longa` (default 70% × 7d + 30% × 28d).
- **AUTO_BACKTEST**: treina em `daily[:-7]`, valida nos últimos 7 dias, escolhe o candidato
  de menor **WAPE** (`Σ|real−pred| / Σ|real|`). Sem dados suficientes → `MEAN_AVAILABLE`.
- **Mês corrente**: `forecast_restante = baseline × dias_restantes`; realizado acumulado
  publicado separadamente com `selected_method='REALIZADO'`.
- **Meses futuros**: `valor = baseline × (1 + growth_rate)^n × dias_do_mês`.

### Regras de negócio do Forecast (`forecast_business_rule`)

Aplicadas DEPOIS do método matemático, ordenadas por `priority`, somente sobre linhas
`tipo='forecast'`:

- **Identificação obrigatória**: a regra DEVE identificar a série via
  `identification_column` + `identification_values` (a coluna precisa ser uma das
  dimensões da série) ou via `scope` (pares dimensão=valor). Regra sem identificação
  é registrada como "não aplicada" — **nunca promovida a ação global silenciosamente**
  (correção de bug histórico onde um Refund sem escopo zerava o forecast inteiro).
  Escopo fora das dimensões → erro de validação.
- **Comportamentos**: `CANCEL_ON_REFUND` (zera), `REPEAT_NEXT_MONTH`,
  `RECURRING_MONTHLY`, `RECURRING_UNTIL_END`, `REPLACE_VALUE` (usa `replacement_value`),
  com `growth_rate` composto por período e vigência `start_period`/`end_period`.
- Refunds associam-se por referência/escopo; um refund não cancela projeções não relacionadas.

### Ajustes (`forecast_adjustment`)

Aplicados após as regras, por `priority`, com `filters` dimensionais e vigência:
`PERCENTAGE`, `FIXED_VALUE`, `ADD_VALUE`, `SUBTRACT_VALUE`, `ZERO`, `CUSTOM_GROWTH`.
Sem SQL/fórmula livre.

### Imutabilidade e auditoria

- Tabela resultado: `forecast.result_run_{run_id}` — duas execuções no mesmo dia nunca
  se sobrescrevem.
- `forecast_run.config_snapshot` congela o contrato de execução; runs antigas continuam
  consultáveis mesmo após deleção da config (FK `ON DELETE SET NULL`).
- Em caso de falha, o resultado anterior é preservado.

### Perfis de agressividade (presets de UI)

| Perfil | Método | growth_rate |
|---|---|---|
| Conservador | MEAN_28 | 0,5% |
| Ponderado | WEIGHTED (70/30) | — |
| Padrão | AUTO_BACKTEST | 0,83% |
| Agressivo | MEAN_7 | 1,5% |

### Metadata automático (`fetch_metadata`)

`DESCRIBE iceberg.gold.{tabela}` no Trino: descobre medidas numéricas, colunas de data
(com prioridade: `date`, `chargedate`, `chargeperiodstart`, `billingperiodstart`,
`usage_date`), período (`_billing_period`) e dimensões textuais. Medida default:
`realizado`, depois `EffectiveCost`. **Nenhum nome de coluna de provedor é universal**;
para tabelas FOCUS a UI pode sugerir dimensões (`cloud_origem`, `departamento`,
`servicename`, `pricingcategory`, `subaccountname`, `resourcename`) somente se existirem.

### Validação (`validate_config`)

Medida numérica válida; data ou período válido; dimensões existentes; horizonte 1–36 meses;
janelas ∈ {7,14,28}; model_mode válido. A validação de config também valida colunas e
períodos das regras de negócio ativas.

## 3.7 Fila, agendamento e gatilhos

- Forecast pós-ingestão só é enfileirado quando: novos dados ingeridos (`rows_raw > 0`),
  reprocess Gold com sucesso, config ativa, worker saudável (heartbeat) e sem run
  concorrente QUEUED/RUNNING (`_queue_forecast_run` em `scheduler_service.py`).
- `_reconcile_dead_worker_tasks` (etl.py) marca como FAILED tasks presas com worker offline.
