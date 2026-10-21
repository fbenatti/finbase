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
