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

## Revisão de RIs e implementação autorizada

Após a solicitação de aplicar a correção, a revisão de outubro identificou um
segundo caso: o projeto **Compra RIs - Retorno** concentra Returns sintéticos
cujos Usages estão classificados em projetos consumidores diferentes. O vínculo
por clone_source_record_id, source_connection_id e billing period confirmou,
entre outros, TRR Plataforma Kubernetes, SIG, Vivo Ads, Aura Canais e Terra Brasil.
Isso é diferente do FABRIC, onde os dois lados estão no mesmo projeto.

Portanto, um valor negativo em Compra RIs - Retorno pode estar correto. Comprar
uma RI e reconhecer o uso coberto por ela também são componentes distintos:
Purchase continua sendo custo; Usage reprecificado e Return sintético formam
a compensação. Não há motivo para forçar o delta mensal de Purchase a zero.

Apenas corrigir o calendário não bastava para o retorno centralizado: uma média
escolhida para o total não é necessariamente a soma das médias escolhidas pelo
AUTO_BACKTEST para cada projeto. Na execução intermediária 511, a família
Virtual Machines Dv3 Series tinha R$ 13.156,16 de Usage projetado contra
R$ -14.194,50 de Return: diferença indevida de R$ -1.038,34. No realizado de
setembro, os pares de reserva se anulavam, inclusive nesta família.

### Alterações efetivas

- As séries usam o início e o fim da janela observada da mesma origem, com
  zeros nos dias sem ocorrência da dimensão. A data da última ocorrência
  continua separada para regras de validade e término.
- A referência de compra mensal, quando o corte é o último dia do mês, passa
  a ser esse mês recém-fechado. O corte de setembro usa agosto, não julho.
- O modelo de lançamentos mensais esparsos trata sinais positivos e negativos
  de forma simétrica.
- As projeções sintéticas de Return são derivadas dos forecasts de seus
  registros Usage de origem. Os vínculos são agrupados pelas dimensões do uso
  e do retorno, respeitando os projetos diferentes e as participações históricas
  de compensação, inclusive quando apenas parte de um uso tem clone.
- A origem e o destino do vínculo usam conexão e billing period além do ID
  do registro. A consulta é limitada ao histórico disponível no cutoff; não usa
  realizado do mês-alvo. Séries sintéticas e créditos reais são separadas por
  is_synthetic, que também é preservado no snapshot de auditoria.
- Regras e ajustes configurados continuam sendo aplicados após a derivação.
  Os valores financeiros continuam podendo ser negativos.
- Percentual de desvio usa delta / abs(projetado); WAPE usa
  abs(delta) / abs(realizado); bias também usa a magnitude do realizado. Isso
  mantém o sentido do delta e evita erro negativo/acurácia acima de 100% só
  porque a base financeira é um crédito. Bases zero continuam indefinidas.
  A comparação dinâmica de histórico segue a mesma convenção percentual.

### Validação

59 testes de forecast passaram. A cobertura adicionada verifica FABRIC,
reservas contínuas, lançamentos esparsos, início de reserva no fim do mês,
origens atrasadas, mês fechado, compras mensais, futuro excluído do cutoff,
retorno centralizado, compensação parcial, crédito real e métricas negativas.

No ensaio completo de outubro, com leitura dos dados de produção e publicação
desabilitada, 948 séries mensais de offsets receberam o método
LINKED_SOURCE_USAGE. A soma dos resíduos absolutos de Usage+Return reservado,
por origem e família de serviço, foi R$ 0,00000000061: zero na precisão monetária.
O projeto Compra RIs - Retorno continuou negativo, conforme sua classificação,
com R$ -14.437,92 projetados. O ensaio confirmou que preservar a compensação
não exige mover os lançamentos entre projetos.

Na execução intermediária corrigida 510, o endpoint usado pelo dashboard
reconciliou setembro do FABRIC em R$ 458.178,27 projetados e R$ 460.732,32
realizados, delta de R$ 2.554,04 (+0,5574%) e acurácia de 99,4457%.
O valor é diferente do contrafactual inicial porque o recálculo usa agosto
como referência da compra e a configuração/histórico atuais. Não é uma edição
da previsão publicada originalmente no snapshot 437.

### Publicação e auditoria

Código publicado na VM em commits 4b7949fb, c28022a3, 4051b764 e 57e2398d,
preservando alterações de produção que não pertencem a esta correção.
Os commits locais correspondentes estão registrados a partir de f7d044ba,
incluindo 1d9b032c, 4ab7c44a, 027b79f5 e 1f79a3f4.
API, scheduler e worker foram atualizados; os serviços foram reiniciados com
a fila ociosa. Alterações locais de outras frentes não foram incluídas.

Os snapshots originais são preservados. A tarefa final de reprocessamento é
9123, para setembro e outubro de 2026, com a versão completa que vincula Returns
aos usos. Os meses anteriores não são reescritos automaticamente. Estas são
novas simulações com cutoff histórico, não uma alegação de que a previsão
original foi produzida com este algoritmo ou com o histórico reprocessado atual.
