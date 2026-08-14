# 06 — Glossário do Domínio

Termos usados no código, na UI e na documentação, em pt-BR (língua do domínio).

## Termos FinOps / de dados

| Termo | Significado no sistema |
|---|---|
| **FOCUS** | FinOps Open Cost and Usage Specification — schema padronizado de billing. A plataforma suporta destinos FOCUS v1.4, mas nenhum nome de coluna FOCUS é obrigatório/universal |
| **Gold / Silver / Bronze** | Camadas medallion do lakehouse: Bronze = bruto imutável; Silver = mapeado ao destino; Gold = analítico final com regras aplicadas |
| **`tipo`** | Coluna discriminadora: `'realizado'` (fato) vs `'forecast'` (projeção). Nunca se sobrepõem no mesmo período |
| **`realizado`** | Custo efetivamente realizado; também nome da medida default de forecast quando presente na Gold |
| **`_billing_period`** | Período de faturamento `YYYYMM` derivado do caminho do blob; usado para particionar, vigência de regras e desambiguação de datas |
| **cost_type** | `actual` (custo real) vs `amortized` (amortizado, reservas/Savings Plans) |
| **On-Demand** | Preço público de lista, usado como referência para precificar uso coberto por reservas no Clone Engine |
| **Clone / linha sintética** | Linha gerada pelo Clone Engine (`is_synthetic=true`) com valor negativo de estorno, pareada a uma linha original que recebe o custo On-Demand |
| **Pendência de preço** | Registro em `clone_price_pendency` quando não existe preço On-Demand de referência para um meter — requer ação do usuário |

## Termos organizacionais

| Termo | Significado |
|---|---|
| **Diretoria** | Topo da hierarquia organizacional (department raiz) |
| **Departamento** | Nó da árvore `department` (até 5 níveis); carrega `centro_custo`, `ambiente`, `custom_fields` herdáveis |
| **Centro de custo** | Código contábil atribuído via herança de departamento |
| **Ambiente** | Classificação de ambiente (ex.: Produção, Sandbox) herdada do departamento |
| **Projeto** | Entidade ligada a um departamento com mapeamento `subscription_rg` (subscription/resource-group) |
| **`department_path`** | Caminho completo `"Raiz > Filho > Neto"` gerado pela herança |
| **"Não classificado"** | Label default (`null_dimension_label`) para dimensões nulas no forecast |

## Termos de processamento

| Termo | Significado |
|---|---|
| **Scan** | Listagem de blobs na origem comparando etag/last_modified com o já ingerido (NEW/CHANGED/UNCHANGED) |
| **Extração** | Task `extraction`: download de blobs → Bronze |
| **Reprocessamento** | Task `reprocess`: Bronze → mappings → clone → business rules → classificação → Gold (staging + swap atômico) |
| **First match wins** | Regra de precedência: primeira regra (menor `priority`) que preenche um valor vence; nada sobrescreve |
| **`_canon`** | Normalização canônica (sem acentos, minúsculas, só alfanuméricos) usada em toda comparação de classificação |
| **Cutoff (D-n)** | `cutoff_days`: só dados até n dias antes da execução contam como realizado; o restante do mês é projetado |
| **WAPE** | Weighted Absolute Percentage Error — métrica do backtest (`Σ\|real−pred\| / Σ\|real\|`) usada para escolher o método no AUTO_BACKTEST |
| **Baseline** | Média diária selecionada que serve de base às projeções |
| **Janelas (windows)** | Médias móveis de 7, 14 e 28 dias; `recent_weight` pondera curta vs longa no método WEIGHTED |
| **Snapshot (forecast_run)** | Congelamento do contrato de execução (`config_snapshot`) garantindo auditabilidade mesmo após deleção da config |
| **Worker** | Processo separado que consome a fila `etl_task`; identificado por heartbeat em `/tmp/openfinops-worker.heartbeat` |
| **Fila** | Tabela `etl_task` — única fonte de verdade do estado das tasks entre API e worker |

## Siglas de provedores e billing

| Termo | Significado |
|---|---|
| **PTAX** | Taxa de câmbio BRL/USD (regra `PTAX_BRL` = valor × taxa) |
| **PayG** | Pay-As-You-Go (preço sob demanda Azure, coluna `PayGPrice`) |
| **meter / meter_id** | Identificador do medidor de consumo do provedor (chave de preço no clone) |
| **pricing_model / pricingcategory** | Modelo de cobrança da linha (OnDemand, Reservation, SavingsPlan...) |
| **charge_type** | Tipo de cobrança no forecast: Purchase, Usage, Refund, Credit, Recurring commitment |
