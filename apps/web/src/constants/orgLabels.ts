/**
 * Rótulos de UI do modelo organizacional.
 * Backend/API mantém nomenclatura técnica (organograma, organograma_nivel_*, department_id).
 */
export const ORG = {
  modelTitle: "Modelo Organizacional",
  modelSubtitle: "Organograma hierárquico com até 7 níveis e campos customizados.",
  hierarchySection: "Organograma hierárquico",
  hierarchySectionDesc: "Diretorias, áreas e unidades com custo compartilhado.",
  node: "Organograma",
  nodeLower: "organograma",
  allNodes: "Todo o organograma",
  selectNode: "Selecione no organograma",
  selectNodeFilterHint: "Selecione um nó do organograma — todas as abas respeitam este filtro.",
  newNode: "Novo organograma",
  editNode: "Editar organograma",
  parentNode: "Nó pai do organograma",
  parentRoot: "Raiz (sem nó pai)",
  searchPlaceholder: "Buscar no organograma (nome, centro de custo, ambiente ou cloud)",
  emptyNone: "Nenhum nó cadastrado no organograma. Clique em 'Novo organograma' ou 'Importar CSV' para começar.",
  emptySearch: "Nenhum nó corresponde à busca.",
  saved: "Organograma salvo.",
  savedWithShared: "Organograma e custo compartilhado salvos.",
  deleted: "Organograma excluído.",
  deleteConfirm: "Excluir organograma?",
  deleteAction: "Excluir organograma",
  financialTooltip: "Cadastro financeiro deste nó",
  configureModel: "Configurar modelo organizacional",
  /** Nível 5 da hierarquia de custo (coluna organograma_nivel_5) */
  level5: "Área",
  directorViewChain: "Segmento → Modelo → Diretoria → Cloud → Projeto → SubProjeto",
  directorViewLevels: "1→2→4→3→5→6",
} as const;
