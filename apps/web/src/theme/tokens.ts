/**
 * Tokens de identidade visual do finbase.cloud (fonte única).
 *
 * Use estes valores em vez de cores fixas nos componentes. Para trocar a
 * identidade, altere apenas este arquivo: o tema MUI, os gráficos Plotly e o
 * shell de navegação leem daqui.
 *
 * Paleta "Azul logo": derivada do logotipo. O azul elétrico do símbolo é a
 * primária, o azul-marinho do nome (#162B3A) vira o trilho lateral e o laranja
 * aparece só como destaque pontual. Cores de status (erro, alerta, sucesso)
 * ficam separadas dos tons da marca, para que um gráfico ou um badge continue
 * legível mesmo com outra paleta.
 */

export const colors = {
  brand: "#0F62FE",
  brandLight: "#6EA8FE",
  brandDark: "#0B3FA8",
  brandTint: "#EEF4FF",
  brandSelected: "#DCE8FF",
  brandHover: "#CFDFFF",

  accent: "#EA580C",
  accentDark: "#C2410C",

  canvas: "#F5F8FB",
  paper: "#FFFFFF",
  raised: "#FAFBFD",
  tableHead: "#EEF3F8",
  rowHover: "#F3F7FC",
  border: "#E1E8F0",
  borderStrong: "#C5D2DE",

  text: "#162B3A",
  textSecondary: "#526777",
  textMuted: "#8497A8",

  rail: "#FFFFFF",
  railActive: "#E6EFFF",
  railText: "#526777",

  success: "#15803D",
  warning: "#B45309",
  error: "#B91C1C",
  info: "#0EA5C6",
} as const;

/** Série de categorias para gráficos, na ordem de uso. */
export const chartSeries = [
  "#0F62FE", // azul do logo (marca)
  "#EA580C", // laranja (destaque)
  "#6EA8FE", // azul claro
  "#0B3FA8", // azul escuro
  "#D97706", // âmbar
  "#64748B", // ardósia
  "#BE185D", // rosa escuro
  "#4D7C0F", // verde oliva
] as const;

/** Cores semânticas usadas em gráficos (realizado, orçamento, forecast, erro). */
export const chartSemantic = {
  actual: colors.brand,
  budget: "#64748B",
  forecast: "#0EA5C6",
  negative: colors.error,
  positive: colors.success,
} as const;

export const fontFamily = {
  body: '"Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
  heading: '"Manrope", "Inter", "Segoe UI", Arial, sans-serif',
  mono: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
} as const;

export const radius = { control: 8, surface: 12, pill: 999 } as const;
