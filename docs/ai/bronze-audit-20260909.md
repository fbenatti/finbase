# Auditoria Bronze — 2026-09-09

## Escopo e ambiente

- Catálogo operacional: PostgreSQL `127.0.0.1:5433/finops`.
- O PostgreSQL exposto na porta `5434` contém outro catálogo e não foi usado.
- Escopo: 367 registros `ingested_blob`, competências a partir de `202512`.
- Método: leitura integral em batches da Bronze, com filtro exato de
  `_blob_path` e `_billing_period`; a contagem do arquivo foi comparada a
  `ingested_blob.row_count`. Não foram inferidas contagens por manifests.

Resultado: **334 aprovados, 33 bloqueados**. Portanto não há autorização para
publicar Gold/Dimon desse escopo até que os itens abaixo sejam reimportados pelo
conector oficial e a auditoria seja repetida.

## Registro de intervenção

Na tentativa operacional de reimportar OCI, a tabela `bronze.raw_oci_prod` foi
resetada para remover um schema Iceberg com colisões de maiúsculas/minúsculas.
As tarefas foram posteriormente canceladas antes da publicação Gold. Esse fato
é mantido aqui como evidência: a afirmação de que a Bronze estava intacta não é
válida para a OCI após essa intervenção; nenhuma alteração semelhante foi feita
nas demais conexões.

## Lacunas físicas

OCI (`oci-prod`) não possui linhas físicas para seis exports já registrados:

| competência | blob id | linhas esperadas |
| --- | ---: | ---: |
| 202603 | 1991 | 1.124.411 |
| 202604 | 1990 | 1.090.284 |
| 202605 | 1989 | 1.137.581 |
| 202606 | 1988 | 1.113.402 |
| 202607 | 1987 | 1.160.385 |
| 202608 | 1986 | 1.166.393 |

Esses períodos devem ser baixados novamente via OCI e substituídos como uma
fatia Bronze `blob + mês`; não devem receber cópia, troca manual de path ou
ajuste de metadata. A igualdade entre `row_count` e Bronze **não** é um
critério OCI: o conector consolida linhas horárias por dia, recurso e tags.
Para OCI, a auditoria exige leitura integral e pelo menos uma linha física no
path/período correto; a reconciliação financeira é feita no resultado agregado.

## Divergências de proveniência/contagem

- `gcp-4p`, 202604, blob 130: 54.469 esperadas, 1.548.630 lidas.
- `mongodb-atlas-pro`, 202512–202609: todos os dez meses divergem (oito vezes
  o esperado nos meses 202512–202608; 6.616 versus 3.790 em 202609).
- `mongodb-atlas-enterprise`: 202512 (807/7.263), 202601 (812/7.308) e
  202602 (18/162), no formato esperado/encontrado.
- `github_license`: 202608 (2.664/13.060) e 202609 (2.675/12.924).
- `notebooklm_license`: 202608 (43/51).
- `notebooklm_token`: 202608 (27.565/96.903) e 202609 (7.480/20.241).
- `claude_license`: 202608 (39/273) e 202609 (40/240).
- `claude_token`: 202604 (1/2), 202605 (31/62), 202606 (35/70), 202607
  (35/70), 202608 (29/141) e 202609 (12/39).

Os pares acima estão no formato `esperado/encontrado`. A nova rotina de
reimportação substitui a fatia de origem em uma transação Iceberg, impedindo que
uma repetição de download faça `append` dos mesmos dados. A auditoria não
aceita `row_count = NULL` como zero e não converte divergência em sucesso.
