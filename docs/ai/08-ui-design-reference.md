# 08 — Referência de UI: Connections

`/connections` é o modelo de referência para páginas operacionais do OpenFinops.
Ao modernizar outro módulo, preserve o comportamento do domínio e reproduza este
padrão visual e de interação antes de inventar uma variação local.

## Estrutura visual

1. **Faixa de contexto branca**: título da página, descrição de uma linha e abas.
2. **Abas de contexto**: segmentam a coleção pelo recorte mais útil ao módulo, sem
   criar páginas separadas. Ordene as abas de forma previsível; selecione a primeira
   opção disponível inicialmente e deixe a visão agregada (**Todas**, quando existir)
   por último. Em Connections, o contexto é o provedor e conexões CSV entram na aba
   do seu `cloud_scope` (por exemplo, CSV IBM aparece em IBM).
3. **Área de trabalho neutra** (`#f8fafc`) com um painel branco, borda discreta e
   sem sombras pesadas.
4. **Cabeçalho do painel**: identidade do contexto à esquerda (ícone quando houver),
   título/subtítulo e somente ações que pertençam ao contexto da página. Não copiar
   automaticamente botões de Connections: `Credenciais` é específico daquele domínio.
   **Não adicionar um botão global `Exportar`** apenas por existir no exemplo.
5. **Conteúdo principal**: busca curta acima do DataGrid; não usar uma grade de
   cards para entidades tabulares.

O conteúdo de uma aba deve continuar legível sem depender de cores. Em telas que
usam identidade de provedor, o logo é um reforço visual, não o único identificador.

## DataGrid operacional

Use o DataGrid da MUI quando a página representa uma coleção de registros. O
componente de referência funcional é
`apps/web/src/pages/Connections/ConnectionsDataGrid.tsx`; não basta copiar sua
aparência ou criar um `DataGrid` básico.

- Busca, paginação, ordenação e filtros são **server-side** para grades operacionais.
  Não usar `rows.filter(...)`, `initialState.pagination` ou `GridToolbar` como
  substitutos. A exceção só é aceitável para uma lista local, imutável e pequena,
  documentada no PR/entrega.
- A grade deve declarar `paginationMode="server"`, `sortingMode="server"` e
  `filterMode="server"`, com `rowCount`, estado controlado de paginação/ordenação/
  filtro e uma consulta inicial.
- Para Connections, o contrato é `GET /connections/grid` com
  `limit`, `offset`, `search`, `provider`, `sort_field`, `sort_direction` e
  `filters` (JSON com filtros combinados por AND).
- Colunas devem mostrar apenas dados seguros e resumidos. Nunca exibir secrets ou
  o conteúdo de `config_json`; detalhes sensíveis ficam apenas no formulário.
- Para colunas de data, use `type: "dateTime"` e retorne `Date` no `valueGetter`.
  Isso habilita o filtro de calendário do DataGrid; o servidor deve comparar a
  data, não a string formatada.
- Ações da linha são ícones pequenos com `Tooltip`. Não usar texto repetitivo nem
  o fluxo “Explorar dados” se ele não existir de fato no produto.
- O resultado do último teste deve ser persistido e exibido com status, horário e
  tooltip da mensagem.

### Filtros por contexto

- Clique direito em uma **célula** oferece `Mostrar iguais` e `Mostrar diferentes`.
  O valor enviado deve ser o valor persistido, não o texto renderizado. Exemplo:
  `Azure` → `azure`; `Azure Blob Storage` → `azure_blob`.
- Vários filtros são acumulados por coluna e enviados juntos com AND. Aplicar um
  novo filtro não pode apagar os anteriores.
- A coluna filtrada recebe o ícone `FilterAlt` no cabeçalho.
- Clique direito no **cabeçalho** expõe as opções padrão da coluna, além de
  `Limpar filtro da coluna` e `Limpar todos os filtros`. O evento deve ser ligado
  ao cabeçalho, não somente ao conteúdo da célula.
- Quando houver exportação, ela é uma ação contextual do **menu do cabeçalho**:
  `Exportar resultados exibidos` (ou nome equivalente que deixe o escopo claro),
  respeitando busca/filtros/ordenação ativos. Não exibir botão de exportação acima
  da grade, barra de ferramentas padrão ou exportação implícita no cabeçalho do painel.
- Não usar `GridToolbar`: ele introduz controles, cabeçalho e fluxo visual que não
  fazem parte deste padrão. Menus de coluna são a fonte das ações da grade.
- Ao integrar rótulos amigáveis ao filtro padrão da MUI, normalize-os no backend
  antes de consultar o campo persistido.

> A edição Community instalada de `@mui/x-data-grid` não oferece reordenação de
> colunas por arraste. Não adicionar props inexistentes. Se o requisito for
> obrigatório em outro módulo, usar MUI X Pro/Premium ou uma solução explicitamente
> aprovada antes de implementar.

## Formulários, diálogos e feedback

- A ação principal abre primeiro um diálogo amplo de escolha de fonte; só então
  abre o diálogo de credenciais do conector selecionado.
- Campos simples e selects devem ter 40px de altura. Preserve larguras e permita
  que campos multilinha (JSON/PEM) cresçam conforme o conteúdo.
- O botão `Testar conexão` do formulário testa a configuração preenchida **sem
  salvar** (`POST /etl/connections/test-config`) e mostra o resultado como `Alert`
  dentro do diálogo.
- O teste de uma conexão já existente (`POST /etl/connections/{id}/test`) persiste
  `last_test_status`, `last_test_message` e `last_tested_at`.
- Exclusão sempre usa diálogo Material de confirmação; nunca `window.confirm`.
  Ao concluir, mostrar `Snackbar` de sucesso e recarregar a grade.
- Estados de carregamento, vazio, erro e sucesso precisam ter feedback explícito.

## Referências de implementação

| Responsabilidade | Referência |
|---|---|
| Página e diálogos | `apps/web/src/pages/Connections/Connections.tsx` |
| Grade e menus contextuais | `apps/web/src/pages/Connections/ConnectionsDataGrid.tsx` |
| Contratos HTTP do frontend | `apps/web/src/services/api.ts` |
| Consulta server-side | `apps/api/app/routers/connections.py` |
| Teste de conexão/configuração | `apps/api/app/routers/etl.py` |

## Checklist para aplicar em outro módulo

1. Manter a estrutura faixa branca → abas de contexto/filtros → painel branco → DataGrid.
2. Garantir que ações e endpoints existam antes de colocá-los na interface.
3. Centralizar tipos e chamadas HTTP em `services/api.ts`; não usar axios direto.
4. Implementar a equivalência funcional do DataGrid de Connections: consulta no
   servidor, ordenação, filtro padrão, filtros de contexto, composição AND,
   indicadores no cabeçalho e limpeza por coluna/global.
5. Usar diálogos Material, `Alert` e `Snackbar` para feedback; nunca alertas do
   navegador.
6. Testar busca, filtros individuais/compostos, limpeza, estado vazio, operações de
   linha e responsividade antes de considerar o padrão adotado.

## Critério de aceite para uma migração de DataGrid

Uma página não está no padrão somente porque usa MUI DataGrid. Antes de entregar,
validar manualmente: ordenação de ao menos duas colunas; filtro de texto; filtro de
data em calendário; `Mostrar iguais` e `Mostrar diferentes`; dois ou mais filtros
simultâneos com resultado AND; indicador `FilterAlt`; limpeza por coluna e global
no clique direito do cabeçalho; paginação entre páginas; e exportação pelo menu de
contexto, se o módulo exigir exportação. Os controles de formulário ao redor da
grade devem ter 40px de altura (selects e campos simples), preservando a largura
necessária e deixando apenas campos multilinha crescerem verticalmente.
