import type { ReactNode } from "react";
import { AutoAwesome, Cloud, Dataset, MenuBook, Psychology, Storage } from "@mui/icons-material";

// Catálogo de fontes em dois níveis: o usuário escolhe o provedor e depois o
// tipo de dado. Cada par vira o connector_type que o backend já conhece.

export interface SourceDataKind {
  connectorType: string;
  label: string;
  description: string;
}

export interface SourceProvider {
  key: string;
  label: string;
  description: string;
  icon: ReactNode;
  kinds: SourceDataKind[];
}

export const SOURCE_PROVIDERS: SourceProvider[] = [
  {
    key: "azure", label: "Microsoft Azure", description: "Custos, contratos, preços e inventário", icon: <Cloud />,
    kinds: [
      { connectorType: "azure_blob", label: "Custos e faturas", description: "Relatórios de custo exportados em arquivo" },
      { connectorType: "azure_mca_invoice", label: "Faturas e contratos", description: "Valores mensais e compromissos firmados (MCA)" },
      { connectorType: "azure_pricesheet", label: "Preços contratados", description: "Tabela de preços negociados" },
      { connectorType: "azure_openai_ptu", label: "Capacidade de IA reservada", description: "Azure OpenAI PTU" },
      { connectorType: "azure_resource_graph", label: "Inventário de recursos", description: "Recursos existentes e ociosos" },
    ],
  },
  {
    key: "gcp", label: "Google Cloud", description: "Custos via BigQuery", icon: <Storage />,
    kinds: [{ connectorType: "gcp_bigquery", label: "Custos e faturas", description: "Exportação de billing no BigQuery" }],
  },
  {
    key: "oci", label: "Oracle Cloud", description: "Custos da OCI", icon: <Cloud />,
    kinds: [
      { connectorType: "oci", label: "Custos e faturas", description: "Relatórios de custo da Oracle Cloud" },
      { connectorType: "oci_monitoring", label: "Métricas de uso", description: "CPU, memória, disco e rede pelo OCI Monitoring" },
    ],
  },
  {
    key: "atlas", label: "MongoDB Atlas", description: "Oferta no marketplace", icon: <Dataset />,
    kinds: [{ connectorType: "mongodb_atlas", label: "Custos e faturas", description: "Faturas do Atlas" }],
  },
  {
    key: "databricks", label: "Databricks", description: "Consumo do workspace", icon: <Storage />,
    kinds: [{ connectorType: "databricks", label: "Uso e consumo", description: "Consumo do workspace" }],
  },
  {
    key: "github", label: "GitHub", description: "Copilot e Actions", icon: <Cloud />,
    kinds: [
      { connectorType: "github_billing", label: "Custos e faturas", description: "Relatórios de custo detalhados" },
      { connectorType: "github_token", label: "Uso e tokens", description: "Uso mensal (Copilot / Actions)" },
      { connectorType: "github_license", label: "Licenças", description: "Usuários e licenças ativas" },
    ],
  },
  {
    key: "claude", label: "Claude", description: "Claude.ai e API Anthropic", icon: <Psychology />,
    kinds: [
      { connectorType: "claude_token", label: "Uso e tokens", description: "Tokens e atividades por mês" },
      { connectorType: "claude_license", label: "Licenças", description: "Colaboradores com licença em uso" },
    ],
  },
  {
    key: "gemini", label: "Gemini", description: "IA do Google", icon: <AutoAwesome />,
    kinds: [
      { connectorType: "gemini_token", label: "Uso e tokens", description: "Consumo de tokens por mês" },
      { connectorType: "gemini_license", label: "Licenças", description: "Usuários com licença ativa" },
    ],
  },
  {
    key: "notebooklm", label: "NotebookLM", description: "IA do Google", icon: <MenuBook />,
    kinds: [
      { connectorType: "notebooklm_token", label: "Uso e tokens", description: "Consumo de tokens por mês" },
      { connectorType: "notebooklm_license", label: "Licenças", description: "Colaboradores com licença em uso" },
    ],
  },
];

// Fontes antigas que não são criadas pelo seletor, mas ainda podem ser editadas.
const LEGACY_PROVIDERS: SourceProvider[] = [
  {
    key: "file", label: "Arquivo", description: "Carga manual", icon: <Storage />,
    kinds: [{ connectorType: "manual_csv", label: "Carga manual de CSV", description: "Arquivo enviado pelo usuário" }],
  },
];

export function providerOf(connectorType: string): SourceProvider | undefined {
  return [...SOURCE_PROVIDERS, ...LEGACY_PROVIDERS].find((provider) => provider.kinds.some((kind) => kind.connectorType === connectorType));
}

export function kindOf(connectorType: string): SourceDataKind | undefined {
  return providerOf(connectorType)?.kinds.find((kind) => kind.connectorType === connectorType);
}

/** Tipos de dado do provedor que estão habilitados em Conectores. */
export function visibleKinds(provider: SourceProvider, hidden: Set<string>): SourceDataKind[] {
  return provider.kinds.filter((kind) => !hidden.has(kind.connectorType));
}

// Lista plana usada pela tela de Conectores (habilitar/desabilitar tipos).
export const CONNECTOR_TYPES = SOURCE_PROVIDERS.flatMap((provider) =>
  provider.kinds.map((kind) => ({ value: kind.connectorType, label: `${provider.label}: ${kind.label}`, scope: provider.key })),
);
