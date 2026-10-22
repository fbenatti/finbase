# E9 — Dashboard de anomalias

Etapa **E9** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

> "O usuário identifica valor, causa, responsável, ação pendente e evidência de
> resolução no mesmo fluxo."

| O que o usuário precisa ver | Onde está na tela |
|---|---|
| **Valor** | coluna "Custo anômalo" na lista; gráfico observado × faixa × área anômala no detalhe |
| **Causa** | campo `cause_category` no painel de etapas e na aba Retrospectiva |
| **Responsável** | coluna "Responsável" (ou "fila FinOps") e painel de etapas |
| **Ação pendente** | tabela de ações com estado e prazo; "próxima ação" no painel de etapas |
| **Evidência de resolução** | histórico de eventos, `verification_note`, aprendizado e medida preventiva |

---

## 1. Arquivos

| Arquivo | Papel |
|---|---|
| `apps/web/src/pages/Anomalies/Anomalies.tsx` | página `/anomalies` completa |
| `apps/web/src/services/anomaliesApi.ts` | cliente tipado da API (17 endpoints) |
| `apps/web/src/App.tsx` | rota `anomalies` (lazy) |
| `apps/web/src/layout/Layout.tsx` | item de menu "Anomalias" |
| `apps/web/src/auth/moduleMap.ts` | rota → módulo RBAC `anomalies` |

## 2. Visões

| Aba | Conteúdo |
|---|---|
| **Painel** (`/alertas/painel`, antiga `/alertas/visao-geral`) | Quatro KPIs: abertas e críticas (`GET /anomalies/metrics`), impacto em R$ (`GET /anomalies/impact/summary`, excesso já incorrido) e tempo para resolver (mediana). Tendência diária de casos e de impacto, impacto por serviço e por nó da hierarquia (cloud › subconta), lista de casos priorizada por impacto com ação **Abrir investigação**, e situação dos casos. Os gráficos e a lista usam `GET /anomalies/cases` (até 200 casos por prioridade). Notas do backend declaram que falsos negativos não são estimados e que aceitação não é falso positivo. Saída retirada: blocos de saving e exposição (já na aba Impacto), contadores de sem dono, encerrados, classificados e famílias recorrentes, e chips de decisão |
| **Impacto** | `GET /anomalies/impact/summary`: custo já incorrido, saving projetado e exposição em aberto (mês e ano) |
| **Meus casos / fila FinOps** | lista com filtros por estado, severidade e "somente fila FinOps" |
| **Casos** | mesma lista, ciclo completo de estados |
| **Eventos esperados** | `GET /anomalies/expected-events`: recorrências agrupadas por família, com ocorrências, séries, valores esperado/observado/desvio, meses e confiança (F4) |
| **Qualidade** | `GET /anomalies/quality`: contagem e custo anômalo por código de razão (`PARTIAL_DATA`, `REFERENCE_UNAVAILABLE`, `EXPLICIT_TOLERANCE`, `UNIFORM_FALLBACK`, `FORECAST_RULE_MISMATCH`, ...) (F4) |
| **Retrospectiva** | causa, decisão, aprendizado e medida preventiva por caso |

## 3. Gráfico (plano §5.1)

`BandChart` desenha, em SVG (sem dependência nova), o que a imagem de referência
da FinOps Foundation orienta:

- **faixa** — área azul entre limite inferior e superior;
- **linha discreta** — previsão central (tracejada);
- **observado** — barra escura;
- **área destacada** — parte observada **acima do limite superior** em vermelho
  translúcido (o custo anômalo).

Legenda numérica: desvio, custo anômalo, tipo de referência e grão de avaliação.
Para eventos mensais o grão é `MONTHLY_EVENT`/`MONTHLY_ACCUMULATED` e os valores
são do evento/acumulado — a compra **não** é espalhada ao longo dos dias.

## 4. Detalhe do caso

- **Painel de etapas**: detecção, atribuição, ciência, investigação, decisão,
  remediação, verificação, retrospectiva e encerramento, com data, responsável,
  prazo de ciência e próxima ação.
- **Ações**: listar, adicionar ação corretiva, concluir (grava `executed_at`).
- **Comando do workflow**: seletor com os 15 comandos, campos condicionais
  (decisão/causa, evidências, resultado de verificação, aprendizado, medida
  preventiva) e envio com `case_version` — conflito de versão aparece como
  alerta 409.
- **Histórico de eventos**: transições auditadas com de → para, versão e nota.
- **Motivos e regras**: `reason_codes` e `applied_rule_refs` com o estado de
  aplicação de cada regra.

## 5. Caixa de notificações

Ícone com contador de não lidas; a gaveta lista as notificações (com destaque
para a fila FinOps), permite **"Assumir / confirmar ciência"** e abrir o caso.

## 6. Configuração

A barra superior mostra medida, moeda, perfil, quantil, materialidade, janelas e
horizonte, com ações **Habilitar/Desabilitar** e **Executar detecção** (enfileira
`anomaly_type='anomaly_detection'`). A configuração nasce desabilitada; habilitar
é decisão explícita.

## 7. Verificação

```text
npx tsc -b          → sem erros
npx vite build      → ✓ built in 19,95s (chunk Anomalies-*.js gerado)
GET /src/pages/Anomalies/Anomalies.tsx (Vite dev) → 200
GET /anomalies/configs        → 200
GET /anomalies/cases/summary  → 200
```

## 7.1 Série diária (F2)

`GET /anomalies/cases/{id}/series?horizon_days=15` deriva a grade diária da
referência compacta, **sem persistir** nada e sem copiar valores para o
PostgreSQL:

```text
esperado_dia  = previsto_mês × peso[dia da semana] / Σ pesos do intervalo coberto
limite_sup    = esperado_dia + offset diário da faixa
custo_anômalo = max(0, observado_dia − limite_sup)
```

O gráfico diário (`SeriesChart`) desenha a faixa (polígono), a previsão central
(tracejada), a linha observada com marcadores e as barras vermelhas dos dias
acima do limite. Quando a série não existe, o gráfico cai para os escalares do
caso.

Verificado em Trino real (`scripts/verify_anomaly_series.py`):

```text
fonte: silver.dest_modelo_focus dimensões=18
série escolhida: {'cloud_origem': 'azure-ti', 'servicename': 'Microsoft.Compute'}
linhas observadas por dia (1ª a 13/10): 13
série derivada: 31 dias; soma esperada=31.000,00 (forecast 31.000,00)
   dia útil médio=1.210,94  fim de semana médio=484,38
```

## 8. Limitações declaradas
- A série é lida por dia a partir da fonte observada; cada abertura de caso faz
  uma consulta no Trino (não há cache). Para listas grandes, o ideal é uma
  leitura em lote por publicação.
- Sem biblioteca de gráficos no projeto (não há `recharts`/`echarts`), o desenho
  é SVG próprio; a interatividade é limitada a tooltip/legenda.
- As consultas de eventos esperados e qualidade são leituras sob demanda no
  Trino (amostra de 200 avaliações por consulta, refinável por período); não há
  cache nem agregação materializada.
