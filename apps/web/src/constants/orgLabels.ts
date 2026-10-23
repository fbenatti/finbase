/**
 * Rótulos de UI da hierarquia.
 * Backend/API mantém nomenclatura técnica (organograma, hierarquia_*, department_id).
 */
export const ORG = {
  modelTitle: "Modelo da hierarquia",
  modelSubtitle: "Estrutura de custos com níveis ilimitados e atributos próprios por nó.",
  hierarchySection: "Hierarquia",
  hierarchySectionDesc: "Diretorias, áreas e projetos que recebem ou repassam custos.",
  node: "Nó",
  nodeLower: "nó",
  allNodes: "Toda a hierarquia",
  selectNode: "Selecione um nó",
  selectNodeFilterHint: "Escolha um nó da hierarquia. Todas as abas passam a respeitar esse filtro.",
  newNode: "Novo nó",
  editNode: "Editar nó",
  parentNode: "Nó pai",
  parentRoot: "Raiz (nível mais alto)",
  searchPlaceholder: "Buscar por nome, centro de custo, ambiente ou cloud",
  emptyNone: "Nenhum nó cadastrado. Use \"Novo nó\" ou \"Importar CSV\" para começar.",
  emptySearch: "Nenhum nó encontrado para essa busca.",
  saved: "Nó salvo.",
  savedWithShared: "Nó e regras de rateio salvos.",
  deleted: "Nó excluído.",
  deleteConfirm: "Excluir nó?",
  deleteAction: "Excluir nó",
  financialTooltip: "Cadastro financeiro deste nó",
  configureModel: "Configurar hierarquia",
  directorViewChain: "Segmento → Modelo → Diretoria → Cloud → Projeto → SubProjeto",
  directorViewLevels: "1→2→4→3→5→6",
} as const;
