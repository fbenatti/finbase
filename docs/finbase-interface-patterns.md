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
