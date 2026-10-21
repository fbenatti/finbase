# Padrão visual de tabelas e planejamento

## Fonte do padrão

`apps/web/src/theme/theme.ts` concentra os tokens e estilos de controles, tabelas e DataGrid.
`Layout.tsx` aplica o tema escolhido também ao conteúdo dos módulos e aos diálogos.

| Elemento | Finbase | Carbon |
| --- | --- | --- |
| Fonte | Inter | IBM Plex Sans |
| Ação principal | #1B5878 | #0F62FE |
| Fundo | #F4F7FA | #F4F4F4 |
| Texto | #1D2D3F | #161616 |
| Bordas | #E4EAF0 | #E0E0E0 |
| Controles | 40 px | 40 px |
| Ícones de ação | 20 px | 20 px |
| Linhas e cabeçalho da grade | 48 px | 48 px |

Os ícones de ordenação usam 16 px. A hierarquia usa controles de expansão de 32 px.
Tamanhos explícitos de componentes especializados continuam permitidos quando necessários.

## Telas atendidas

- Orçamentos e Projeções manuais compartilham `Planning.tsx`: cabeçalho único,
  filtros, busca, ações de importação/exportação/publicação, grade com rolagem,
  paginação e virtualização, legenda única e histórico de versões.
- A estrutura de Planejamento não repete os cabeçalhos dos componentes filhos.
  Acurácia do orçamento, Histórico do orçamento e História dos contratos usam
  o espaçamento do contêiner principal.
- Configuração, regras, histórico e acurácia de projeções usam o mesmo tema e
  títulos correspondentes às suas rotas.
- Grades de conexões, classificação, reprocessamento, extração, auditoria e
  histórico operacional herdam o padrão central; estilos locais que removiam
  o foco de teclado foram retirados.
- Tabelas MUI recebem fontes, cores de cabeçalho e números tabulares pelo tema.

## Novos módulos

Use os controles e tabelas do tema, sem sobrescrever cores, fontes e altura com
valores fixos. Os ícones da grade estão em `components/DataGridIcons.tsx` e são
aplicados automaticamente. Preserve o indicador de foco, os nomes acessíveis
nas ações por ícone e o alinhamento à direita de valores monetários.

## Skill de apoio

Foi instalada `frontend-design`, de `anthropics/skills`, em
`~/.codex/skills/frontend-design`. A orientação foi aplicada à hierarquia visual,
à tipografia, à redução de elementos repetidos e à revisão visual no navegador.

## Área de Fontes de dados

`pages/Connections` segue o mesmo padrão, incluindo os diálogos internos:

- Título do módulo: **Fontes de dados**; ação principal: **Nova conexão**.
- Filtros na ordem categoria, provedor ou aplicação e busca; início sem filtros.
- Ações da lista: **Atualizar lista** e **Exportar página**, que exporta somente
  os registros da página atual. Nenhuma credencial é incluída no arquivo.
- Colunas padrão: conexão, provedor, tipo de conexão, validação e ações.
  Código, origem e atualização podem ser exibidos pelo menu de colunas.
- Cada linha oferece edição e um menu com validação, histórico e exclusão.
- Formulários usam identificação antes das credenciais, campos em português,
  duas colunas quando houver espaço e rodapé cancelar/validar/salvar.
- Fontes Azure, serviços auxiliares e importação CSV usam os mesmos ícones
  Carbon por meio de `ConnectionIcons.tsx`, com cores e dimensões do tema.

## Estrutura compartilhada das grades

As 24 implementações de DataGrid importam `components/FinbaseDataGrid.tsx`.
A importação de `DataGrid` do pacote MUI fica restrita a esse componente; tipos,
seletores e hooks continuam disponíveis diretamente pelo pacote.

A estrutura inclui controles de colunas, filtros, densidade, exportação CSV,
contagem de registros, cabeçalho de 48 px, linhas de 48 px na densidade padrão,
foco de teclado visível e ícones Carbon. Cada módulo preserva os callbacks de
consulta, paginação, filtros e edição, além dos destaques semânticos das linhas.
Em grades com paginação no servidor, a opção CSV informa que exporta a página
atual. A opção de impressão automática está oculta. `SortableTable.tsx` utiliza
a mesma organização de busca, contagem, cabeçalho e tipografia.

## Busca no workspace

A navegação dos dois layouts tem um campo **Buscar menus**, com o atalho
**Ctrl/Cmd + K**. A busca ignora acentos e maiúsculas, combina nomes de grupos e
submenus, expande os grupos encontrados e mantém as rotas individuais.
**Escape** ou o botão de limpar restaura a navegação completa; **Enter** abre o
primeiro submenu encontrado. Novos módulos entram automaticamente pelo catálogo.

A busca usa somente módulos visíveis, habilitados e permitidos pelo backend,
incluindo a verificação dos ancestrais. Ela não muda permissões e não exibe
módulos ocultos ou indisponíveis para o cliente.
