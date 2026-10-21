/**
 * Tokens de identidade visual do finbase.cloud (fonte única).
 *
 * Use estes valores em vez de cores fixas nos componentes. Para trocar a
 * identidade, altere apenas este arquivo: o tema MUI, os gráficos Plotly e o
 * shell de navegação leem daqui.
 *
 * Cores de status (erro, alerta, sucesso) ficam separadas dos tons da marca,
 * para que um gráfico ou um badge continue legível mesmo com outra paleta.
 */

export const colors = {
  brand: "#4338CA",
  brandLight: "#6366F1",
  brandDark: "#312E81",
  brandTint: "#EEF2FF",
  brandSelected: "#E7E9FB",
  brandHover: "#DCDDF8",

  accent: "#EA580C",
  accentDark: "#C2410C",

  canvas: "#F5F6FA",
  paper: "#FFFFFF",
  raised: "#FAFBFD",
  tableHead: "#EEF0F7",
  rowHover: "#F3F4FB",
  border: "#E2E5EE",
  borderStrong: "#CDD2DF",

  text: "#161B2E",
  textSecondary: "#5A6378",
  textMuted: "#8A93A8",

  rail: "#15123A",
  railActive: "#2B2770",
  railText: "#B9B6E8",

  success: "#15803D",
  warning: "#B45309",
  error: "#B91C1C",
  info: "#1D4ED8",
} as const;

/** Série de categorias para gráficos, na ordem de uso. */
export const chartSeries = [
  "#4338CA", // índigo (marca)
  "#0E7490", // ciano escuro
  "#EA580C", // laranja (destaque)
  "#BE185D", // rosa escuro
  "#4D7C0F", // verde oliva
  "#475569", // ardósia
  "#D97706", // âmbar
  "#0369A1", // azul petróleo
] as const;

/** Cores semânticas usadas em gráficos (realizado, orçamento, forecast, erro). */
export const chartSemantic = {
  actual: colors.brand,
  budget: "#7C3AED",
  forecast: "#0E7490",
  negative: colors.error,
  positive: colors.success,
} as const;

export const fontFamily = {
  body: '"Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
  heading: '"Manrope", "Inter", "Segoe UI", Arial, sans-serif',
  mono: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
} as const;

export const radius = { control: 8, surface: 12, pill: 999 } as const;
