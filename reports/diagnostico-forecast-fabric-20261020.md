# Diagnóstico do forecast negativo do FABRIC

Data: 20/10/2026. Escopo: investigação antes de corrigir o motor ou publicar dados.
Consultas somente de leitura na produção, via container da API e Trino.

## Conclusão

O projetado negativo de setembro não representa uma previsão coerente do custo
líquido do FABRIC. O snapshot contém um Return sintético de compensação de
reservas sem a projeção positiva correspondente. Os valores negativos das linhas
Return são válidos no realizado; a inconsistência está em projetá-los
independentemente do Usage que compensam.

## Reconciliação da tela

Caminho: Consumo Cloud > Showback > Azure > DIR INFRAESTRUTURA E CLOUD > FABRIC.
Execução: 437, tabela iceberg.forecast.result_run_437, config_version 26.
Simulação: 02/09/2026; cutoff 31/08/2026; geração em 05/10/2026.
Esta é a execução mais recente encontrada para a simulação de setembro.

| Componente de setembro | Categoria | Método registrado | Projetado (R$) |
|---|---|---|---:|
| Fabric Capacity Reservation | Purchase | MONTHLY_LUMP | 457.944,82 |
| OneLake | Usage | MEAN_7D | 2.710,69 |
| Fabric Capacity | Usage | MEAN_7D | 0,00 |
| Fabric Capacity | Return | MEAN_7D | -536.919,88 |
| Total | | | **-76.264,37** |

Realizado de setembro, consultado na Silver atual:

| Componente | Realizado (R$) |
|---|---:|
| Reserva | 457.835,51 |
| OneLake | 2.896,81 |
| Fabric Capacity Usage | 1.354.243,55 |
| Fabric Capacity Return | -1.354.243,55 |
| Total | **460.732,32** |

Desvio calculado sobre valores completos: R$ 536.996,69, exibido arredondado como
536.997. O percentual -704,1% decorre da divisão desse desvio pelo projetado
negativo. A tela está reproduzindo o snapshot e a fórmula implementada.

## Evidência da origem

A regra clone_rule 1, "Azure Reservation Usage → Return", ativa e versão 1,
seleciona Usage com CommitmentDiscountType=Reservation e ConsumedQuantity>0.
O clone recebe ChargeCategory=Return e o valor oposto do Usage reprecificado.
No código, isso ocorre em apps/api/app/services/clone_engine.py, linhas 260–277.

Na Silver atual, os Returns do FABRIC têm is_synthetic=true e clone_rule_id=1.
Em julho e setembro, Usage reservado e Return se anulam integralmente.
Em agosto, ambos também totalizam valores opostos de R$ 543.200,55.
Há ainda R$ 523,19 de Usage OnDemand em agosto, separado da compensação.

Em agosto os Returns existem de 01/08 a 12/08. O Usage reservado continua
registrado até 31/08, com valor zero depois de 12/08. A ausência dos Returns de
valor zero é compatível com o clone engine, que só gera linhas de valor positivo
antes de inverter o sinal. Portanto, ausência de Return não significa que a
coleta inteira terminou no dia 12.

O motor, em apps/api/app/services/forecast_service.py:1599, limita cada série à
menor data entre cutoff da origem e última ocorrência da própria série.
O calendário preenchido com zeros termina nessa data. Assim, o Return para no
dia 12 e conserva uma média negativa antiga; o Usage chega ao dia 31 e sua média
recente é zero. O snapshot confirma history_points=12 para Return e 62 para
Usage. O modelo então publica os dois lados independentemente.

A proteção ENDED_SERIES, na linha 1737, só alcança séries terminadas antes do
primeiro dia do mês do cutoff; não cobre o término do Return no próprio agosto.
A função _replicated_month_value, na linha 309, também retorna None para âncora
mensal não positiva: uma regra mensal de Return não garante, por si só, que uma
projeção negativa matemática seja substituída. Isso é um ponto de revisão,
não uma justificativa para tornar todo Return recorrente.

## Alcance e limites

- No agrupamento por provedor e projeto do snapshot 437, FABRIC foi o único
  projeto com total projetado inferior a -R$ 1.
- Uma triagem por serviço/subcategoria encontrou também saldos negativos de
  Usage+Return em Terra Brasil, Aura Brain, DevOps, Antifraude, Open Gateway,
  SEM PROJETO DEFINIDO - AZURE e Processum 3.0. São candidatos a investigação;
  não foi validado o vínculo sintético individual de cada um deles.
- Na execução de planejamento 506, os pares de Fabric Capacity estão
  equilibrados em outubro e novembro. Isso não elimina o defeito do motor:
  o problema depende do calendário e da composição dos dados de cada série.
- O snapshot 437 não registra source_snapshot_id nem business_rules completas.
  O histórico Silver disponível hoje pode ter sido reprocessado desde 05/10.
  A reconciliação do número publicado é exata; um novo backtest com a Silver
  atual não equivale a reproduzir bit a bit a execução histórica original.
- Removendo apenas o Return sem contrapartida do snapshot, a conferência fica
  em R$ 460.655,51, diferença de R$ 76,81 para o realizado atual. Esse valor é
  um contrafactual aritmético, não o resultado de um novo processamento.

## Estratégia de correção proposta

1. Reproduzir em teste integrado o caso Usage reservado presente até 31/08,
   Return sintético somente até 12/08 e Purchase mensal separado. Verificar
   tanto o valor futuro quanto a ausência de forecast residual em mês fechado.
2. Resolver a cobertura temporal dos Returns sintéticos a partir da origem e
   do Usage vinculado. Preencher ausência de compensação com zero somente
   quando houver evidência de cobertura; preservar a data real de observação
   separadamente para validação de frescor. Evitar aplicar indiscriminadamente
   o cutoff global a faturas mensais ou origens atrasadas.
3. Preservar o vínculo econômico durante a modelagem: para pares integralmente
   sintéticos de reserva, projetar um componente pareado ou derivar a compensação
   da projeção do Usage elegível. Usar rastreabilidade do clone e dimensões de
   reserva; não inferir que toda linha negativa é sintética.
4. Preservar Purchase, Usage OnDemand, créditos reais, Refunds e regras manuais.
   Não aplicar piso zero ao total nem zerar todos os Returns: isso esconderia
   o defeito e poderia aumentar custos projetados legitimamente compensados.
5. Testar regressões: compensação contínua; término no meio do mês; mês fechado;
   atraso da origem inteira; reserva combinada com OnDemand; faturas mensais;
   previsões de compromissos; simulação sem ler datas posteriores ao cutoff.
6. Comparar execução anterior e corrigida em memória/ambiente isolado usando o
   mesmo histórico. Revisar os demais candidatos da triagem antes de expandir
   o alcance da mudança. Na métrica percentual, avaliar denominador absoluto
   ou apresentação explícita para base negativa; essa decisão é separada da
   correção financeira e deve ser aplicada consistentemente às visões.
7. Publicar código validado e gerar uma nova execução de acurácia de setembro.
   Preservar a run 437 para auditoria e identificar a nova run como backtest
   recalculado. Conferir reconciliação do dashboard, cache e demais meses.
   Recalcular planejamento corrente apenas se a validação mostrar necessidade.

Critério de aceite: a previsão não contém compensação sintética desvinculada;
o par elegível preserva sua identidade econômica; meses fechados não recebem
forecast residual por causa da última ocorrência de uma dimensão; o dashboard
reconcilia com a nova execução, mantendo a anterior consultável.

## Estado ao encerrar a investigação

Nenhum código de aplicação, regra, snapshot, dado financeiro ou configuração
de produção foi alterado. Este documento registra o diagnóstico e a proposta
para a etapa de implementação posterior ao mapeamento solicitado.
