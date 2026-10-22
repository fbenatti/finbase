# Vocabulário de dados do finbase.cloud

Os dois layouts usam a mesma nomenclatura de produto em menus, telas, formulários,
ajuda, relatórios, permissões e mensagens de execução.

| Referência interna | Nome no produto |
| --- | --- |
| Data Lake / Data Lakehouse | Central de dados |
| Iceberg | Acervo de dados |
| Bronze | Base de origem |
| Silver | Base preparada |
| Gold | Base consolidada |
| Cubos Gold | Cubos analíticos |
| Staging | Área temporária / tabelas temporárias |
| Trino | Serviço de consultas |
| PostgreSQL | Banco de dados |
| Nessie | Catálogo de dados |
| MinIO | Armazenamento de arquivos |
| ETL | Processamento de dados / coleta, conforme a ação |

## Navegação

- Base de origem: `/dados/explorador/origem`.
- Base preparada: `/dados/explorador/preparados`.
- Base consolidada: `/dados/explorador/consolidados`.
- Área temporária: `/dados/explorador/temporarios`.
- Materialização de cubos: `/operacoes/cubos`.

Cada menu tem uma rota exclusiva, sem parâmetros de seleção de página. O catálogo
no backend registra os menus automaticamente e permite controlar a disponibilidade
por cliente. Veja [menu-catalog.md](menu-catalog.md).

## Limites entre apresentação e integração

`apps/web/src/utils/dataPresentation.ts` concentra rótulos e a tradução de mensagens
operacionais, incluindo mensagens de versões anteriores ainda presentes no histórico
ou na API. O tratamento acontece na apresentação, sem alterar os dados enviados.

Identificadores físicos, códigos de tarefas, permissões, parâmetros de API e nomes
usados em consultas executáveis continuam com seus valores originais. O editor de
consultas mantém o SQL real para que as consultas continuem válidas. Nomes de
provedores, serviços contratados e informações de faturamento conservam sua identidade.

`apps/web/src/utils/dataNavigation.ts` concentra a compatibilidade entre os links
anteriores e as URLs com nomes de produto. Os testes cobrem os destinos, os filtros,
a equivalência de permissões e a preservação dos identificadores internos.

## Glossário de telas e menus

Estes termos valem para menus, títulos, subtítulos, rótulos, botões, estados vazios e tooltips. Quando uma tela usar outra palavra, troque pela daqui.

| Termo | Uso no produto | Evitar |
| --- | --- | --- |
| Panorama | Painéis de leitura rápida, sem edição | Dashboard |
| Previsão | Projeção futura dos custos | Forecast, Projeção |
| Projeções manuais | Nome da tela de ajuste manual da previsão, por mês | Forecast manual |
| Aderência ao orçamento | Verifica se cada projeto está dentro ou fora do orçamento | Acurácia de Orçamento |
| Desvios da previsão | Diferença entre o previsto e o realizado, por departamento | Acurácia de desvio |
| Precisão | Quão perto a previsão ficou do realizado. Usada em telas de precisão das previsões | Acurácia, Accuracy |
| Justificativas dos desvios | Motivos registrados para desvios da previsão | Justificativas das projeções |
| Desvio | Diferença entre o previsto e o realizado | Gap |
| Justificativa | Explicação registrada para um desvio | Ack |
| Orçamento | Valor aprovado para o ano | Budget |
| Compromisso | Valor contratado ou reservado | Commitment |
| Rateio | Divisão de custo compartilhado entre áreas | Shared cost |
| Execução | Uma rodada de carga, previsão ou processamento | Run, Job |
| Carga | Ingestão de dados das fontes | Extração, quando for só carga |
| Extração | Coleta pontual de uma fonte | Export |
| Economia | Valor que deixou de ser gasto por uma ação | Savings |
| Desligamento | Parada programada de recursos ociosos | Shutdown |
| Órfão | Recurso sem dono ou sem uso | Orphan |
| Alerta | Desvio detectado pelo modelo, pendente de análise | Anomalia antes da confirmação |
| Anomalia confirmada | Alerta analisado e aceito como real | Alerta confirmado |
| Área | Grupo grande do menu (Panorama, Planejamento, Dados e operação) | Domínio, Módulo |
| Tela | Cada página navegável | Página, Módulo, Submenu |
| Configurar | Definir regras e parâmetros | Setup |

Regras de escrita:

- Títulos curtos, sem repetir o nome da área.
- Botões no infinitivo: "Salvar", "Executar previsão", "Exportar".
- Estados vazios dizem o próximo passo, por exemplo: "Nenhuma carga executada hoje. Agende uma em Agendamentos."
- Mensagens de erro dizem o que aconteceu e o que fazer, sem jargão técnico.
- Use "você" e frases curtas. Evite "favor", "clique aqui" e "solicite".
