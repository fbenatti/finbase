# Série dos casos: consulta e referência histórica — 20/10/2026

## Consulta do caso #92

O histórico do Trino mostrou duas leituras na abertura do caso:

- Diário: 8.586.176 posições, 204.233.391 bytes lidos, 13,73 s decorridos.
- Detalhamento: 1.760.768 posições, 98.824.522 bytes, 9,27 s decorridos.

As consultas já podavam conexão e competência. Entretanto, os filtros de
escopo normalizavam várias strings para cada linha, e o intervalo diário era
recortado somente em Python. O endpoint também consultava schema pelo Trino.

Alterações gerais do leitor v2:

1. Schema e snapshot são lidos diretamente nos metadados do catálogo Iceberg.
2. Uma consulta estreita, de uma coluna, resolve as grafias reais do resource
   group; sem esse campo, usa assinatura ou serviço. A resolução mantém a
   equivalência original de caixa e espaços, sem adivinhar valores.
3. O filtro exato `IN` fica visível ao leitor Parquet. Um `CASE` aplica as
   normalizações restantes apenas nas linhas que passaram pelo escopo seletivo.
4. Filtros de datas usam no máximo três intervalos conservadores na coluna
   original. A desambiguação MM/DD × DD/MM continua após agregação.
5. Todas as consultas da requisição usam o mesmo `FOR VERSION AS OF`.
6. Uma tentativa e timeout de 20 s por consulta; sem repetição automática.

A primeira versão que expandia um intervalo por dia foi descartada: ela
aumentou o custo de avaliação. A versão final usa três intervalos.

Medição da versão final, chamando diretamente o leitor sem cache para #92:
resolução do escopo 1,17 s; detalhe 3,05 s; diário 6,80 s; montagem completa
8,07 s. Foram retornados 50 pontos de calendário e quatro detalhes. Essa
medição é do serviço, não do tempo HTTP total observado no navegador.

As partições físicas ainda são competência/conexão; a otimização não cria
índice nem reescreve o warehouse. Não se trata de uma promessa de tempo constante
sob carga concorrente.

## Pico artificial do caso #97

O problema foi reproduzido com os dados reais já lidos da origem:

- 17/10: realizado 228,2610; referência recalculada 184.578,1935.
- O fator de sábado era 611,4983, aprendido de uma única observação de sábado.
- O gráfico treinava novamente a referência durante o desvio, diferindo do
  detector, que congela a referência no início do episódio.

Correção compartilhada, modelo v2.2:

- Um fator semanal exige quatro observações anteriores daquele dia da semana.
  Sem evidência suficiente, o fator é neutro. Padrões semanais repetidos seguem
  suportados; não foi introduzido corte artificial no eixo do gráfico.
- Novos episódios registram nível, fatores semanais, faixa e corte do modelo
  congelado nos metadados do caso. O gráfico usa esse mesmo modelo.
- Casos anteriores, sem coeficientes preservados, mantêm os escalares de
  referência gravados no caso; não inventam sazonalidade nem absorvem o desvio.
- A linha v2 é apresentada como referência histórica, distinguindo-a de uma
  execução do motor de forecast.

Recalculando somente a apresentação do #97 com os valores gravados no caso:
13/10 mantém realizado 1.884,7339 e esperado 0,7227; 17/10 mantém realizado
228,2610 e esperado 0,7227, com limite superior 3,3935. Todos os valores
observados permaneceram idênticos. O aumento real de consumo continua visível.

## Validação

30 testes direcionados passaram, incluindo fator semanal com amostra única,
padrão semanal repetido, referência congelada, casos anteriores, SQL compacto,
snapshot comum e controles existentes do motor. TypeScript passou.

As leituras reais limitaram-se às séries dos exemplos, com timeout e sem
repetição. A validação do cálculo posterior reutilizou os dados já obtidos, sem
nova consulta financeira. O worker foi reiniciado ocioso para carregar v2.2;
nenhuma execução de detecção foi iniciada por esta validação.
