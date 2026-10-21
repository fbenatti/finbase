/** Gera nome sugerido para salvar o PDF (Chrome usa document.title como padrão). */
export function azurePtuPdfFilename(periodId: string): string {
  const date = new Date().toISOString().slice(0, 10);
  return `visoes-consumo-ia-${periodId}-${date}`;
}

/** Abre o dashboard visível no diálogo de impressão para salvá-lo como PDF. */
export function exportDashboardPdf(dashboardId: string): void {
  const previousTitle = document.title;
  const date = new Date().toISOString().slice(0, 10);
  document.title = `visoes-consumo-ia-${dashboardId}-${date}`;
  document.body.classList.add("finops-print-mode");

  const cleanup = () => {
    document.body.classList.remove("finops-print-mode");
    document.title = previousTitle;
    window.removeEventListener("afterprint", cleanup);
  };

  window.addEventListener("afterprint", cleanup);
  window.setTimeout(() => window.print(), 300);
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
  document.body.classList.add("finops-print-mode");

  const cleanup = () => {
    document.body.classList.remove("finops-print-mode");
    document.title = previousTitle;
    window.removeEventListener("afterprint", cleanup);
  };

  window.addEventListener("afterprint", cleanup);

  // Pequeno atraso para Plotly/MUI aplicarem layout antes da captura de impressão.
  window.setTimeout(() => window.print(), 150);
}
