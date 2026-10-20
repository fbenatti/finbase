# Evolução da aderência de tags — Azure

Snapshot reconstruído em 20/10/2026 a partir dos inventários Resource Graph preservados na Bronze. Chaves: `projeto`, `diretoria` e `centro-de-custos`.

Aderência = itens tagueáveis com as três chaves preenchidas / total de itens tagueáveis. O escopo histórico das policies foi reconstituído pelas assignments `of-asg*` visíveis no Azure Resource Graph em 20/10: **25 assignments em 16 subscriptions**. É uma coorte fixa para comparação; algumas dessas assignments foram criadas depois da coleta de 15/10.

| Inventário (UTC) | Portfólio total | Coorte com policy | Development |
|---|---:|---:|---:|
| 2026-10-15 | 12.6% (3,279/25,941) | 19.0% (2,057/10,827) | 8.9% (439/4,931) |
| 2026-10-16 | 28.5% (7,394/25,949) | 57.1% (6,172/10,816) | 9.7% (477/4,939) |
| 2026-10-17 | 29.2% (7,478/25,568) | 58.8% (6,239/10,608) | 11.8% (567/4,805) |
| 2026-10-19 | 40.7% (10,548/25,915) | 86.8% (9,339/10,759) | 73.1% (3,563/4,875) |
| 2026-10-20 | 41.3% (10,712/25,953) | 87.4% (9,487/10,849) | 75.2% (3,715/4,939) |

## Leitura da evolução

- O portfólio total registrado saiu de **12,6% em 15/10** para **41,3% em 20/10** (+28,7 p.p.).
- Na coorte das subscriptions com assignments `of-asg*`, subiu de **19,0%** para **87,4%** (+68,4 p.p.).
- Development saiu de **8,9%** para **75,2%** (+66,3 p.p.). O snapshot de 20/10 foi coletado às 10:18 no horário de São Paulo, após a rodada de remediação daquele dia.
- Os percentuais que você lembra parecem misturar escopos: o valor próximo a 8% corresponde a Development em 15/10; 29% corresponde ao portfólio nos snapshots de 16–17/10. O último snapshot global disponível aqui é 41,3%, não 45%; Development está em 75,2%.

## Subscriptions no escopo das assignments

| Subscription | Primeiro assignment identificado (UTC) |
|---|---|
| Terra-Parceiros-Dev (`0641c338-26cb-464a-8bf7-b49d171b681e`) | 2026-10-16T00:45:26.1605035Z |
| Sandbox-OSS (`32ddc892-9789-40e6-af83-a04d005cef58`) | 2026-10-16T16:50:23.3452961Z |
| Fundação Telefônica (`63dbc6fc-93d8-4ac1-9745-253f6b502240`) | 2026-10-15T14:15:53.9978178Z |
| Vertical - B2C Telco – Dev/Pre (`657e0c41-6044-48f8-84e6-fe0a659fdf13`) | 2026-10-16T16:41:46.647945Z |
| Sandbox-B2C-Beatrix (`6b3a8f0b-2135-411e-8c28-bd2b75184a3b`) | 2026-10-16T16:44:42.1655532Z |
| Development (`76461b8e-edbd-464c-ad30-b13ba994709c`) | 2026-10-16T19:10:06.4538592Z |
| Terra-Parceiros-Test (`77851502-10d3-40bc-b021-16d3d17aea4e`) | 2026-10-16T00:48:05.5346795Z |
| Terra-Mexico-Dev (`affa4164-f872-477e-b604-49f789ad1bca`) | 2026-10-16T16:43:11.6114897Z |
| Sandbox-Infra (`b3536f22-445a-4cb5-9164-4fc71f5292e1`) | 2026-10-16T16:48:27.9349488Z |
| Vertical - B2C Varejo – Dev/Pre (`b9be1dc6-fc14-46af-a3f4-8dbb8c366f51`) | 2026-10-16T16:42:28.1488924Z |
| Test (`cd4ed2c4-eb6d-48d7-98b7-e6db50218b71`) | 2026-10-16T16:09:43.0124142Z |
| Terra-Mexico-Test (`d9f34e0b-3b91-4749-a1ba-bd1b1cd8407f`) | 2026-10-15T17:24:16.5828313Z |
| Sandbox-Arquitetura (`dd31861a-39f0-4898-b4c2-b2cf7aa10e04`) | 2026-10-16T16:43:57.517793Z |
| Sandbox-OCS-BSS-Pre (`ebd821da-3f85-467b-9851-80cdb639a741`) | 2026-10-16T16:49:32.9140782Z |
| Terra-Brasil-Dev (`f08b1fe3-f4f7-4c0a-bb51-d6a47cf1a81c`) | 2026-10-15T16:59:58.3480618Z |
| Sandbox-Vivo_na_Cloud (`fb95ed88-6679-4984-83cd-2bc23c15229c`) | 2026-10-16T16:51:11.2271554Z |

## Limitações

- Há snapshots de 15, 16, 17, 19 e 20/10; não há inventário preservado de 18/10.
- Assignment `createdOn` comprova quando foi publicada, não quando todos os recursos terminaram a avaliação ou remediação.
- Para manter a série comparável, as três tags ativas hoje foram aplicadas à leitura dos snapshots antigos. Esta é aderência por presença das tags no Resource Graph, não o percentual oficial de compliance do Azure Policy.
