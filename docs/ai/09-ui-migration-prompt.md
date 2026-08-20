# Prompt para migrar uma página ao padrão UI de Connections

Copie o texto abaixo para uma nova sessão de IA. Substitua apenas o endereço e o
nome da página.

```text
Quero ajustar a página [ROTA, por exemplo /reprocess?tab=data] ao padrão visual e
FUNCIONAL de /connections. Antes de editar, leia integralmente:

- docs/ai/08-ui-design-reference.md
- apps/web/src/pages/Connections/Connections.tsx
- apps/web/src/pages/Connections/ConnectionsDataGrid.tsx
- apps/web/src/services/api.ts
- o router/backend que fornece os dados desta página

Não faça uma interpretação apenas visual e não altere APIs, modelos ou fluxos de
domínio existentes sem necessidade. Preserve o worktree: não reverta mudanças de
outros arquivos.

Objetivo visual:
- faixa branca com título, descrição e abas de contexto quando forem úteis;
- área de trabalho #f8fafc e painel branco com borda discreta;
- controles compactos: campos e selects simples com 40px de altura; manter a
  largura atual e permitir crescimento vertical apenas em campos multilinha;
- não copiar ações específicas de Connections (por exemplo, Credenciais) nem criar
  botão global Exportar sem uma necessidade explícita desta página.

Se a página tiver uma coleção/histórico, ela DEVE reproduzir a capacidade do
ConnectionsDataGrid, não somente sua aparência:
- MUI DataGrid controlado e server-side: paginationMode, sortingMode e filterMode
  iguais a "server", rowCount, paginação, ordenação e filtros na API;
- não usar GridToolbar, filtro client-side com rows.filter, nem paginação somente
  em initialState;
- manter filtro padrão da grade, ordenação e filtro de datas em calendário
  (coluna type dateTime com valueGetter retornando Date);
- clique direito na célula: Mostrar iguais, Mostrar diferentes, Limpar filtro da
  coluna e Limpar todos os filtros;
- clique direito no CABEÇALHO: Opções da coluna, Limpar filtro da coluna e Limpar
  todos os filtros. Os filtros compostos são acumulados por AND; não podem se
  substituir. Mostrar FilterAlt no cabeçalho de cada coluna filtrada;
- se exportação for necessária, adicioná-la somente ao menu de clique direito do
  cabeçalho, como “Exportar resultados exibidos”, respeitando o recorte ativo.
  Não colocar botão Exportar acima/ao lado da tabela;
- não tentar reordenar colunas por arraste: a edição Community instalada não suporta
  isso.

Primeiro, apresente uma tabela curta de equivalência “comportamento de Connections
→ implementação nesta página”, incluindo endpoint/parâmetros server-side. Depois
implemente somente após essa verificação. Ao terminar, teste build/lint e valide:
ordenação, filtro de texto, data, Mostrar iguais/diferentes, dois filtros AND,
limpeza por coluna/global, paginação, vazio e exportação contextual (quando houver).
Informe com precisão os arquivos modificados e limitações reais.
```
