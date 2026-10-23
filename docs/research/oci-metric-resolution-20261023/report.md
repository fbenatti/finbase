# Piloto OCI de resolução e percentis — 23/10/2026

Experimento executado antes do plano de implementação. Duas VMs com CPU publicada, um compartment, região `sa-saopaulo-1`. Não houve escrita em PostgreSQL, Bronze/Silver/Gold de produção nem alteração na OCI.

Janela: `[2026-10-22T11:30:00+00:00, 2026-10-23T11:30:00+00:00)`. Datas/partições em UTC; 08:30 de 22/10 até 08:30 de 23/10 em America/Sao_Paulo.

## Escopo e método

Foram consultadas oito métricas (`CpuUtilization`, `MemoryUtilization`, `DiskIopsRead`, `DiskIopsWritten`, `DiskBytesRead`, `DiskBytesWritten`, `NetworksBytesIn`, `NetworksBytesOut`), oito estatísticas (mean/min/max/count/sum/P90/P95/P99) e três resoluções (1/5/15m). Cada requisição explicitou intervalo e resolução iguais, resourceId e a mesma janela. Contagens abaixo excluem pontos retornados em endTime; não houve interpolação nem preenchimento com zero.

A comparação fez 384 chamadas sem erros e preservou 233.472 valores. O script completo fez 388 chamadas, incluindo quatro sondagens antigas (duas rejeições de parâmetro). Mais 15 sondagens curtas complementaram o histórico; o total desta investigação é 403 chamadas SummarizeMetricsData, além da descoberta e da validação de acesso anteriores.

A amostra é intencionalmente pequena e restrita a VMs em execução com telemetria. Não representa sazonalidade, diferentes clouds, frota completa ou recursos sem agente. Há somente uma conexão OCI no banco local; outras nuvens não foram consultadas ao vivo.

## Volume e latência medidos

| Resolução | Chamadas | Pontos retornados | Pontos retidos | JSON SDK (bytes) | Gzip separado (bytes) | Parquet ZSTD (bytes) | Soma API (s) | P95 latência API (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1m | 128 | 184448 | 184320 | 11863882 | 1475791 | 761826 | 15.036 | 189.5 |
| 5m | 128 | 36992 | 36864 | 2464380 | 379807 | 208384 | 11.603 | 150.1 |
| 15m | 128 | 12416 | 12288 | 895258 | 185547 | 84561 | 10.395 | 156.2 |

JSON é serialização local do SDK; gzip é a soma de compressões locais por resposta, sem cabeçalhos de rede. Parquet local contém uma linha por estatística e não é a futura Silver larga. Esses números não medem bytes TCP nem custo de Iceberg. Foram 128 chamadas em cada resolução porque mudamos o tamanho do bucket sem mudar o número de recursos/métricas/estatísticas/chunks.

Não há valor monetário efetivo medido. A cobrança deve ser estimada por unidade do endpoint, preço vigente, franquia compartilhada e eventuais custos de rede/armazenamento. Pontos retornados são um proxy de volume; não provam o número faturado de datapoints analisados. Não declarar custo zero a partir do tamanho deste piloto.

## P90/P95/P99 calculados sobre a série de médias

Os percentis abaixo são locais, calculados com quantil linear (`numpy.percentile(method="linear")`). Não são os percentis nativos dentro dos buckets. Os oito tipos de estatística e payloads correspondentes foram preservados no diretório privado.

| Recurso | Métrica | Resolução | Buckets | Cobertura | P90 | P95 | P99 | Máxima média | Máximo nativo global |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| vm-1413c3cbc3 | CpuUtilization | 1m | 1440 | 100.0% | 28.311 | 30.885 | 45.202 | 94.666 | 97.307 |
| vm-1413c3cbc3 | DiskBytesRead | 1m | 1440 | 100.0% | 4.057e+10 | 4.057e+10 | 4.057e+10 | 4.057e+10 | 4.057e+10 |
| vm-1413c3cbc3 | DiskBytesWritten | 1m | 1440 | 100.0% | 1.2263e+12 | 1.228e+12 | 1.2297e+12 | 1.2301e+12 | 1.2301e+12 |
| vm-1413c3cbc3 | DiskIopsRead | 1m | 1440 | 100.0% | 6.0083e+05 | 6.0085e+05 | 6.0085e+05 | 6.0085e+05 | 6.0085e+05 |
| vm-1413c3cbc3 | DiskIopsWritten | 1m | 1440 | 100.0% | 1.0325e+08 | 1.0339e+08 | 1.0353e+08 | 1.0357e+08 | 1.0358e+08 |
| vm-1413c3cbc3 | MemoryUtilization | 1m | 1440 | 100.0% | 37.333 | 38.04 | 43.329 | 58.62 | 61.716 |
| vm-1413c3cbc3 | NetworksBytesIn | 1m | 1440 | 100.0% | 2.1336e+13 | 2.136e+13 | 2.1384e+13 | 2.1391e+13 | 2.1391e+13 |
| vm-1413c3cbc3 | NetworksBytesOut | 1m | 1440 | 100.0% | 9.2431e+12 | 9.2486e+12 | 9.2583e+12 | 9.262e+12 | 9.2621e+12 |
| vm-195c58418b | CpuUtilization | 1m | 1440 | 100.0% | 2.2786 | 2.3701 | 2.5495 | 4.0366 | 15.939 |
| vm-195c58418b | DiskBytesRead | 1m | 1440 | 100.0% | 7.8203e+09 | 7.8406e+09 | 7.8406e+09 | 7.8406e+09 | 7.8406e+09 |
| vm-195c58418b | DiskBytesWritten | 1m | 1440 | 100.0% | 1.5146e+11 | 1.5166e+11 | 1.5186e+11 | 1.519e+11 | 1.519e+11 |
| vm-195c58418b | DiskIopsRead | 1m | 1440 | 100.0% | 1.9946e+05 | 2.0028e+05 | 2.0028e+05 | 2.0028e+05 | 2.0028e+05 |
| vm-195c58418b | DiskIopsWritten | 1m | 1440 | 100.0% | 1.926e+07 | 1.929e+07 | 1.9315e+07 | 1.932e+07 | 1.932e+07 |
| vm-195c58418b | MemoryUtilization | 1m | 1440 | 100.0% | 31.714 | 31.843 | 32.185 | 32.552 | 36.086 |
| vm-195c58418b | NetworksBytesIn | 1m | 1440 | 100.0% | 1.4994e+12 | 1.4999e+12 | 1.5011e+12 | 1.5016e+12 | 1.5016e+12 |
| vm-195c58418b | NetworksBytesOut | 1m | 1440 | 100.0% | 1.6317e+12 | 1.6323e+12 | 1.6335e+12 | 1.6341e+12 | 1.6341e+12 |
| vm-1413c3cbc3 | CpuUtilization | 5m | 288 | 100.0% | 28.253 | 29.995 | 38.318 | 75.884 | 97.307 |
| vm-1413c3cbc3 | DiskBytesRead | 5m | 288 | 100.0% | 4.057e+10 | 4.057e+10 | 4.057e+10 | 4.057e+10 | 4.057e+10 |
| vm-1413c3cbc3 | DiskBytesWritten | 5m | 288 | 100.0% | 1.2262e+12 | 1.2279e+12 | 1.2295e+12 | 1.2299e+12 | 1.23e+12 |
| vm-1413c3cbc3 | DiskIopsRead | 5m | 288 | 100.0% | 6.0083e+05 | 6.0084e+05 | 6.0085e+05 | 6.0085e+05 | 6.0085e+05 |
| vm-1413c3cbc3 | DiskIopsWritten | 5m | 288 | 100.0% | 1.0324e+08 | 1.0338e+08 | 1.0352e+08 | 1.0356e+08 | 1.0357e+08 |
| vm-1413c3cbc3 | MemoryUtilization | 5m | 288 | 100.0% | 37.368 | 38.38 | 41.167 | 50.949 | 61.716 |
| vm-1413c3cbc3 | NetworksBytesIn | 5m | 288 | 100.0% | 2.1332e+13 | 2.1358e+13 | 2.1381e+13 | 2.1388e+13 | 2.1389e+13 |
| vm-1413c3cbc3 | NetworksBytesOut | 5m | 288 | 100.0% | 9.2417e+12 | 9.248e+12 | 9.2567e+12 | 9.2605e+12 | 9.2611e+12 |
| vm-195c58418b | CpuUtilization | 5m | 288 | 100.0% | 2.2545 | 2.3389 | 2.4108 | 2.4734 | 15.939 |
| vm-195c58418b | DiskBytesRead | 5m | 288 | 100.0% | 7.8203e+09 | 7.8406e+09 | 7.8406e+09 | 7.8406e+09 | 7.8406e+09 |
| vm-195c58418b | DiskBytesWritten | 5m | 288 | 100.0% | 1.5144e+11 | 1.5164e+11 | 1.5184e+11 | 1.5188e+11 | 1.5189e+11 |
| vm-195c58418b | DiskIopsRead | 5m | 288 | 100.0% | 1.9946e+05 | 2.0028e+05 | 2.0028e+05 | 2.0028e+05 | 2.0028e+05 |
| vm-195c58418b | DiskIopsWritten | 5m | 288 | 100.0% | 1.9258e+07 | 1.9288e+07 | 1.9312e+07 | 1.9318e+07 | 1.9319e+07 |
| vm-195c58418b | MemoryUtilization | 5m | 288 | 100.0% | 31.663 | 31.833 | 31.992 | 32.331 | 36.086 |
| vm-195c58418b | NetworksBytesIn | 5m | 288 | 100.0% | 1.4993e+12 | 1.4999e+12 | 1.5009e+12 | 1.5014e+12 | 1.5014e+12 |
| vm-195c58418b | NetworksBytesOut | 5m | 288 | 100.0% | 1.6315e+12 | 1.6322e+12 | 1.6333e+12 | 1.6338e+12 | 1.6339e+12 |
| vm-1413c3cbc3 | CpuUtilization | 15m | 96 | 100.0% | 28.005 | 30.204 | 35.293 | 51.482 | 97.307 |
| vm-1413c3cbc3 | DiskBytesRead | 15m | 96 | 100.0% | 4.057e+10 | 4.057e+10 | 4.057e+10 | 4.057e+10 | 4.057e+10 |
| vm-1413c3cbc3 | DiskBytesWritten | 15m | 96 | 100.0% | 1.2259e+12 | 1.2275e+12 | 1.229e+12 | 1.2295e+12 | 1.2297e+12 |
| vm-1413c3cbc3 | DiskIopsRead | 15m | 96 | 100.0% | 6.0083e+05 | 6.0084e+05 | 6.0085e+05 | 6.0085e+05 | 6.0085e+05 |
| vm-1413c3cbc3 | DiskIopsWritten | 15m | 96 | 100.0% | 1.0322e+08 | 1.0334e+08 | 1.0348e+08 | 1.0352e+08 | 1.0354e+08 |
| vm-1413c3cbc3 | MemoryUtilization | 15m | 96 | 100.0% | 37.504 | 37.975 | 39.918 | 46.844 | 61.716 |
| vm-1413c3cbc3 | NetworksBytesIn | 15m | 96 | 100.0% | 2.1327e+13 | 2.1353e+13 | 2.1374e+13 | 2.138e+13 | 2.1384e+13 |
| vm-1413c3cbc3 | NetworksBytesOut | 15m | 96 | 100.0% | 9.2409e+12 | 9.2468e+12 | 9.2534e+12 | 9.2566e+12 | 9.2585e+12 |
| vm-195c58418b | CpuUtilization | 15m | 96 | 100.0% | 2.2418 | 2.3042 | 2.3341 | 2.372 | 15.939 |
| vm-195c58418b | DiskBytesRead | 15m | 96 | 100.0% | 7.8203e+09 | 7.8406e+09 | 7.8406e+09 | 7.8406e+09 | 7.8406e+09 |
| vm-195c58418b | DiskBytesWritten | 15m | 96 | 100.0% | 1.514e+11 | 1.516e+11 | 1.5176e+11 | 1.5183e+11 | 1.5186e+11 |
| vm-195c58418b | DiskIopsRead | 15m | 96 | 100.0% | 1.9946e+05 | 2.0028e+05 | 2.0028e+05 | 2.0028e+05 | 2.0028e+05 |
| vm-195c58418b | DiskIopsWritten | 15m | 96 | 100.0% | 1.9252e+07 | 1.9282e+07 | 1.9305e+07 | 1.9312e+07 | 1.9315e+07 |
| vm-195c58418b | MemoryUtilization | 15m | 96 | 100.0% | 31.661 | 31.765 | 31.898 | 31.921 | 36.086 |
| vm-195c58418b | NetworksBytesIn | 15m | 96 | 100.0% | 1.4992e+12 | 1.4998e+12 | 1.5005e+12 | 1.5009e+12 | 1.5011e+12 |
| vm-195c58418b | NetworksBytesOut | 15m | 96 | 100.0% | 1.6314e+12 | 1.632e+12 | 1.6329e+12 | 1.6333e+12 | 1.6336e+12 |

As métricas de CPU/memória estão em percentual. As demais colunas mostram os valores nativos da série, sem conversão ou alegação de equivalência entre contador, delta e rate; a classificação final pertence ao catálogo de métricas. Não comparar valores de unidades diferentes nem usar sum de percentual como consumo acumulado.

## Resultado principal

Na VM `vm-1413c3cbc3`, a maior média de CPU passou de 94,6661% (1m) para 75,8836% (5m) e 51,4824% (15m). O P99 das médias passou de 45,2023% para 38,3181% e 35,2926%: diferenças relativas de -15,23% e -21,92%. O maior máximo nativo foi 97,3073% nas três resoluções. Guardar máximos preserva a amplitude do pico; sua distribuição/duração sub-bucket não é reconstruída apenas com esse campo.

A segunda VM teve CPU mais estável/baixa; a existência de uma série estável não justifica aplicar resolução grossa à VM com picos. A referência provisória é 1m; comparar sete dias antes de decidir política mais ampla. Derivar 5m/15m de uma base fina evita repetir aquisições na operação normal.

## Histórico sondado

Consultas curtas históricas foram feitas sobre a primeira VM; um sucesso não comprova continuidade entre as datas. A tabela preserva parâmetros/resultado de todas as sondagens.

| Idade aproximada | Resolução | Métrica/estatística | Resultado | Pontos dentro da janela |
| --- | --- | --- | --- | ---: |
| 8 dias | 1m | CpuUtilization/mean | OK | 120 |
| 31 dias | 5m | CpuUtilization/mean | ERROR InvalidParameter | — |
| 35 dias | 15m | CpuUtilization/mean | ERROR InvalidParameter | — |
| 59 dias | 60m | CpuUtilization/mean | OK | 2 |
| 14 dias | 1m | CpuUtilization/mean | OK | 60 |
| 29 dias | 1m | CpuUtilization/mean | OK | 60 |
| 31 dias | 1m | CpuUtilization/mean | OK | 60 |
| 29 dias | 5m | CpuUtilization/mean | OK | 12 |
| 29 dias | 15m | CpuUtilization/mean | OK | 4 |
| 31 dias | 15m | CpuUtilization/mean | ERROR InvalidParameter | — |
| 31 dias | 1m | CpuUtilization/mean() | OK | 60 |
| 59 dias | 1m | CpuUtilization/mean() | OK | 60 |
| 59 dias | 1m | CpuUtilization/count() | OK | 60 |
| 59 dias | 5m | CpuUtilization/mean() | ERROR InvalidParameter | — |
| 59 dias | 15m | CpuUtilization/mean() | ERROR InvalidParameter | — |
| 59 dias | 1m | CpuUtilization/percentile(0.90) | OK | 60 |
| 59 dias | 1m | CpuUtilization/percentile(0.95) | OK | 60 |
| 59 dias | 1m | CpuUtilization/percentile(0.99) | OK | 60 |
| 59 dias | 1m | MemoryUtilization/mean() | OK | 60 |

A sondagem CPU de 59 dias atrás em 1m trouxe 60 valores distintos em uma hora e seis observações por bucket no count. P90/P95/P99 nativos e médias de memória nessa mesma data também retornaram 60 pontos cada. Em contraste, 5m/15m antigos foram rejeitados com InvalidParameter e mensagem de intervalo de 300s. Isso é evidência específica deste endpoint/recurso, não contrato universal de retenção. Guardar no capability registry tanto limites documentados quanto comportamento observado. Backfill deve usar chunks limitados e verificar cadência, idade e cobertura reais.

## Arquivos, privacidade e reprodução

- Script versionado: `scripts/pilot_oci_metrics_resolution.py`.
- Medidas completas sanitizadas: `summary.json`; sondagens: `history_probes.json`.
- Raw e inventário completo local: `apps/data/metric-pilot/oci-resolution-20261023/` (ignorado pelo Git).
- Os aliases do relatório são hashes parciais de recurso; não contêm nome de VM, OCID completo ou segredo.

```bash
python3 scripts/pilot_oci_metrics_resolution.py \
  --config env/oci_config_preview.conf --compartment tivit \
  --instances 2 --hours 24 \
  --output apps/data/metric-pilot/oci-resolution-NOVO-RUN
```

O script repete a comparação e quatro sondagens. Para repetir as quinze sondagens complementares, usar os parâmetros `start`, `end`, `minutes`, `statistic` e `metric` de `history_probes.json`, a mesma dimensão resourceId do inventário privado, e a API SummarizeMetricsData com resolução explícita. Não persistir credenciais nos manifests. As janelas originais ficam cada vez mais antigas; um resultado posterior pode mudar por retenção.

## Fontes oficiais e limites desta análise

- [OCI MQL](https://docs.oracle.com/en-us/iaas/Content/Monitoring/Reference/mql.htm).
- [OCI requisição e resolução](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/monitoring/models/oci.monitoring.models.SummarizeMetricsDataDetails.html).
- [OCI métricas Compute](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computemetrics.htm).
- [OCI preços de Monitoring](https://www.oracle.com/manageability/pricing/).

A pesquisa não escolhe resolução definitiva de toda a frota, não mede custos faturados e não calcula savings nem veredito de risco. Seu resultado fundamenta o plano de implementação e a próxima comparação restrita.
