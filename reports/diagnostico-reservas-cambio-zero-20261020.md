# Reservas SAPTDF e Terra Mexico: câmbio zero no histórico local

Ambiente investigado: localhost:5173, API nativa :8001, Trino local :8080,
metadata nativa :5433. Não confundir IDs de execução com os da VM de produção.
Snapshot dos prints: run 443, simulação de 02/09/2026, cutoff 31/08/2026.
Os ajustes não commitados da outra frente em forecast_synthetic.py foram
preservados integralmente.

## Diagnóstico reproduzido

| Componente | Projetado setembro | Realizado setembro |
|---|---:|---:|
| SAPTDF — compra de reserva | 116.519,41 | 117.259,98 |
| SAPTDF — retorno | 0,00 | -328.187,09 |
| Terra Mexico AVS — compra | 188.971,25 | 190.172,18 |
| Terra Mexico AVS — uso reservado | 0,00 | 298.129,53 |
| Terra Mexico AVS — retorno | 0,00 | -298.129,53 |

O uso reservado do SAPTDF também foi projetado em zero e está lançado
diretamente em SAPTDF, sem nível 6. O ranking antigo mostrava somente os filhos
folha, omitindo este uso e exibindo o retorno como aparente economia.

No AVS, compra e uso estão no mesmo filho Reserva AVS. Por isso o realizado
desse filho, 488.301,71, era comparado a apenas 188.971,25 de compra projetada.
O filho Retorno carregava a contrapartida de -298.129,53 separadamente.

## Causa dos zeros

Em agosto o SAPTDF mantém aproximadamente 120 horas de uso reservado por dia.
O México mantém 216 horas/dia. Portanto, valor monetário zero não significa
ausência de consumo nesses exemplos.

- SAPTDF: 223 de 249 registros de agosto tinham quantidade positiva,
  source_exchange_rate <= 0 e realizado zero. Após 09/08, todos os valores
  monetários diários estavam zerados.
- Terra Mexico: 90 de 279 registros apresentavam a mesma condição. Após 21/08,
  todos os valores monetários diários estavam zerados.
- Em setembro, nenhum registro reservado desses projetos apresentava essa
  condição. Os Usages e Returns realizados se anulavam corretamente.
- Os preços de referência dos meters existem no catálogo, com moeda USD e
  unidade 1 Hour. A falha está no fator de câmbio zero.
- O CloneEngine multiplicava preço × quantidade × câmbio, descartava o valor
  zero e não registrava uma pendência de câmbio. Um replay de duas linhas reais
  reproduziu zero clones antes da correção.

O Bronze de agosto contém câmbio USD→BRL válido de 5,13745 com data 01/08/2026.
Também contém observações antigas de 5,17135, com data 01/07/2026. Não se deve
escolher MAX ou média das duas taxas. Nos registros problemáticos examinados,
a data de câmbio do Bronze é 01/08/2026.

## Correção implementada

1. Pré-carregar taxas de Usage do mês selecionado no Bronze imutável, limitadas
   aos blobs selecionados, conexão, conta e par de moedas. A data de câmbio
   precisa pertencer ao próprio mês. Só aceitar fallback com taxa positiva,
   finita e inequívoca nesse escopo.
2. Preservar taxas positivas fornecidas pela origem. Resolver taxas inválidas
   pelo fallback; moedas iguais permitem identidade 1. Se não há taxa segura,
   registrar pendência explícita, em vez de descartar silenciosamente o clone.
3. Usar a taxa resolvida tanto no Usage quanto no Return sintético e conservar
   o vínculo ao registro de origem. O Bronze não é alterado.
4. Incluir os caminhos de fatos com custos diretos em pais no ranking de
   acurácia, além das folhas, eliminando caminhos duplicados. Os totais diretos
   continuam exclusivos dos filhos, identificados como lançamentos diretos.
5. Reconstruir agosto Azure-TI pela rotina normal de publicação atômica e só
   depois gerar uma nova simulação de setembro. Preservar a run 443 original.

## Validação inicial

78 testes passaram: forecast, referências Azure, clone engine e caminhos de
custos diretos. Cobertura nova: câmbio zero, ausência de taxa do mês/conta,
moedas iguais, referências ambíguas, exclusão de data de câmbio de outro mês,
custos no pai com retornos nos filhos e reconciliação sem duplicidade.

No replay das mesmas duas linhas reais, o código corrigido gerou dois clones:
R$ 2.311,96 de Usage e R$ -2.311,96 de Return. O carregador retornou 5,13745
para USD→BRL e 1 para BRL→BRL, conforme as observações do mês.

Commits: 7fb5f1ba, 7e62da88 e 2aa22924. Reconstrução local: tarefa 8942,
precedida de auditoria Bronze 8941. A primeira tentativa 8940 foi interrompida
antes de aplicar a correção de câmbio, para não repetir os zeros antigos.

## Resultado publicado e validado

As três tarefas finais concluíram com sucesso:

- 8942: agosto Azure-TI reconstruído e publicado, 3.470.023 linhas.
- 8943: setembro recalculado, nova run **446**, cutoff 31/08/2026.
- 8944: cubo Gold regra 4 / visão completa atualizado para agosto, 17.233 linhas.

Na Silver publicada, o uso reservado de agosto do SAPTDF passou a
R$ 337.449,40, com retorno de R$ -337.449,40. Para Terra Mexico AVS, o par passou
a R$ 308.855,38 e R$ -308.855,38. A quantidade original de consumo foi
preservada: 3.719,866797 e 6.696 horas, respectivamente. Nenhuma linha desses
grupos continua com quantidade positiva e valor zero por câmbio inválido.

O endpoint efetivamente usado pelo dashboard retornou, na run 446:

| Linha de setembro | Projetado | Realizado | Delta | Delta (%) |
|---|---:|---:|---:|---:|
| SAPTDF — Reserva | 116.519,41 | 117.259,98 | 740,58 | +0,64% |
| SAPTDF — Retorno | -329.049,67 | -328.187,09 | 862,58 | +0,26% |
| Terra Mexico — Reserva AVS | 489.003,22 | 488.301,71 | -701,51 | -0,14% |
| Terra Mexico — Retorno AVS | -300.031,96 | -298.129,53 | 1.902,44 | +0,63% |

Reserva AVS inclui Purchase e Usage reservado, como na classificação existente.
O Return segue no filho próprio. O ranking agora inclui também os lançamentos
diretos do SAPTDF e do México. Somando todas as linhas do escopo uma única vez,
os deltas líquidos são R$ -6.301,06 para SAPTDF e R$ -649,97 para Terra Mexico;
estes incluem os demais serviços desses projetos, além das reservas.

A verificação global de Usage+Return com commitmentdiscounttype=Reservation,
agrupada por origem e subcategoria em setembro da run 446, não encontrou saldo
superior a um centavo em valor absoluto. O snapshot antigo 443 foi preservado.
O seletor de meses passou a escolher a run 446 para setembro. A API e o worker
foram conferidos saudáveis após a publicação.
