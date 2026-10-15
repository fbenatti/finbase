# Evolução de realizado, forecast e desvio — revisão de 14/10/2026

## Problemas encontrados

1. `derive_daily_points` expandia todos os snapshots sobrepostos. A mesma semana
   aparecia como 28 pontos, repetindo datas e realizado.
2. O horizonte do forecast era limitado ao cutoff do realizado. Isso apagava a
   previsão futura, mesmo quando ela existia na referência.
3. Histórico e comparação ficavam em gráficos separados. Uma mediana histórica
   aparecia como contexto, mas não explicava a diferença para o forecast.
4. O fallback diário para forecast zerado era recalculado com o histórico atual,
   que podia incluir o próprio aumento. Agora usa pesos e normalizador congelados.
5. O caso consolidava várias séries, mas o detalhe consultava apenas a série
   representativa. Seus valores não reconciliavam com o escopo da avaliação.
6. Compras mensais eram distribuídas como consumo diário, inclusive quando o
   snapshot só continha saldo restante zero.

## Contrato da API de série

- Uma entrada por data civil, ordenada, incluindo lacunas explícitas.
- Observado exclusivamente de `realizado_cloud`, limitado a `analysis_data_end`.
- Forecast continua até o horizonte preservado da referência, sem criar realizado
  futuro e sem dividir o valor mensal pelo número de dias visíveis no gráfico.
- Histórico usa a referência válida anterior a cada dia; na cobertura do episódio,
  a referência original tem precedência sobre forecasts posteriores.
- `expected_cloud`, `lower_cloud`, `upper_cloud` são nulos quando a comparação
  não é suportada. Nulo nunca vira expectativa zero.
- `phase`: `HISTORY`, `COMPARISON` ou `FORECAST`.
- `reference`: execução, cutoff, data de geração, tipo e valor mensal de origem.
- `contributor_series_count` e `scope_verified` identificam o escopo da leitura.
- Ausência da referência original é explícita; a API não escolhe silenciosamente
  uma previsão posterior para substituir a expectativa de um caso ativo.

O histórico de criação/atualização do caso preserva `contributor_series`, contendo
apenas IDs e dimensões. Não há valores financeiros nessa nova procedência no PG.
O realizado e as referências continuam sendo consultados no Iceberg. A previsão
agregada exige cobertura de todos os membros para não apresentar soma parcial
como forecast completo.

## Visualização

Um único gráfico Plotly, usando o componente existente de Forecast Accuracy:

- Cinza: realizado anterior à cobertura de uma referência comparável.
- Verde: parte do realizado até o forecast.
- Vermelho: desvio positivo empilhado sobre a barra; soma das partes = realizado.
- Azul tracejado: forecast; faixa e limite tolerado em segundo plano.
- Área futura identificada e sem barras de realizado.

Resumo, tooltip e gráfico usam a mesma data e o mesmo conjunto de séries. O resumo
é diário (ou acumulado mensal), não o máximo financeiro guardado no workflow.
Selecionar uma data altera o resumo. Há janelas de 14/30 dias anteriores e link
para a execução original em `/forecast/history/:runId`.

Desvio positivo e custo anômalo permanecem distintos: o segundo é apenas o valor
acima do limite tolerado. Nenhum valor do gráfico é somado aos custos financeiros.

## Compras mensais e limitação de referência

As barras mensais mostram acumulado nas datas reais de cobrança, reiniciado a
cada mês. Se a referência contém apenas o saldo restante, ela não pode representar
uma expectativa mensal completa. A interface mostra o saldo e o motivo da
comparação indisponível; não espalha a compra nem cria um desvio contra zero.

Esta correção visual não valida retrospectivamente os candidatos mensais já
abertos pelo detector antigo. Exemplo conhecido: caso 56 contém referência de
saldo zero, enquanto o forecast com corte anterior ao mês tinha uma expectativa
mensal positiva. Sua avaliação financeira requer correção/reavaliação do contrato
mensal no detector; não se deve interpretar a mudança visual como confirmação
humana ou encerramento desse caso.

## Verificação

- 94 testes de séries, referências, publicação e consultas passaram.
- Build TypeScript/Vite passou.
- API real: casos Azure e GCP sem datas duplicadas, realizado até 12/out e forecast
  preservado até 26/out; nenhum observado futuro.
- Compras com saldo restante: comparação indisponível explícita, acumulado apenas
  nas datas de cobrança e sem previsão diária artificial.
- Reprocessamento da configuração 11 concluído: execução 22, forecast 397,
  55.162 avaliações, 41 atualizações e nenhum caso novo.
- Caso 17 após publicação: duas séries contribuintes; realizado de 1.153,6556
  reconcilia com a avaliação, forecast de 725,8735 e limite superior de 802,2421
  reconciliam dentro do arredondamento. Desvio diário de 427,7821 e parcela acima
  da faixa de 351,4135. O impacto do episódio não substitui o valor da data.
- Validação visual em largura de 1.066 px e na largura normal do navegador:
  gráfico único, sem sobreposição com workflow/rodapé, forecast futuro visível.
  A altura do contêiner é explícita para preservar o fluxo após redimensionamento.

## Clareza operacional e organograma — caso 17

O detalhe apresenta primeiro a área comum às séries contribuintes, com o caminho
organizacional. Cada ramo mantém sua conta, centro de custo e todos os níveis
preenchidos do organograma; a conta representativa não é usada como se descrevesse
todo o episódio. O maior desvio positivo indica por onde iniciar a investigação.

`contributor_breakdown` na API de série deriva os valores da mesma consulta Trino
já usada pelo gráfico, sem copiar dinheiro para PostgreSQL nem adicionar consulta
por contribuinte. A comparação usa a data da avaliação do caso e a mesma referência
individual que compõe o total. Ausência de coleta permanece nula. A API conserva
os valores sem arredondamento; o formato monetário da interface arredonda por linha.

Conferência real em 12/out/2026:

- Production / kcli - estruturante excedente / CC 29TN161700: previsto 412,59,
  realizado 653,74, desvio +241,15.
- Development / SEM PROJETO DEFINIDO - AZURE / CC ausente: previsto 313,28,
  realizado 499,92, desvio +186,64.

A informação principal é a comparação textual e o gráfico único. Cartões
repetidos foram removidos. Cálculo, critérios técnicos, etapas, ações corretivas
e auditoria ficam em seções expansíveis. O botão “Tratar caso” leva ao formulário
existente, com nomes de ações em português; clicar no atalho não altera o workflow.
A classificação organizacional não é usada para afirmar causalidade automática.

Validação: 95 testes de séries, referências, publicação e consultas aprovados;
build TypeScript/Vite aprovado; leitura real do caso 17, teste do atalho e
verificação visual nas larguras normal e 1.066 px, sem sobreposição.

## Investigação por decomposição hierárquica

A evolução anterior ainda exigia que o usuário interpretasse listas independentes.
O detalhe agora liga a contribuição financeira ao caminho de investigação, seguindo
o padrão interativo de `GoldDashboard/HierarchicalDecompTree.tsx`:

1. Resumo do evento: diferença para o forecast, previsto/observado e data avaliada.
2. Contexto temporal: primeiro excesso acima da faixa e quantidade de dias na janela
   até a avaliação; último observado posterior é identificado separadamente.
3. Árvore: desvio líquido do nó → projetos/contas contribuintes ordenados por impacto.
4. Selecionar um ramo filtra o resumo e o mesmo gráfico de evolução. Breadcrumbs
   permitem voltar ao total. Ancestrais compartilhados são preservados no caminho;
   níveis sem divisão são pulados, e a folha mostra conta, SKU/categoria e centro de custo.
5. Acesso explícito à investigação, sem executar transição automática.

As barras de participação representam os aumentos positivos do nível. Reduções
aparecem com sinal negativo e reduzem o desvio líquido; não recebem uma participação
positiva artificial. Valores indisponíveis não viram zero. Para compor um gráfico
filtrado, todos os membros precisam ter a referência/observado da data; faltas
permanecem lacunas. Os dados de cada membro usam o forecast congelado e o cutoff
já aplicados na API, sem nova consulta Trino por clique e sem persistência financeira
no PostgreSQL. A API retorna `items` dentro de `contributor_breakdown` para esse fim.

O indicador do último dia só afirma se ultrapassa o limite superior de alerta;
não confirma recuperação, não valida causa e não altera o workflow. A interface
não atribui o desvio a mudança de preço/quantidade/recurso sem evidência disponível.

Referências consultadas (a organização da tela é uma adaptação do OpenFinops):

- [FinOps Foundation — Managing Cloud Cost Anomalies](https://www.finops.org/wg/managing-cloud-cost-anomalies/): comparar expectativa e custo, identificar contexto e investigar antes de decidir.
- [AWS — Getting started with Cost Anomaly Detection](https://docs.aws.amazon.com/cost-management/latest/userguide/getting-started-ad.html): contribuintes ordenados por impacto e evolução filtrada pelo contribuinte selecionado.
- [Microsoft — Identify anomalies and unexpected changes in cost](https://learn.microsoft.com/en-us/azure/cost-management-billing/understand/analyze-unexpected-charges): investigar detalhes e mudanças no contexto afetado.

Validação: 95 testes backend e quatro testes Vitest do modelo de decomposição
(reconciliação com reduções, seleção de projeto, encadeamento por conta e referências
parciais); build frontend aprovado. No navegador, o caso 17 abre em 427,78 de desvio;
selecionar SEM PROJETO DEFINIDO filtra para 186,64, previsto 313,28 e observado 499,92,
inclusive no gráfico. Retornar ao total restaura a comparação agregada. Layout
validado nas larguras normal e 1.066 px.
