# SOURCE VALIDATION — v2.5.3

Fonte validada: `finops_CSV.csv`

- Linhas de dados: **684,516**
- Competências com Realizado: **202601, 202602, 202603, 202604, 202605, 202606**
- Comparação recente usada em Crescimento/Redução: **202605 → 202606**
- Forecast exclusivo de meses futuros não entra nessa série.

## Maior redução — Família de Serviço
| # | Família | Variação recente | 202605 | 202606 |
|---:|---|---:|---:|---:|
| 1 | Management and Governance | -7.32% | 530,711.30 | 491,845.32 |
| 2 | Azure Marketplace Services | -3.18% | 5,051.83 | 4,891.27 |
| 3 | Security | -2.90% | 15,587.83 | 15,136.15 |
| 4 | Web | -2.22% | 2,124.98 | 2,077.79 |
| 5 | Databases | -0.58% | 833,438.38 | 828,581.10 |
| 6 | Não informado | 1.04% | 48,297.49 | 48,799.08 |
| 7 | Integration | 1.29% | 72,152.63 | 73,086.02 |
| 8 | Data | 1.39% | 444,562.16 | 450,757.73 |

## Maior crescimento — Família de Serviço
| # | Família | Variação recente | 202605 | 202606 |
|---:|---|---:|---:|---:|
| 1 | Developer Tools | 27.69% | 582,372.33 | 743,644.66 |
| 2 | AI + Machine Learning | 9.35% | 462,256.23 | 505,471.34 |
| 3 | SaaS | 7.30% | 512,555.80 | 549,997.53 |
| 4 | Azure Communication Services | 7.29% | 189.67 | 203.50 |
| 5 | Internet of Things | 7.09% | 309,481.87 | 331,421.34 |
| 6 | Analytics | 7.01% | 714,373.56 | 764,442.05 |
| 7 | Storage | 5.63% | 1,819,004.59 | 1,921,428.09 |
| 8 | Compute | 4.74% | 2,793,552.58 | 2,926,000.64 |

## Regra funcional validada
- Maior custo: total de `Realizado` somente.
- Maior crescimento: % entre as duas últimas competências com `Tipo=realizado`.
- Maior redução: menor % entre as duas últimas competências com `Tipo=realizado`.
- Volatilidade: desvio dos deltas mensais somente na série de `Realizado`.
- Sparkline: apenas competências com `Tipo=realizado`.
- Gap: tendência mensal somente quando Forecast e Realizado coexistem na mesma competência.
