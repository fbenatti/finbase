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
| **Visão geral** | KPIs do plano §5.2: novos, abertos, vencidos, encerrados, sem responsável, custo anômalo observado, tempo até ciência e resolução, classificados, famílias reincidentes; distribuição por estado e decisões; notas declarando que falsos negativos não são estimados e que aceitação não é falso positivo |
| **Meus casos / fila FinOps** | lista com filtros por estado, severidade e "somente fila FinOps" |
| **Casos** | mesma lista, ciclo completo de estados |
| **Eventos esperados** | recorrências reconhecidas (permanecem na tabela analítica) |
| **Qualidade** | dados parciais, referências reconstruídas, `reason_codes` |
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

## 8. Limitações declaradas

- O gráfico mostra o **escalar consolidado do caso** (observado/esperado/faixa),
  não a grade diária completa: a série diária é derivada da referência compacta
  e a API de série detalhada ainda não expõe os 15 pontos. O contrato de
  distribuição existe (E3) e a leitura por dia é a próxima extensão.
- Sem biblioteca de gráficos no projeto (não há `recharts`/`echarts`), o desenho
  é SVG próprio; a interatividade é limitada a tooltip/legenda.
- A aba "Eventos esperados" filtra a listagem corrente; uma consulta dedicada à
  tabela analítica de eventos esperados depende do endpoint de avaliações (não
  implementado nesta rodada).
