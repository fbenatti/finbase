# Plano de anomalias de custo cloud — documentação de execução

Implementação do plano **Gestão de anomalias de custo cloud: detecção até
encerramento** no OpenFinops.

Documento de referência externa: [Managing Cloud Cost
Anomalies](https://www.finops.org/wg/managing-cloud-cost-anomalies/) (FinOps
Foundation) e a [imagem de
referência](https://www.finops.org/wp-content/uploads/2023/03/managing-anomalies-6.png).

## Contrato financeiro (não negociável)

```text
realizado_cloud observado na data do consumo/cobrança
    versus
realizado_cloud esperado antes desse evento
```

- `realizado` não participa da comparação.
- `_billing_period` é apenas procedência (poda de partição e desambiguação de
  parsing são permitidas; seleção/deslocamento/limite do dia analisado não).
- A janela usa a **data completa do consumo/cobrança, normalizada em UTC**.
- Não aguardar fechamento de fatura.
- Não excluir GCP nem criar tratamento fixo por nome de provedor.
- Não substituir `realizado_cloud` por outra medida quando ausente.
- Preservar as regras existentes de compras recorrentes, cancelamento e
  validade da fonte.

## Índice

| Etapa | Documento | Conteúdo |
|---|---|---|
| E0 | [00-contrato-cloud.md](00-contrato-cloud.md) | Auditoria da origem, moeda, filtros, datas UTC, regras aplicadas e cardinalidade de `realizado_cloud` |
| E1 | [01-persistencia-estados.md](01-persistencia-estados.md) | Modelos PostgreSQL, schemas Iceberg, máquina de estados, permissões |
| E2 | [02-referencia-forecast.md](02-referencia-forecast.md) | Adaptador de snapshots, seleção prospectiva/retrospectiva, `applied_rule_refs` |
| E3 | [03-distribuicao-faixa.md](03-distribuicao-faixa.md) | Referência compacta de 15 dias, pesos semanais, calibração, perfis e materialidade |
| E4 | [04-detectores-episodios.md](04-detectores-episodios.md) | Detector diário, evento mensal, acumulado, nota e agrupamento em episódios |
| E5 | [05-retrospectiva-dados.md](05-retrospectiva-dados.md) | Reconstrução histórica em lotes |
| E6 | [06-worker-publicacao.md](06-worker-publicacao.md) | Worker, publicação consistente e composição com Planning |
| E7/E8 | [07-atribuicao-investigacao.md](07-atribuicao-investigacao.md) | Roteamento, fila FinOps, notificações, investigação e decisão |
| E8 | ver [07-atribuicao-investigacao.md](07-atribuicao-investigacao.md) | Ações, verificação de três dias válidos, retrospectiva e encerramento |
| E9 | [09-dashboard.md](09-dashboard.md) | Gráfico observado/faixa/área anômala, painel de etapas, KPIs |
| E10 | [10-validacao-ativacao.md](10-validacao-ativacao.md) | Os 18 testes obrigatórios, regressões e piloto |
| F1–F5 | [11-operacionalizacao.md](11-operacionalizacao.md) | Agendamento, série diária, imutabilidade no banco, eventos esperados/qualidade e o **piloto real ponta a ponta** |

## Item de dados do plano

Os nomes de tabela do plano (`silver.dest_modelo_focus_anomaly_reference`,
`silver.dest_modelo_focus_anomalies`, `gold.dest_dm_cost_anomalies`) foram
mantidos como **contrato nominal**. A E1 confirma a correspondência com as
tabelas reais do catálogo antes de criá-las.

## Log de execução

| Etapa | Data | Entrega | Testes | Limitações |
|---|---|---|---|---|
| E0 | 2026-10-14 | `00-contrato-cloud.md` — auditoria completa com medições reais | Medições executadas via Trino (cardinalidade, parsing de datas, moeda, medidas) | `applied_rule_refs` ainda não existe no motor de forecast (escopo E2); `realizado_cloud` ausente no cubo Gold |
| E1 | 2026-10-14 | 7 tabelas PostgreSQL, máquina de estados, contrato Iceberg, permissões, configuração inicial desabilitada | 56 testes novos; migração idempotente verificada em PostgreSQL real; suíte completa sem regressão | Endpoints HTTP são E7; tabelas Iceberg criadas na primeira publicação (E6) |
| E2 | 2026-10-14 | Adaptador de snapshot, contrato de referência compacta, rastro estruturado de regras e seleção prospectiva/retrospectiva | 30 testes novos; reconciliação real snapshot × referência ao centavo em 14.111 linhas | Faixa, pesos semanais e normalizador completos são E3 |
| E3 | 2026-10-14 | Distribuição por dia da semana, calibração de faixa por quantil unilateral, perfis, tolerâncias e materialidade | 30 testes novos; distribuição reconcilia em dados reais (3,33 M em 31 dias); faixa calibrada com 181 resíduos | Faixa por série é obrigatória na detecção (E4) |
| E4 | 2026-10-14 | Detectores diário/mensal/acumulado, nota e severidade, status de avaliação e consolidação de episódios | 57 testes novos; nenhuma regressão na suíte | Detector é puro: leitura observada e persistência são E6 |
| E5 | 2026-10-14 | Reconstrução histórica em lotes, preferência por execução histórica, limitações declaradas e guarda de não publicar Planning | 22 testes novos; plano real encontrou referências históricas para 202608–202610 | Reconstrução roda uma execução por mês; publicação corrente é E6 |
| E6 | 2026-10-14 | task_type anomaly_detection, publicação atômica, composição com Planning e ciclo de vida dos casos | 21 testes novos; PostgreSQL real: falha preserva a publicação, reprocessamento não duplica caso | Notificações são E7; Iceberg e PostgreSQL não formam transação distribuída |
| E7/E8 | 2026-10-14 | Workflow completo por comandos explícitos: roteamento, fila FinOps, caixa de notificações, decisão, ações, verificação de três dias válidos e encerramento auditado | 20 testes novos; 17 endpoints publicados e respondendo | Agendador de vencimento e reavaliação automática da verificação pendentes |
| E9 | 2026-10-14 | Página `/anomalies` com visão geral, fila, casos, eventos esperados, qualidade, retrospectiva, gráfico observado/faixa/área anômala e painel de etapas | `tsc -b` sem erros; `vite build` OK; página serve em dev (200) | Gráfico usa os escalares do caso; série diária de 15 pontos é extensão |
| E10 | 2026-10-14 | Suíte de aceite com os 18 testes obrigatórios do plano, regressões e piloto manual | 20 testes de aceite passando; 861 na suíte; 18 falhas pré-existentes confirmadas por linha de base | Agendamento mantido desligado (piloto manual primeiro) |
| F1–F5 | 2026-10-14 | Agendamento (detecção + prazos), série diária de 15 pontos, gatilho de imutabilidade, eventos esperados/qualidade e piloto real ponta a ponta | **927 na suíte**; piloto publicou 10.903 referências + 914 avaliações e 1 caso real; 8 defeitos encontrados e corrigidos | Faixa por série em séries curtas; séries sem observação não geram avaliação |
| F6 | 2026-10-14 | **Automação ligada de verdade**: agendador disparou, worker executou, caso nasceu roteado, ciclo completo até CLOSED, caso encerrado não reabriu, prazos notificados | Execução `SCHEDULED` real (run 10) · ciclo de 11 eventos · `REPROCESSING_IGNORED` · `DUE_SOON`/`OVERDUE` com dedup | Faltavam o roteamento na criação e o caminho da remediação — corrigidos ao vivo |
