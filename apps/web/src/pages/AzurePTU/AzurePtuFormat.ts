/** Formatadores compartilhados das páginas Azure PTU. */

const fmtCompact = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Formata volumes grandes em unidades compactas: 1 Mi, 1 Bi, 1 Tr (mín. 2 casas). */
export const tokensLabel = (v: any) => {
  const value = Number(v || 0);
  if (!isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${fmtCompact.format(value / 1e12)} Tr`;
  if (abs >= 1e9) return `${fmtCompact.format(value / 1e9)} Bi`;
  if (abs >= 1e6) return `${fmtCompact.format(value / 1e6)} Mi`;
  return fmtCompact.format(value);
};
