# Padrão visual de tabelas e planejamento

## Fonte do padrão

Os tokens de cor, fonte e raio ficam em `apps/web/src/theme/tokens.ts`. O tema MUI (`theme/theme.ts`),
os gráficos (`theme/chartTheme.ts`) e o shell (`layout/Layout.tsx`) leem desse arquivo. Não use cores
fixas nos componentes: importe `colors` de `theme/tokens.ts`.

| Elemento | Valor atual | Token |
| --- | --- | --- |
| Fonte do texto | Inter | `fontFamily.body` |
| Fonte de títulos | Manrope | `fontFamily.heading` |
| Ação principal | #0F62FE | `colors.brand` |
| Fundo | #F4F8F7 | `colors.canvas` |
| Texto | #162B3A | `colors.text` |
| Bordas | #DCE7E5 | `colors.border` |
| Controles | 40 px | `radius.control` = 8 px |
| Ícones de ação | 20 px | Material Icons |
| Linhas e cabeçalho da grade | 48 px | 48 px |

Os ícones de ordenação usam 16 px. A hierarquia usa controles de expansão de 32 px.
Tamanhos explícitos de componentes especializados continuam permitidos quando necessários.

## Telas atendidas

- Orçamentos e Projeções manuais compartilham `Planning.tsx`: cabeçalho único,
  filtros, busca, ações de importação/exportação/publicação, grade com rolagem,
  paginação e virtualização, legenda única e histórico de versões.
- A estrutura de Planejamento não repete os cabeçalhos dos componentes filhos.
  Aderência ao orçamento e Histórico do orçamento usam
  o espaçamento do contêiner principal.
- Configuração, regras, histórico e precisão de projeções usam o mesmo tema e
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
  Material por meio de `ConnectionIcons.tsx`, com cores e dimensões do tema.

## Estrutura compartilhada das grades

As 24 implementações de DataGrid importam `components/FinbaseDataGrid.tsx`.
A importação de `DataGrid` do pacote MUI fica restrita a esse componente; tipos,
seletores e hooks continuam disponíveis diretamente pelo pacote.

A estrutura inclui controles de colunas, filtros, densidade, exportação CSV,
contagem de registros, cabeçalho de 48 px, linhas de 48 px na densidade padrão,
foco de teclado visível e ícones Material. Cada módulo preserva os callbacks de
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

## Diálogo "Editar valores mensais" (Orçamentos e Projeções)

Componente: `pages/Planning/MonthlyValuesDialog.tsx`, aberto pelo ícone de editar de uma linha em `Planning.tsx`.
Edita somente os valores próprios da linha; os totais dos níveis superiores continuam sendo recalculados pela grade.

- Cabeçalho com o nome da linha e o centro de custo. O resumo mostra **Total original**, **Total editado** e **Diferença** (R$ e %).
- Ações em lote acima da tabela: **Distribuir igualmente** (divide o total anual em 12 meses, com a diferença de centavos nos últimos meses), **Aplicar %** (ajusta cada mês pelo percentual informado, que pode ser negativo) e **Zerar**. **Restaurar atuais** desfaz a edição.
- Tabela por mês com colunas Mês, Valor atual, Novo valor, Variação (R$) e Variação (%). Meses agrupados por trimestre, com subtotal por trimestre e total do ano.
- Valores em BRL com 2 casas, alinhados à direita. A entrada aceita `1.234,56` e `1234.56`; o campo é formatado ao sair.
- Meses alterados ficam com fundo `colors.brandTint`. Aumento aparece em `colors.warning` e redução em `colors.success`.
- Valores negativos ou inválidos mostram alerta e campo em erro. Nesses casos o botão **Aplicar** fica desabilitado.
- **Aplicar** também fica desabilitado enquanto nada tiver mudado. O rodapé mostra quantos meses foram alterados.
