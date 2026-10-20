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
