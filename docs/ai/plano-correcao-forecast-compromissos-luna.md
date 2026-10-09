# Plano revisado: reverter regressão e controlar validade da fonte — GPT Luna

## Revisão de 09/10/2026 após investigação do histórico Git

**Esta revisão é o plano de execução vigente. Substitui a implementação extensa proposta nas seções 4–8 do diagnóstico original preservado abaixo.** Primeiro restaurar o comportamento das regras existentes e tratar a validade da fonte por regra visível. Não implementar MONTHLY_COMMITMENT, MIRROR_SOURCE_FORECAST ou uma ponte de linhagem para resolver esta regressão. Esses mecanismos seriam uma evolução independente, somente se problemas residuais forem demonstrados depois da restauração.

O usuário levantou a hipótese de regressão após o patch que encerrava forecast sem realizado recente. A hipótese foi confirmada pelo diff, pelos snapshots reais e pela execução isolada das funções antes/depois, sem alterar o motor ou publicar dados.

### R1. Commit responsável e evidências

Commit **`7d63645aa8205c4e869d1229007a3ac73d87956a`**, de **08/10/2026 às 10:13:06 -03:00**, mensagem `Stop recurring forecasts after realized zero month`.

Ele introduziu duas alterações financeiras gerais:

1. `_replicated_month_value`: quando `last_closed_month_total <= 0`, passou de retornar `None` (preserva a previsão matemática existente) para retornar `0.0` nas recorrências futuras. Isso elimina estornos negativos legítimos.
2. `_apply_forecast_business_rules`: passou a buscar o realizado no mês anterior a **cada mês projetado**, usando zero quando o período não existe. Isso encerra a recorrência porque o histórico não contém meses futuros.

O teste `<= 0` já existia antes, mas retornava `None`: a previsão negativa continuava intacta. Portanto, **não trocar simplesmente `<= 0` por `== 0`** como correção principal. Isso faria a regra replicar uma base negativa mensal que anteriormente era preservada pelo modelo diário, alterando a semântica das regras que funcionavam.

| Caso reproduzido em memória, três meses | Código anterior ao patch | Código atual |
|---|---|---|
| Return, base negativa -300 | [-300, -300, -300] | [0, 0, 0] |
| Purchase, base positiva 100 | [100, 100, 100] | [100, 0, 0] |

Comparação dos snapshots simulados de outubro, ambos com `simulated_as_of=2026-10-02` e cutoff 30/09:

| SAPTDF, outubro | Run 381, antes do patch | Run 383, depois do patch |
|---|---:|---:|
| Purchase reserva | R$ 116.519,41 | R$ 116.519,41 |
| Usage reserva | R$ 342.162,88 | R$ 342.162,88 |
| Return reserva | -R$ 342.189,69 | R$ 0,00 |
| Usage OnDemand | R$ 185.755,24 | R$ 185.755,24 |
| Total | R$ 302.247,83 | R$ 644.437,52 |

O aumento de **R$ 342.189,69** corresponde exatamente ao Return que foi zerado. Os dados/snapshots não registram o SHA executado, mas a cronologia e a alteração exata da função confirmam o mecanismo. Não presumir igualdade de todas as fontes entre dias de coleta.

Savings Plan Azure TI, novembro: no run 380 anterior ao patch havia Purchase R$ 49.822,49, Return -R$ 61.265,77 e Usage R$ 61.228,14. No run 382 posterior, Purchase e Return ficam zero, enquanto Usage permanece em R$ 61.468,81. São execuções normais com cutoffs diferentes (06/10 e 07/10), por isso a pequena diferença de Usage não deve ser atribuída ao patch.

Commit `e7b30f6b` de 08/10 às 10:06 criou `_close_missing_months`. Ele não adiciona meses à série: apenas atribui zero a dias que já estão no grid e cujo total mensal já era zero. Sua eficácia é limitada, mas não contém as duas condições responsáveis pela inflação. Não reverter em bloco vários patches sem evidência. `fb4a04fa`, de 01/10, alinha cutoffs por conexão e também deve ser preservado inicialmente.

A projeção Purchase excessiva no mês aberto e a referência mensal antiga têm condições introduzidas antes de 08/10. Devem ficar registradas como problemas residuais, não como justificativa para ampliar a reversão da regressão confirmada.

### R2. Ordem de execução da correção mínima

**Passo 1 — restaurar o patch identificado.** Verificar se os dois arquivos alterados por `7d63645a` continuam sem mudanças posteriores de implementação. Se sim, executar `git revert --no-edit 7d63645a`, que cria o commit de reversão imediatamente. Se houver alterações posteriores/conflitos, restaurar somente as mudanças desse commit, preservando intervenções novas, e fazer commit imediatamente. Não reverter `7f2372c5` nem esta revisão documental.

A reversão deve devolver `return None` para bases não positivas e remover a exigência de realizado do mês anterior a cada mês futuro. Restaurar também os testes alterados pelo patch, verificando que documentem a semântica anterior, sem eliminar cobertura útil de outros casos. Comparar diff do resultado com `git show 7d63645a`; uma reversão integral também restaura a pequena reorganização de contexto, não somente as duas linhas financeiras.

**Passo 2 — validar restauração antes de qualquer evolução.** Reexecutar casos de Return negativo e horizonte positivo de 12 meses. Rodar os testes existentes de forecast/regras/cutoff/IBM/planning pertinentes. Executar simulado isolado de outubro sem publicar planning e comparar com run 381. Purchase e Usage de reserva devem permanecer compatíveis com a base escolhida; Return deve recuperar seu sinal e sua previsão matemática. Não exigir cancelamento perfeito onde já havia diferenças pequenas antes do patch: quantificar e registrar separadamente.

**Passo 3 — tratar o problema original por uma regra explícita de validade.** Seguir R3–R5. A ausência da invoice é uma condição da fonte/escopo e não deve ser inferida pela presença de dados nos meses futuros da projeção.

**Passo 4 — publicar somente depois da validação conjunta.** Gerar snapshot novo, registrar regras/versões e então publicar corrente. Preservar snapshots históricos, respeitar camada da planning e rateio. Regenerar simulados por nova execução, mantendo runs antigos auditáveis.

Após **cada alteração de código**, fazer commit Git imediatamente. Preservar exclusões preexistentes em `backups/`. Não fazer commit indiscriminado do workspace.

### R3. Validade da fonte e PTU: regra visível, sem caso chumbado

Adicionar comportamento genérico **`SOURCE_VALIDITY`**, com rótulo “Interromper previsão sem dados recentes”, à **mesma aba Forecast → Regras**. Pode usar `behavior_options` JSON validado ou colunas explícitas, conforme menor alteração coerente com as migrações existentes. As condições e parâmetros devem ser salvos na regra e expostos em edição, grade, API e snapshot.

Campos obrigatórios visíveis:

* Escopo de origem/conexão e dimensões opcionais: usar o mecanismo de seleção da regra; para este incidente configurar `cloud_origem=azure-4p`, e, se o bloqueio for específico à PTU, serviço/subcategoria/projeto explicitamente na UI.
* Unidade da validade: dias; valor inicial configurado **30**, sem constante financeira escondida.
* Relógio de referência: data da execução; no simulado, `simulated_as_of`. Não comparar um simulado histórico com hoje.
* Evidência de cobertura: data de cobertura da invoice/coleta bem-sucedida, ou coluna/período da fonte expressamente selecionados como fallback. Mostrar qual evidência foi usada e as limitações.
* Ação quando idade **> 30 dias**: interromper a previsão do escopo, mantendo realizado e snapshots anteriores. Confirmar na UI que “30 dias” não dispara e “31 dias” dispara.
* Ação para escopo com realizado zero em mês **confirmadamente completo**: encerrar forecast daquele escopo, se habilitada. Zero deve ser medido na medida configurada, não com `<= 0`; valores negativos válidos não são ausência.
* Política de evidência ausente: alertar/bloquear run ou suspender escopo, conforme opção visível. Nunca inventar cobertura.

Implementação genérica da validade não deve citar Azure, PTU, nomes de projetos nem IDs de regra no cálculo. O backend recebe escopo, limites e evidência da regra. Desativar a regra retira essa política.

Aplicar a validade depois das regras de recorrência e ajustes, antes da publicação, ou garantir precedência terminal equivalente, documentada e visível: uma regra posterior de Purchase não pode ressuscitar o escopo suspenso. Respeitar vigência da regra e não afetar projeções de períodos anteriores à interrupção. A idade deve ser calculada para o momento de avaliação definido no contrato da execução; não presumir que todo mês futuro estará sem novas coletas.

**Fonte atrasada e série ausente são condições diferentes.** Conexão ativa pode continuar enviando outros custos e parar apenas a invoice de Purchase; nesse caso o grupo de validade deve ser o fluxo/escopo da invoice declarado na regra. Se os dados de uma fonte terminam em julho, não avançar artificialmente seu cutoff até outubro para simular meses vazios. Avaliar a idade em relação à execução, mantendo o cutoff histórico intacto.

Não usar somente `MAX(ChargePeriodStart)` como data de coleta sem deixar essa escolha explícita: uma compra no dia 2 pode cobrir o mês completo. Preferir cobertura do billing period e metadata de coleta completa quando disponível. Metadata financeira detalhada continua Iceberg, PostgreSQL guarda apenas controle.

### R4. Evidência específica de Azure-4P e reaproveitamento da regra existente

Consulta local confirmou `cloud_origem=azure-4p` com último `_billing_period=202607` e última data de cobrança parseada 02/07/2026. PTU `Azure AI Foundry Provisioned Throughput Reservation` em julho tem:

* Purchase: **R$ 161.311,95639**.
* Refund: **-R$ 161.311,95639**.
* Total líquido: zero, com fatos positivos e negativos presentes.

Não foram encontradas linhas de PTU Azure-4P nos snapshots 381/383 consultados. Portanto, não afirmar que reverter o patch necessariamente fará essa PTU reaparecer: reproduzir o caso na versão anterior e nas regras atuais.

Já existe a regra visível **“Vivo Mais - encerrar reserva PTU Azure-4P estornada”**, id local 21, `CANCEL_ON_REFUND`, escopo cloud Azure-4P, projeto Vivo Mais, serviço Microsoft.Capacity, categoria Purchase, subcategoria PTU, início 202609. Ela é uma suspensão explícita da compra selecionada; não depende de achar Refund entre as dimensões do forecast. Verificar se escopo e vigência representam o cancelamento real antes de alterar. Seu início em setembro não alcança meses anteriores.

Priorizar reutilizar essa regra para compromisso estornado confirmado. A regra SOURCE_VALIDITY trata a falta de invoice >30 dias. A opção “zero confirmado” cobre fontes/escopos com invoice completa cujo valor líquido é zero; não usar zero líquido de todo Azure para cancelar todas as reservas, pois Usage/Return sintéticos também se anulam normalmente.

Se quiser disparar cancelamento automaticamente por Refund, evoluir um comportamento declarativo separado ou opção explícita de vínculo à compra, usando referência configurada. Não tratar todo Return sintético como refund de contrato. Para esta correção mínima, reaproveitar o cancelamento visível existente é suficiente quando o evento já foi confirmado.

### R5. Arquivos e aceite da regra de validade

Revisar `forecast.py` em models/router, migração Alembic, `forecast_service.py`, criação de snapshot no worker/router, `apps/web/src/services/api.ts`, `forecastConstants.ts`, `ForecastRuleDialog.tsx`, `ForecastRulesDataGrid.tsx` e ajuda de campos. Garantir create/read/update/duplicate e enum permitido, validação das colunas e tipos, seleção de escopo com AND correto e persistência das opções. Não substituir as regras de Purchase/Return que voltaram a funcionar após reversão.

Exibir no resultado do run: regra aplicada, escopo, última cobertura conhecida, data avaliada, idade, limite, razão de suspensão e soma do forecast suprimido. Exibir diagnóstico mesmo quando não há série gerada, para que fonte histórica fora da janela apareça como suspensa e não suma silenciosamente. Snapshot precisa congelar parâmetros efetivos; não mutar regras durante o job.

Testes de aceite, com cenários independentes:

1. Return negativo de Azure TI continua negativo por 12 meses; Purchase mensal continua por 12 meses conforme regra original.
2. Fonte/fluxo Azure-4P só com julho, execução em outubro, validade 30: suspende escopo selecionado sem afetar Azure TI.
3. Validade 30: idade 30 conserva previsão; idade 31 suspende. Regra desativada não suspende.
4. Fonte sem invoice não vira “zero confirmado”; invoice completa com zero habilita somente política explícita de zero.
5. PTU Purchase+Refund julho cancela apenas compromisso/projeto selecionados, respeitando vigência da regra; outras reservas continuam.
6. Usage+Return sintéticos que se anulam não são tratados como fonte inexistente ou contrato cancelado.
7. Série negativa válida não é encerrada por valor negativo; mês futuro ausente não é classificado como realizado zero.
8. Simulado histórico calcula idade com sua data simulada e usa cobertura disponível até ela; não usa hoje, coleta ou status futuro.
9. Invoice mensal coberta até fim do mês não é declarada velha só porque compra ocorreu no dia 2; opção fallback mostra limitação.
10. Regra visível é editável, duplicável e auditável; fonte retomada só libera projeção com evidência válida e sem reaproveitar base cancelada indevidamente.
11. IBM por intervalo, Marketplace, PTU de outras origens, settlement e planning/rateio não regridem.

Depois da restauração, medir separadamente a inflação residual de Purchase SAPTDF no mês aberto. Se persistir, apresentar evidência antes de implementar mudanças de calendário/compromisso. Isso não bloqueia a identificação e reversão da regressão de 08/10, mas impede declarar que todos os desvios foram resolvidos por ela.

### R6. Prompt revisado para o GPT Luna

“Execute a revisão R1–R5 de `docs/ai/plano-correcao-forecast-compromissos-luna.md`. Primeiro reverta o commit `7d63645a` que zerou Return negativo e interrompeu Purchase nos meses futuros; valide o comportamento restaurado. Depois trate invoice ausente >30 dias por regra SOURCE_VALIDITY visível na aba Forecast → Regras, com escopo e parâmetros persistidos, e reaproveite a regra de cancelamento de PTU existente quando adequada. Não implemente os novos motores de mensal/espelho do diagnóstico original para corrigir esta regressão. Não chumbe Azure-4P/PTU/projetos no motor. Faça commit imediatamente após cada alteração de código, preserve alterações preexistentes, valide snapshots e só publique após reconciliação. Documente problemas residuais separadamente.”

---

# Diagnóstico original preservado como referência técnica

Data da investigação: 09/10/2026. Escopo: diagnóstico e plano; nenhuma correção no motor ou nas regras foi aplicada nesta investigação.

## 1. Instrução de execução

Leia este documento integralmente antes de implementar. Execute os pacotes na ordem. Após cada alteração de código, faça imediatamente o commit Git correspondente, conforme instrução do usuário. Preserve alterações preexistentes do workspace, inclusive exclusões em `backups/`; nunca use `git add -A` indiscriminadamente. Não publique nem reprocese dados antes de concluir os critérios de validação dos pacotes.

**Exigência explícita do usuário:** qualquer regra nova ou alterada deve aparecer e ser editável em `http://localhost:5173/forecast?dept=Consumo+Cloud&tab=rules`. Nenhuma seleção de provedor, reserva, Savings Plan, projeto ou tratamento especial pode ficar chumbada no motor. O código implementa comportamentos genéricos; a tabela `forecast_business_rule` determina onde eles se aplicam. Não criar outra tela de configuração.

O trabalho deve corrigir compras recorrentes, cancelamento contábil dos pares Usage/Return e continuidade do horizonte. Reduzir crescimento ou dividir os valores por dois não corrige as causas.

## 2. Evidências obtidas no ambiente local

As consultas foram executadas em Trino local e no PostgreSQL configurado por `apps/api/.env`. O PostgreSQL Docker da porta 5434 estava sem configurações de forecast; não usá-lo como fonte de verdade deste incidente. Carregar `apps/api/.env` com `dotenv` antes de importar `app.database`, sem imprimir credenciais.

Na Silver, o projeto está grafado `SAPTDF`. O filtro literal `IN ('saptdf')` retornou zero linhas; para diagnóstico usamos `lower(organograma_nivel_5) = 'saptdf'`. A Silver contém realizado; projeções estão nos snapshots `iceberg.forecast.result_run_*` e na planning publicada. A consulta original, sozinha, não mede forecast.

Configuração ativa encontrada: id 9, `Forecast padrão FinOps`, versão 21, medida `realizado`, fonte Silver `dest_modelo_focus`, data `chargeperiodstart`, período `_billing_period`, cutoff D-2, crescimento mensal 0,0083, `AUTO_BACKTEST`, janelas 7/14/28, horizonte 12. Dimensões incluem categoria, tipo de compromisso e níveis organizacionais; não incluem `commitmentdiscountid` nem `is_synthetic`. Rateio por intervalo está habilitado apenas para IBM.

Regras relevantes no banco:

| ID observado | Nome | Identificação | Comportamento |
|---|---|---|---|
| 7 | Return simulado | chargecategory = Return | RECURRING_MONTHLY |
| 8 | Purchase recorrente Savings Plan | chargecategory = Purchase | RECURRING_MONTHLY |
| 9 | Refund cancela compromisso | chargecategory = Refund | CANCEL_ON_REFUND |

IDs são evidência local, não devem ser usados como seletor fixo na implementação/migração. A regra 8 também alcança reservas e outras compras, apesar do nome. Há outras regras de Marketplace, PTU e OCI que exigem regressão.

Snapshots observados:

* Run 382: execução normal, cutoff registrado 07/10/2026, concluída em 09/10/2026.
* Run 383: simulado em 02/10/2026, cutoff 30/09/2026. Deve continuar isolado da planning corrente.

| SAPTDF | Valor |
|---|---:|
| Realizado setembro, total líquido | R$ 285.074,92 |
| Reserva Purchase setembro | R$ 117.259,98 |
| Reserva Usage setembro | R$ 328.187,98 |
| Reserva Return setembro, incluindo linha fora do nível 6 | -R$ 328.187,98 |
| Reserva Purchase realizado outubro na fonte consultada | R$ 88.783,58 |
| Reserva Purchase forecast restante outubro, run 382 | R$ 329.767,60 |
| Total projetado outubro, simulado run 383 | R$ 644.437,52 |
| Total projetado novembro, run 382 | R$ 626.312,83 |

Comparar outubro simulado com setembro realizado evidencia escala e composição, mas não constitui medição de acurácia de outubro: outubro ainda está aberto.

Em novembro do run 382, SAPTDF tem Purchase de R$ 117.259,98, Usage de reserva de R$ 331.899,18 e Return de reserva igual a zero. Em dezembro, Purchase também vira zero, mas Usage continua.

Savings Plans Azure TI, agregado de todos os projetos:

| Categoria | Realizado setembro | Forecast outubro run 382 | Forecast novembro run 382 |
|---|---:|---:|---:|
| Purchase | R$ 49.822,49 | R$ 49.822,49 | R$ 0,00 |
| Usage | R$ 60.045,88 | R$ 48.770,46 | R$ 61.468,81 |
| Return | -R$ 60.045,88 | -R$ 48.807,82 | R$ 0,00 |

Isso confirma perda dos estornos e interrupção da recorrência em Savings Plans também. O fator de inflação não é constante; há erros que aumentam e outros que reduzem a previsão.

As regras de clone 1 e 2 geram Return a partir de Usage, mudando `ChargeCategory`. A classificação organiza o retorno em outro nível 6. São estornos sintéticos contábeis, não refunds reais de cancelamento. A Silver tem `source_record_id`, `clone_source_record_id`, `clone_rule_id`, `clone_rule_version` e `is_synthetic` para rastrear a associação.

## 3. Causas no código

### 3.1 Valores negativos tratados como ausência

Em `apps/api/app/services/forecast_service.py`, `_replicated_month_value` testa `last_closed_month_total <= 0` e devolve zero para recorrência futura. A regra Return simulado alcança séries negativas legítimas e as zera. O motor mantém Usage positivo; assim, custos que se anulavam no realizado ficam líquidos no forecast.

Correção genérica necessária: zero e ausência devem ter semântica distinta de valor negativo. Não aplicar `abs` ao resultado financeiro. Apenas permitir negativos na replicação mensal não é suficiente para os clones: replicar Return do mês fechado e projetar Usage por média diária/crescimento pode romper o cancelamento.

### 3.2 Compras mensais viram média diária

No loop de séries, `nonzero_days = (daily > 0).sum()` conta dias em toda a janela. O limite de três dias não representa frequência mensal. SAPTDF tem três compromissos com compras nos dias 1, 5 e 10; setembro completo mais outubro parcial somam cinco dias com cobrança. A série deixa de ser MONTHLY_LUMP e entra em MEAN_7D.

Reconstrução exata do run 382: últimas compras em 01/10 e 05/10 somam 88.783,5834927; a janela de sete dias resulta em 12.683,3690704/dia. A série usa o último lançamento, dia 5, e projeta 26 dias: 88.783,5834927 / 7 × 26 = 329.767,5958300. A regra recorrente conserva essa projeção no mês aberto porque `lumpy=False`.

O histórico de setembro tem três cobranças: R$ 58.629,99 no dia 1 e R$ 29.315,00 nos dias 5 e 10. Outubro consultado tem duas compras: R$ 59.189,06 no dia 1 e R$ 29.594,53 no dia 5; a terceira ainda não apareceu. Sua falta não autoriza repetir as duas primeiras por 26 dias.

### 3.3 Horizonte futuro é confundido com mês realizado vazio

Em `_apply_forecast_business_rules`, para cada destino futuro o motor busca o mês anterior em `realized_month_totals` e usa `.get(prior_period, 0.0)`. Meses futuros não existem nesse dicionário; ausência vira zero e encerra a recorrência. Isso zera Purchase no segundo mês projetado ou até no primeiro, conforme o cutoff da série.

O stop por mês fechado realmente vazio deve continuar funcionando. Aplicá-lo apenas a períodos observáveis e completos até o cutoff, jamais a um período futuro ou aberto. A decisão de recorrência deve ser feita a partir do histórico disponível, não recalculada como se cada mês futuro já tivesse sido coletado.

### 3.4 Última compra não é data de cobertura

`series_cutoff = min(cloud_effective_cutoff, series_last_date)` usa última transação como relógio da série. No simulado 383, a última compra é 10/09, mas setembro está fechado no cutoff 30/09. `_build_series_context` trata setembro como corrente e usa agosto como mês fechado: Purchase outubro vira R$ 116.519,41, embora setembro completo seja R$ 117.259,98.

A recorrência mensal precisa de calendário de faturamento e cobertura da fonte distintos da data de uma compra. Não alterar indiscriminadamente o cutoff de todas as séries: uso diário, fontes atrasadas e IBM já possuem contratos específicos.

## 4. Contrato da solução e controles visíveis

Adotar dois comportamentos genéricos novos na lista de regras, com parâmetros estruturados persistidos na própria regra. Escolher nomes finais consistentes com os enums existentes, respeitando o limite `String(40)`:

1. `MONTHLY_COMMITMENT`: “Projetar cobrança mensal por compromisso”. Usa a coluna de referência configurada, calendário de cobrança, valor do último mês completo e residual do mês aberto. Não usa médias diárias para compras mensais selecionadas.
2. `MIRROR_SOURCE_FORECAST`: “Espelhar forecast da origem com sinal oposto”. Relaciona fatos sintéticos à origem pelas colunas configuradas e produz seu forecast negativo nas dimensões de destino do estorno. Não confundir com CANCEL_ON_REFUND.

É aceitável implementar o primeiro como opção explícita de RECURRING_MONTHLY em vez de outro enum, desde que comportamento, campos e escolha fiquem visíveis e persistidos. Para Luna, preferir enums separados para evitar mudar implicitamente todos os contratos legados.

Adicionar `behavior_options` JSON à `forecast_business_rule`, com schema validado por comportamento, compatível com regras antigas (default `{}`). Não aceitar fórmulas, SQL nem Python livres.

Campos visíveis para mensal: coluna de frequência, valores aceitos, coluna da referência, base “último mês completo”, residual “total esperado menos compras já realizadas”, política para compromisso ausente, prazo explícito opcional via coluna configurada. Campos visíveis para espelho: indicador sintético, valor selecionado, coluna que referencia a origem, chave da origem, categoria de origem, fator -1, política de vínculo ausente “bloquear publicação e informar”.

A interface deve permitir identificar `Recurring` e `Monthly`. O backend não presume que todo Purchase é recorrente nem que todo Return é sintético. OR nas cláusulas existentes não equivale a AND: não criar `Purchase OR Recurring` por engano. Manter Purchase como identificação e usar filtro de frequência explicitamente declarado no comportamento, ou evoluir cláusulas com operador lógico visível e retrocompatível.

Regras propostas para este ambiente:

| Regra visível | Seleção declarada | Ação |
|---|---|---|
| Compras mensais de compromissos | Purchase + frequência configurada Monthly/Recurring + referência commitmentdiscountid | MONTHLY_COMMITMENT |
| Return sintético de compromissos | Return + is_synthetic=true, escopo Azure TI confirmado | MIRROR_SOURCE_FORECAST |

Restrição Reservation/SavingsPlan, se usada, deve estar expressa na seleção da regra e visível na UI. Uma regra genérica deve suportar outros tipos declarados pelo usuário. Não escrever `if cloud == 'azure-ti'`, `if project == 'SAPTDF'`, IDs locais ou nomes de regras como lógica financeira.

## 5. Pacotes de implementação

### P0 — Baseline reproduzível

Ler `FORECAST.md`, `docs/ai/AGENTS.md`, `03-business-rules.md`, os arquivos citados e testes existentes. Registrar commit-base, status do workspace, config ativa e regras. Guardar apenas metadata e agregados financeiros em documento de diagnóstico; fatos financeiros permanecem Iceberg.

Reexecutar as consultas da seção 7 antes de corrigir. Registrar cutoffs e IDs dos snapshots usados; não assumir que 382/383 continuam sendo os últimos. Montar fixtures pequenas com três compras mensais, pares Usage/Return de sinais opostos e horizonte de 12 meses. Não copiar milhões de fatos para Pandas.

Aceite: reprodução dos quatro caminhos causais e números acima, ou registro da mudança de dados que explica diferenças.

### P1 — Modelo, API, UI e snapshots

Arquivos: `apps/api/app/models/forecast.py`, migração Alembic conforme padrão local, `apps/api/app/routers/forecast.py`, `apps/web/src/services/api.ts`, `ForecastRuleDialog.tsx`, `ForecastRulesDataGrid.tsx`, `forecastConstants.ts`, `forecastFieldHelp.ts`.

Implementar os enums/opções e validação de coluna contra schema real, incluindo booleanos e chaves textuais. Adicionar formulários específicos aos comportamentos e resumo legível dos parâmetros na grade. Roundtrip create/read/update/duplicate deve preservar opções. Verificar serialização explícita das regras em todos os endpoints e caminhos de duplicação.

Resolver colunas sem diferenciar casing. Colunas necessárias às regras que não estão nas dimensões públicas devem virar contexto interno de execução; não exigir que o usuário adicione milhões de IDs de linha às dimensões. Validar obrigatórios e apontar erro com nome da regra e coluna. Sem fallback silencioso quando falta referência.

Congelar no `config_snapshot` configuração completa, regras/opções/versões, ajustes e política de cutoff. Inspecionar worker/router que montam esses snapshots: 382/383 mostraram contratos de configuração incompletos. Execução deve consumir o snapshot congelado; mudança na UI durante um job não muda o cálculo em andamento.

Aceite: regras editáveis na aba existente, parâmetros persistidos e snapshot auditável. Fazer commit imediatamente após o lote de alterações.

### P2 — Zero, negativo, cobertura e horizonte

Arquivo central: `forecast_service.py`.

Corrigir `_replicated_month_value` para suportar totais negativos válidos nas regras mensais genéricas; comparar zero com tolerância numérica documentada. Representar em contexto `observed_months`, `closed_months`, `coverage_cutoff` e base escolhida. Não inferir cobertura completa só por haver até três dias de cobrança.

Corrigir o guard de `prior_period`: ausência de mês futuro não termina recorrência; mês observado completo realmente sem fatos segue a política declarada da regra. Avaliar cessação uma vez com histórico/cobertura e reutilizar a decisão no horizonte.

Para MONTHLY_COMMITMENT, usar cutoff de cobertura da fonte/conexão ou simulado, independentemente da última compra. Buscar o mês anterior completo a esse cutoff, com partition pruning. Não pular para um mês antigo não nulo sem registrar e sem obedecer à política de ausência. Para as séries comuns, preservar políticas de cutoff já existentes e validar regressão de fontes atrasadas.

Detecção matemática legada de lump deve ser simétrica em sinal: dias não zero por magnitude, totais com sinal preservado. Ela não deve prevalecer sobre uma regra mensal explícita. Não ampliar o limiar fixo de 3 para disfarçar o incidente.

Aceite: horizonte positivo e negativo chega ao mês 12; stop em mês fechado vazio funciona; simulado outubro usa setembro completo como base das compras mensais. Commit imediato.

### P3 — Compra mensal por compromisso

Extrair os agregados de compras elegíveis por dimensões, período e coluna de referência configurada, mantendo a identificação interna do compromisso. A regra ativa define o ramo mensal antes de rodar média diária. Excluir esses agregados do ramo diário com filtros mutuamente exclusivos; cada fato participa de um componente de forecast.

Mesmo que as dimensões públicas agreguem três reservas, manter subchave interna por `commitmentdiscountid` até calcular base/residual. SAPTDF tem três referências distintas preenchidas. Depois agregar de volta para as dimensões públicas, sem sobrescrever contextos de componentes diferentes.

Para cada referência ativa: `B = total do último mês completo`, `A = compras do mês aberto observadas até o cutoff`. Política visível de residual: `max(B - A, 0)` para compras positivas. Isso é uma estimativa pelo último mês, não preço contratado conhecido. Para referência com compra já presente acima de B, residual é zero. Não prever novamente as parcelas já cobradas; não dividir B pelos dias restantes. Se o contrato admitir múltiplas parcelas no mês, usar a política de total mensal explicitamente selecionada, sem presumir uma linha única.

SAPTDF: referências cobradas nos dias 1 e 5 terão residual zero; referência ausente do dia 10 terá estimativa de R$ 29.315,00 pela base de setembro. O preço observado de outubro sugere aproximadamente R$ 29.594,53, mas não extrapolar esse reajuste sem configuração/preço autorizado. Estimativa mensal inicial será ~R$ 118.098,58 com base por referência. O objetivo é explicar o cálculo; não fixar esse total no código.

Savings Plan: setembro cobrado no dia 17, outubro ainda sem Purchase consultado. A regra prevê o compromisso ausente no mês aberto em ~R$ 49.822,49 e mantém a recorrência futura. Para meses futuros aplicar crescimento da regra; decidir e exibir se ele substitui o crescimento global (recomendação: comportamento mensal usa exclusivamente crescimento da regra, já 0 nas regras atuais). Evitar crescimento aplicado duas vezes.

Respeitar `start_period`, `end_period`, desativação, ausência observada, cancelamentos e novo compromisso sem mês fechado. Para este último, exigir política explícita, por exemplo usar primeira compra mensal observada com aviso ou manter sem base e informar. Não inferir fim do contrato por `ChargePeriodEnd`: intervalos SAPTDF de 05/09→04/10 e 10/09→09/10 são ciclos de cobrança.

Aceite: reserva outubro não recebe R$ 329,8 mil adicionais; falta de uma das três compras é preservada; referência ainda não lançada não zera todas as outras; 12 meses respeitam vigência. Commit imediato.

### P4 — Estorno sintético acompanha a origem

Regra MIRROR_SOURCE_FORECAST ativa define ligação e sinal. No Trino, associar `clone_source_record_id` do sintético ao `source_record_id` da origem, qualificando pela conexão e demais elementos necessários à unicidade. Verificar unicidade antes de JOIN para impedir multiplicação de fatos. Validar também método/versão de clone quando necessário. Não associar só por nome de projeto ou commitmentdiscountid, nem por índice Pandas.

Agregar a associação no Trino em ponte compacta entre chave de série da origem e chave de série do estorno. Conservar as dimensões de destino, inclusive nível 6 diferente. IDs de linha não entram em GROUP BY de série nem são carregados detalhadamente em Pandas. A ponte deve incluir histórico/cobertura suficientes e detectar origem ausente ou mais de um vínculo conflitante.

Calcular projeção da origem uma vez. Produzir estorno proporcional ao componente realmente coberto por clones usando a ponte. Se a origem agrega consumo com e sem clone, separar componentes internos antes do modelo; não estornar a parte sem clone. Se uma origem tem destinos múltiplos legítimos, usar pesos conservativos extraídos da associação, que somem 1; não duplicar a previsão completa em cada destino.

Aplicar crescimento e ajustes da origem antes do espelho para manter sinais simétricos. Definir precedência explícita: espelho é terminal para fatos selecionados, substitui a antiga recorrência Return e não permite que a regra antiga o reescreva depois. Mostrar no relatório de execução regras sobrepostas e resolução. Cancelamentos da origem devem propagar ao espelho. Ajuste direto ao estorno que rompa o par exige política visível específica; default bloqueia conflito e explica.

Depois aplicar medidas financeiras duais sob os contratos existentes. Não alterar settlement de contratos para esconder a inflação. Validar cancelamento em `forecast_value`, `realizado_cloud` e, onde os efeitos financeiros permitirem, `realizado`; divergência de contrato deve ser explicada.

Na ausência de vínculo ou schema requerido, registrar regra, contagem, valor afetado e bloquear publicação desse run conforme política declarada; manter planning anterior. Não fazer Return=0 como fallback.

Aceite: Usage coberto + Return sintético ≈ 0 por mês e grupo contábil comum, com estorno no nível organizacional correto. Refund real não entra no espelho. Commit imediato.

### P5 — Migração visível das regras

Atualizar o seed `PROVIDER_RULE_PACKS` e criar migração de metadata idempotente. Não somente alterar seeds: regras existentes já persistidas continuariam erradas. Identificar templates conhecidos por contrato de seleção/comportamento e config/provedor, preservando customizações. Regras divergentes devem aparecer como pendência de revisão, sem atualização cega pelo nome.

Migrar a regra de Purchase para mensal somente no escopo/frequências adequados, e a regra Return simulado para espelho sintético. Separar outras compras legítimas em regras declaradas quando necessário. Preservar regras especiais de PTU, Marketplace e refunds, com precedência documentada. Incrementar versão de regra e configuração; registrar antes/depois e origem da migração.

Não implementar reescrita invisível a cada startup. A UI deve mostrar o estado persistido, permitir desativar os novos comportamentos e explicar impacto. Desativar uma regra retira seu tratamento no motor. Incluir resumo “regra → séries atingidas → base → residual → vínculos → alertas” no resultado do run, sem expor IDs de milhões de fatos.

Aceite: tela existente exibe integralmente as decisões; edição/salvamento/desativação têm efeito; migração reaplicada não duplica regras. Commit imediato.

### P6 — Validação e publicação

Adicionar testes de regressão de comportamento, com fixtures independentes das fórmulas internas. Executar os testes relevantes de `test_forecast_business_rules.py`, `test_forecast_cutoff.py`, `test_forecast_allocation.py`, `test_forecast_ibm_allocation.py`, `test_planning_rateio_preservation.py` e `test_forecast_daily_drilldown.py`, mais os novos casos abaixo. Fazer commit após adicionar/alterar código e então executar os checks; se corrigir falha, commit imediatamente após a correção.

Casos obrigatórios:

* Total negativo recorrente não vira zero; total zero fechado segue política de encerramento.
* Horizonte de 12 meses não exige realizado futuro, nem encerra Purchase no mês 2.
* Três compromissos com compras em 1/5/10 em dois meses não viram taxa diária.
* Mês aberto com duas compras prevê só a referência ainda ausente; mês com todas já cobradas gera residual zero.
* Simulado em 02/10 com última compra 10/09 usa setembro fechado, sem carregar compras de outubro.
* Savings Plan com aniversário dia 17 tem residual no mês aberto antes da compra e zero após compra completa.
* Ponte de clone mantém vínculo ao projeto/nível de destino; ID duplicado, vínculo faltante e consumo parcialmente clonado não duplicam nem estornam consumo descoberto.
* Ajustes/crescimento/cancelamento preservam o par; Refund real e créditos negativos não sintéticos têm comportamento independente.
* Meses realmente vazios, troca de classificação, fonte atrasada, IBM por intervalo, Marketplace ONESHOT/RECURRING, PTU, GCP diário e contratos financeiros não regridem.
* Sem regra ativa de mensal/espelho não existe tratamento especial oculto. UI/API persistem os parâmetros e o run congela a versão.

Executar primeiro em snapshot novo sem publicar planning, com modo de validação explícito se esse caminho ainda não existir. Comparar agregados do run novo com os snapshots antigos por mês/categoria/projeto. Para pares completamente clonados, tolerância monetária de R$ 0,01 por grupo reconciliado (documentar eventual agregação/arredondamento que exija tolerância maior). Não exigir equality linha a linha quando dimensões originais/estorno diferem.

Somente depois publicar forecast corrente e reconstruir os consumidores dependentes pela rotina já existente. Confirmar camada efetiva da planning via `lakehouse_layers`, não presumir Gold: o sistema migrado usa Silver como detalhe. Snapshot simulado não publica planning. Preservar snapshots 382/383 e demais runs históricos; gerar novas execuções para acurácia.

Validar que o dashboard soma realizado + somente residual do mesmo mês; meses futuros usam previsão completa. Confirmar rateio preservado, ausência de duplicação de realizado na planning e cache/cubos atualizados. Rollback: reverter commits/migração de metadata de forma auditada e restaurar a publicação anterior sem modificar snapshots de auditoria.

Atualizar `FORECAST.md` e `docs/ai/03-business-rules.md` para remover as descrições contraditórias de cutoff e explicar regras mensais/espelho e diagnóstico visível. Entregar relatório final com commits, regras migradas, testes executados, comparativo antes/depois e pendências reais.

## 6. Critérios finais de aceite

1. SAPTDF não replica diariamente parcelas mensais já lançadas.
2. Pares sintéticos de reserva e Savings Plan conservam soma líquida zero no forecast do horizonte, conforme parcela coberta.
3. Compromissos recorrentes válidos aparecem até o fim do horizonte ou da vigência configurada.
4. Simulados e mês aberto usam cobertura correta e não misturam realizado futuro.
5. Regra, seleção, referência, política de residual e espelho estão visíveis/editáveis na aba de regras; motor não contém seletores financeiros específicos escondidos.
6. Snapshot registra o contrato exato usado e consumidores apresentam valores reconciliáveis.

## 7. Consultas de diagnóstico para reutilizar

Usar `scripts/trino_query.py` ou serviço Trino existente. As consultas abaixo são somente leitura. Substituir IDs de snapshots explicitamente após identificar o run desejado; não misturar run simulado com forecast corrente.

```sql
-- Realizado e cancelamento contábil por categoria.
SELECT cloud_origem, organograma_nivel_6, _billing_period,
       chargecategory, commitmentdiscounttype, is_synthetic,
       COUNT(*) AS linhas, SUM(realizado) AS realizado,
       SUM(realizado_cloud) AS realizado_cloud
FROM iceberg.silver.dest_modelo_focus
WHERE lower(organograma_nivel_5) = 'saptdf'
  AND _billing_period IN ('202609', '202610')
GROUP BY 1,2,3,4,5,6
ORDER BY 3,2,4;

-- Composição da previsão, incluindo métodos escolhidos.
SELECT forecast_month, organograma_nivel_6, chargecategory,
       commitmentdiscounttype, selected_method,
       SUM(forecast_value) AS forecast, SUM(realizado) AS realizado,
       SUM(realizado_cloud) AS realizado_cloud
FROM iceberg.forecast.result_run_382
WHERE lower(organograma_nivel_5) = 'saptdf'
  AND forecast_month IN ('202610','202611','202612')
GROUP BY 1,2,3,4,5
ORDER BY 1,2,3;

-- Referências e calendário das compras mensais.
SELECT _billing_period, chargeperiodstart, chargeperiodend,
       chargefrequency, commitmentdiscountid, source_reservation_id,
       SUM(realizado) AS valor
FROM iceberg.silver.dest_modelo_focus
WHERE lower(organograma_nivel_5) = 'saptdf'
  AND chargecategory = 'Purchase'
  AND _billing_period IN ('202608','202609','202610')
GROUP BY 1,2,3,4,5,6
ORDER BY 1,2;

-- Reserva e Savings Plan em todos os projetos.
SELECT cloud_origem, commitmentdiscounttype, chargecategory,
       is_synthetic, clone_rule_id, COUNT(*) AS linhas,
       SUM(realizado) AS valor
FROM iceberg.silver.dest_modelo_focus
WHERE _billing_period = '202609'
  AND commitmentdiscounttype IN ('Reservation','SavingsPlan')
GROUP BY 1,2,3,4,5
ORDER BY 1,2,3;
```

Não construir JOIN de linhagem detalhado sem validar unicidade e limitar período/conexão. Não cancelar toda categoria Return nem eliminar Usage coberto: ambos são necessários para os níveis contábeis existentes. Não ativar rateio global de Azure para tratar compras mensais; os intervalos cruzam meses e essa mudança alteraria também consumo diário.

## 8. Prompt pronto para iniciar a execução no GPT Luna

“Implemente o plano em `docs/ai/plano-correcao-forecast-compromissos-luna.md`, começando por P0 e avançando em ordem até os critérios de aceite. A correção financeira deve ser governada pelas regras visíveis na aba Forecast → Regras, sem seletores de projeto/provedor chumbados. Faça commit Git imediatamente após cada alteração de código. Preserve mudanças preexistentes. Valide compras mensais por referência, espelho dos estornos sintéticos, meses futuros, cutoff e snapshots; só publique depois da reconciliação. Ao terminar, informe commits, regras alteradas, testes e comparativo financeiro antes/depois.”
