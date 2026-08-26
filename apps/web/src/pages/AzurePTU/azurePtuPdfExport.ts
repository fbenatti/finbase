/** Gera nome sugerido para salvar o PDF (Chrome usa document.title como padrão). */
export function azurePtuPdfFilename(periodId: string): string {
  const date = new Date().toISOString().slice(0, 10);
  return `visoes-consumo-ia-${periodId}-${date}`;
}

export type AzurePtuPrintMeta = {
  periodId: string;
  periodLabel: string;
  projectLabel: string;
  grainLabel: string;
  tabLabel: string;
  dataSourceLabel: string;
};

/** Abre o diálogo de impressão; o usuário escolhe "Salvar como PDF". */
export function exportAzurePtuPdf(meta: AzurePtuPrintMeta): void {
  const previousTitle = document.title;
  document.title = azurePtuPdfFilename(meta.periodId);
  document.body.classList.add("azure-ptu-print-mode");

  const cleanup = () => {
    document.body.classList.remove("azure-ptu-print-mode");
    document.title = previousTitle;
    window.removeEventListener("afterprint", cleanup);
  };

  window.addEventListener("afterprint", cleanup);

  // Pequeno atraso para Plotly/MUI aplicarem layout antes da captura de impressão.
  window.setTimeout(() => window.print(), 150);
}
