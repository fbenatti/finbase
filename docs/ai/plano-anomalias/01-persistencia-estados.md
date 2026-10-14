# E1 — Persistência e máquina de estados

Etapa **E1** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Transições inválidas são rejeitadas | Máquina de estados declarativa com validação de estado, vocabulário e campos obrigatórios | `apps/api/tests/test_anomaly_state.py` (33 testes) |
| Workflow histórico não depende da sobrevivência da configuração editável | `anomaly_case`/`anomaly_case_event` sobrevivem ao `anomaly_config` removido; `anomaly_case_event` é imutável | `test_acoes_e_historico_sobrevivem_a_configuracao_removida`, `test_historico_de_eventos_e_imutavel` |
| Modelos PostgreSQL, schemas Iceberg e migração idempotente | 7 tabelas + contrato de 3 tabelas Iceberg + migração derivada do metadata | `app/models/anomaly.py`, `app/services/anomaly_schema.py`, `app/services/anomaly_migrations.py` |
| Permissões cadastradas | Módulo RBAC `anomalies` com escopos do plano | `role_permission` em DEV: 5 linhas |
| Configuração inicial desabilitada | `enabled = false` semeado no startup | `anomaly_config.id = 11` em DEV |

---

## 1. Tabelas PostgreSQL (controle e workflow)

As sete tabelas do plano (§4.1) foram criadas **exatamente com os nomes do
plano**. Valores financeiros e séries permanecem no Iceberg.

| Tabela | Conteúdo | Notas de projeto |
|---|---|---|
| `anomaly_config` | Forecast vinculado, perfis, parâmetros, roteamento e prazos | `measure_column` fixo em `realizado_cloud`; `currency` explícita (nunca `BillingCurrency`); `enabled` nasce `false` |
| `anomaly_run` | Execução, snapshots, configuração congelada e progresso | Guarda o contrato de referência inteiro (§2.2 do plano): `forecast_run_id`, `forecast_config_version`, `source_snapshot_id`, `data_cutoff`, `generated_at`, `target_start`, `target_end`, `reference_kind` |
| `anomaly_publication` | Publicação válida corrente | Índice único parcial `uq_anomaly_publication_current` garante **uma** publicação corrente por configuração |
| `anomaly_case` | Identidade, estado, responsáveis, dimensões de roteamento, datas e referência à avaliação atual | 59 colunas; guarda nota, classificação e os escalares de referência; **não** copia séries nem grades |
| `anomaly_case_event` | Histórico imutável de transições, ciência, decisões e reaberturas | `UPDATE`/`DELETE` bloqueados por listener de mapper e por guard de `before_flush` |
| `anomaly_action` | Ações corretivas e preventivas | `mandatory` controla o bloqueio de ida para verificação (§3.6) |
| `anomaly_notification` | Notificações internas e seus estados | `dedup_key` único = caso + evento + destinatário; `recipient_user_id` nulo + `is_finops_queue` = fila FinOps |

### 1.1 Separação de dimensões no caso

O plano exige separar estado do workflow de classificação, qualidade,
confirmação humana e evolução financeira. No modelo:

| Dimensão | Onde vive |
|---|---|
| Estado do workflow | `anomaly_case.state` (+ `state_changed_at`) |
| Classificação do detector | `score`, `severity`, `reason_codes`, `applied_rule_refs` |
| Qualidade dos dados | `data_quality` |
| Confirmação humana | `acknowledged_at`, `acknowledged_by_id` |
| Evolução financeira | `observed_cloud`, `expected_cloud`, `lower/upper_cloud`, `delta_cloud`, `anomalous_cost_cloud` |

Assim a detecção pode deixar de disparar (`score` cai) sem alterar o estado
operacional do caso.

### 1.2 Concorrência

Toda transição exige `case_version`. Divergência levanta
`AnomalyVersionConflict` → **HTTP 409** (plano §5.3). O modelo não usa
`version_id_col` do SQLAlchemy de propósito: a versão é um valor de contrato,
incrementado pela transição, e não um detalhe de ORM.

---

## 2. Máquina de estados

`apps/api/app/services/anomaly_state.py` — Python puro, sem banco, para permitir
teste hermético.

### 2.1 Estados

```text
NEW → ASSIGNED → INVESTIGATING → DECIDED → IN_REMEDIATION → VERIFYING
                                                         ↘ RETROSPECTIVE → CLOSED
```

`RETROSPECTIVE` também é alcançável direto de `DECIDED` (decisões sem ação) e de
`VERIFYING` (validação humana). `CLOSED → INVESTIGATING` é a reabertura.

### 2.2 Comandos

Transições são **comandos explícitos**; `status` nunca é editado livremente.

| Comando | De | Para | Campos obrigatórios |
|---|---|---|---|
| `assign` | `NEW`, `ASSIGNED` | `ASSIGNED` | `assignee_user_id` |
| `acknowledge` | `NEW`, `ASSIGNED`, `INVESTIGATING` | mesmo estado | responsável definido |
| `return_to_queue` | `NEW`, `ASSIGNED` | `NEW` | `note` |
| `start_investigation` | `ASSIGNED` | `INVESTIGATING` | — |
| `update_investigation` | `INVESTIGATING` | mesmo estado | — |
| `decide` | `INVESTIGATING` | por decisão | `decision`, `cause_category`, `note`, evidência |
| `redecide` | `DECIDED` | por decisão | idem |
| `start_remediation` | `DECIDED` | `IN_REMEDIATION` | ≥ 1 ação cadastrada |
| `cancel_remediation` | `IN_REMEDIATION` | `DECIDED` | `note` |
| `start_verification` | `IN_REMEDIATION`, `DECIDED` | `VERIFYING` | `remediation_started_at`; nenhuma ação obrigatória pendente |
| `fail_verification` | `VERIFYING` | `IN_REMEDIATION` | `note` |
| `validate_result` | `VERIFYING` | `RETROSPECTIVE` | `verification_outcome` ∈ {`SUCCESS`, `CONFIRMED_ABSENCE`}; `verification_note` no sucesso |
| `record_retrospective` | `DECIDED`, `VERIFYING`, `RETROSPECTIVE` | `RETROSPECTIVE` | `learning` + (`preventive_measure` ou `no_preventive_measure_reason`) |
| `close` | `RETROSPECTIVE` | `CLOSED` | ver §2.4 |
| `reopen` | `CLOSED` | `INVESTIGATING` | `note` |

### 2.3 Decisão escolhe o caminho

`decide`/`redecide` resolvem o destino pela decisão (plano §3.5):

| Decisão | Destino |
|---|---|
| `EXPECTED` | `RETROSPECTIVE` |
| `DATA_ISSUE` | `RETROSPECTIVE` |
| `CONFIRMED_ACCEPTED` | `RETROSPECTIVE` |
| `LOW_PRIORITY_NO_ACTION` | `RETROSPECTIVE` |
| `CONFIRMED_ACTION` | `IN_REMEDIATION` |

Baixa prioridade não é contada como falso positivo e ganho aceito não é erro do
detector — por isso ambos seguem para retrospectiva com justificativa, e não
para um estado de "descartado".

### 2.4 Encerramento

`close` exige, cumulativamente:

1. decisão registrada (`decision`);
2. causa determinada **ou** `cause_category = UNDETERMINED` explícito;
3. evidência (`evidence`);
4. `learning`;
5. `preventive_measure` **ou** `no_preventive_measure_reason`;
6. resultado de verificação quando houve remediação
   (`verification_succeeded_at` ou `verification_outcome`).

O responsável pelo encerramento é gravado fora da máquina de estados
(`closed_by_id`), pela camada de API, porque é identidade do ator e não regra de
negócio.

### 2.5 Vocabulários fechados

```text
CAUSE_CATEGORIES = LEGITIMATE_GROWTH | EXPECTED_PURCHASE | MISCONFIGURATION |
                   FORGOTTEN_RESOURCE | PRICE_OR_DISCOUNT_CHANGE | DATA_ISSUE |
                   SECURITY_INCIDENT | UNDETERMINED

DECISIONS        = EXPECTED | DATA_ISSUE | CONFIRMED_ACTION |
                   CONFIRMED_ACCEPTED | LOW_PRIORITY_NO_ACTION
```

Valor fora do vocabulário é rejeitado com mensagem que lista os permitidos.

---

## 3. Contrato de schema Iceberg

`apps/api/app/services/anomaly_schema.py` centraliza o contrato para o escritor
(E6), a API e o dashboard (E9).

| Tabela | Papel |
|---|---|
| `silver.dest_modelo_focus_anomaly_reference` | Referências compactas e imutáveis: **uma linha por forecast × série × mês** |
| `silver.dest_modelo_focus_anomalies` | Avaliações compatíveis com Planning |
| `gold.dest_dm_cost_anomalies` | Avaliação financeira consolidada do caso |

Decisões de contrato verificadas por teste:

- a referência **não tem coluna de grade diária** — tem `reference_month`,
  `covered_days`, `weekday_weights`, `normalizer`, `distribution_method` e a
  faixa (`band_lower_cloud`, `band_upper_cloud`, `band_method`);
- a avaliação preserva as 18 dimensões de Planning;
- a linha de anomalia tem `tipo = 'anomalia'`, `realizado` **nulo**,
  `realizado_cloud = anomalous_cost_cloud` e `is_financial_additive = false`
  (plano §4.3), garantido por `assessment_financial_guards()`.

---

## 4. Migração idempotente

`apps/api/app/services/anomaly_migrations.py`, chamado no startup da API.

Mecanismo:

1. `Base.metadata` é a fonte de verdade das colunas;
2. tabelas ausentes são criadas; colunas ausentes em tabela existente são
   adicionadas com `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`;
3. `DEFAULT` das colunas NOT NULL é convergido com `ALTER COLUMN ... SET DEFAULT`
   (o `create_all` não evolui tabela existente);
4. índice único parcial da publicação corrente é recriado;
5. uma correção de dados estreita garante materialidade não vazia;
6. a configuração inicial é semeada **desabilitada** e vinculada ao forecast
   ativo.

### 4.1 Evidência em PostgreSQL real

`apps/api/scripts/verify_anomaly_schema.py` (DEV, `localhost:5433`):

```text
1ª execução: tabelas criadas=[] colunas=0 config=None
2ª execução: tabelas criadas=[] colunas=0 config=None
defaults: {'materiality_by_currency': '\'{"BRL": 100.0}\'::jsonb',
           'enabled': 'false', 'measure_column': "'realizado_cloud'::character varying",
           'currency': "'BRL'::character varying", 'profile': "'BALANCED'::character varying"}
OK: migração idempotente verificada em PostgreSQL real.
```

Configuração semeada em DEV:

```text
id | name                           | measure_column  | currency | profile  | materiality_by_currency | enabled
11 | Detecção de anomalias (padrão) | realizado_cloud | BRL      | BALANCED | {"BRL": 100.0}          | f
```

### 4.2 Armadilhas encontradas

| Armadilha | Sintoma | Correção |
|---|---|---|
| Colunas NOT NULL sem `server_default` | `NotNullViolationError` no seed por SQL direto; API não subia | `server_default` no modelo + `SET DEFAULT` na migração |
| `server_default` com `::jsonb` | `unrecognized token ":"` no SQLite dos testes herméticos | literal sem cast (`'[]'`), coagido para `json` no PostgreSQL |
| Colunas `JSON` são `json`, não `jsonb` | `operator does not exist: json = jsonb` | cast explícito na comparação |
| FKs sem os modelos referenciados importados | `NoReferencedTableError` ao compilar o DDL | imports de `forecast`, `etl_task`, `identity`, `destination` no módulo de migração |

---

## 5. Permissões

Módulo RBAC novo: **`anomalies`**.

| Verbo do plano | Permissão |
|---|---|
| consultar | `(anomalies, read)` |
| configurar/processar | `(anomalies, create)` |
| atribuir | `(anomalies, update, "assign")` |
| investigar | `(anomalies, update, "investigate")` |
| decidir | `(anomalies, update, "decide")` |
| validar/encerrar | `(anomalies, update, "close")` |
| administrar | `(anomalies, delete)` |

Comandos do workflow são `POST /anomalies/cases/{id}/transition`; o marcador
`/transition` já existe em `UPDATE_POST_MARKERS`, então o middleware aplica
`update` no módulo e o escopo específico é exigido por
`Depends(require("anomalies", "update", <escopo>))` no router.

Backfill aplicado (semeado no startup):

```text
 Administrador | anomalies | create |
 Administrador | anomalies | delete |
 Administrador | anomalies | read   |
 Administrador | anomalies | update |
 Leitor Global | anomalies | read   |
```

---

## 6. Testes

```text
apps/api/tests/test_anomaly_state.py           33 testes — máquina de estados
apps/api/tests/test_anomaly_schema.py          11 testes — persistência e imutabilidade
apps/api/tests/test_anomaly_schema_contract.py 12 testes — contrato Iceberg
apps/api/scripts/verify_anomaly_schema.py      verificação em PostgreSQL real
```

Suíte completa: **671 passaram**, 18 falhas **pré-existentes** (confirmado por
execução de linha de base sem as alterações desta etapa: `test_etl_task_claim`,
`test_shared_cost`, `test_gold_daily_consumption`).

## 6.1 Imutabilidade no banco (F3)

O listener do ORM cobre a aplicação; o **gatilho** cobre SQL textual:

```sql
CREATE OR REPLACE FUNCTION anomaly_case_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'anomaly_case_event é um histórico imutável: registre um novo evento '
        '(tentativa de % na linha %)', TG_OP, COALESCE(OLD.id, 0);
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anomaly_case_event_immutable
    BEFORE UPDATE OR DELETE ON anomaly_case_event
    FOR EACH ROW EXECUTE FUNCTION anomaly_case_event_immutable();
```

Casos **não** são apagados: o ciclo termina em `CLOSED`, nunca em `DELETE`. Por
isso o gatilho pode bloquear o `DELETE` em cascata sem efeito colateral.

Verificado em PostgreSQL real:

```text
gatilhos em anomaly_case_event: ['trg_anomaly_case_event_immutable']
   UPDATE: bloqueado (mensagem correta)
   DELETE: bloqueado (mensagem correta)
```

### 6.2 Defaults convergidos no banco

`ADD COLUMN` em tabela existente não impõe NOT NULL nem default. A migração
converge os defaults de todas as colunas NOT NULL que só tinham default do ORM
(`series_dimensions`, `score`, `evaluation_grain`, `valid_days_streak`,
`reopen_count`, `status`, `selected_models`, `is_current`, `payload`, ...), para
que INSERT por SQL direto — script operacional, verificação, backfill — funcione
sem depender do ORM.

## 7. Limitações declaradas

- As tabelas Iceberg são criadas na primeira publicação (E6), não no startup:
  criar tabela vazia em produção antes de existir dado publicável tornaria a
  publicação "corrente" indistinguível de uma tabela vazia.
- O gatilho bloqueia mutação de `anomaly_case_event` inclusive em cascata. Se um
  dia for necessário apagar um caso inteiro, será preciso desabilitar o gatilho
  explicitamente — decisão consciente, não acidente.
