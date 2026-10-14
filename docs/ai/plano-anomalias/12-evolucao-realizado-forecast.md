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
- Reprocessamento da configuração 11 para gravar a procedência de contribuintes
  nos casos existentes: execução 22 (registrar resultado abaixo).
