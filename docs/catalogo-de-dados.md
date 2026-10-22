# Catálogo de dados (Operação de dados › Catálogo de dados)

Tela: `apps/web/src/pages/Destinations/Destinations.tsx`, aba **Tabelas de saída** de `pages/Tables/Tables.tsx`.
A aba **Relações entre tabelas** (`pages/Relationship/Relationship.tsx`) continua sendo o editor de vínculos.

## Layout (redesenho de 22/10/2026)

1. **Indicadores**: tabelas de saída (e quantas em uso), campos catalogados, origens vinculadas e vínculos de campo, com alerta de tabelas sem origem.
2. **Barra de ferramentas**: busca por nome, nome físico ou origem; filtro Todas / Em uso / Fora de uso; alternância lista ↔ cartões; Atualizar; Nova tabela.
3. **Lista de tabelas**: tabela, campos, origens (chips), vínculos, partição, data da última atualização e situação. Clique abre o detalhe.
4. **Mapa de relações**: diagrama SVG origem → tabela de destino; espessura e número da linha = campos vinculados. Clique numa tabela abre o detalhe na aba Relações.
5. **Detalhe (painel lateral)** com abas:
   - **Campos**: busca, tipo, "Não nulo", "Vinculado"/"Sem origem", incluir e remover campo.
   - **Mapeamentos**: origem, campo de origem → campo de destino, transformação.
   - **Relações**: diagrama só das origens daquela tabela.
   - **Partição**: `DestinationPartitionSettings`.

## Rotas do backend usadas (`apps/api/app/routers/destinations.py`, prefixo `/dados/catalogo`)

| Chamada (api.ts) | Rota |
|---|---|
| getDestinationTables | GET `/dados/catalogo/tabelas/` |
| getDestinationTable | GET `/dados/catalogo/tabelas/{id}` |
| createDestinationTable | POST `/dados/catalogo/tabelas/` |
| addDestinationColumn | POST `/dados/catalogo/tabelas/{id}/columns/` |
| deleteDestinationColumn | DELETE `/dados/catalogo/colunas/{id}` |
| getSourceDestMappings | GET `/dados/catalogo/vinculos/` (sem filtro = todos) |
| getConnections | conexões de origem (nomes no diagrama) |

A contagem de campos faz uma chamada de detalhe por tabela, em paralelo; o catálogo é pequeno (dezenas de tabelas).
