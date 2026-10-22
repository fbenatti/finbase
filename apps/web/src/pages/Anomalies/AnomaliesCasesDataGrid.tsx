import DataGrid from "../../components/FinbaseDataGrid";
import { type GridColDef } from "@mui/x-data-grid";
import { Chip } from "@mui/material";
import type { AnomalyCase } from "../../services/anomaliesApi";

const money = (value: number | null | undefined, currency = "BRL") =>
  value == null ? "—" : value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 0 });

const SEVERITY_COLOR: Record<string, "default" | "info" | "warning" | "error"> = {
  LOW: "default",
  MEDIUM: "info",
  HIGH: "warning",
  CRITICAL: "error",
};

const CLOUD_LABEL: Record<string, string> = {
  azure: "Azure",
  gcp: "GCP",
  ibm: "IBM Cloud",
  aws: "AWS",
  oci: "Oracle Cloud",
  mongodb: "MongoDB Atlas",
  atlas: "MongoDB Atlas",
};

const STATE_LABEL: Record<string, string> = {
  NEW: "Novo",
  ASSIGNED: "Atribuído",
  INVESTIGATING: "Em apuração",
  DECIDED: "Decidido",
  IN_REMEDIATION: "Em correção",
  VERIFYING: "Em conferência",
  RETROSPECTIVE: "Retrospectiva",
  CLOSED: "Encerrado",
};

type Props = {
  cases: AnomalyCase[];
  currency: string;
  onOpen: (id: number) => void;
};

export default function AnomaliesCasesDataGrid({ cases, currency, onOpen }: Props) {
  const anomalyName = (row: AnomalyCase) => {
    const dimensions = row.series_dimensions ?? {};
    const keys = [
      "skuname", "sku_name", "sku", "skumeter", "metername", "meter_name",
      "servicesubcategory", "service_category", "servicename", "service_name",
    ];
    for (const key of keys) {
      const value = dimensions[key]?.trim();
      if (value) return value;
    }
    for (const key of keys) {
      const prefix = `${key}=`;
      const value = row.event_family?.split("|").find((part) => part.trim().toLowerCase().startsWith(prefix));
      if (value) return value.slice(value.indexOf("=") + 1).trim();
    }
    return "—";
  };

  const columns: GridColDef[] = [
    {
      field: "severity",
      headerName: "Severidade",
      width: 110,
      renderCell: ({ value }) => {
        const severity = String(value);
        return <Chip size="small" color={SEVERITY_COLOR[severity.toUpperCase()] ?? "default"} label={severity} />;
      },
    },
    {
      field: "state",
      headerName: "Estado",
      width: 150,
      valueFormatter: (value) => STATE_LABEL[String(value)] ?? String(value ?? "—"),
    },
    {
      field: "event_family",
      headerName: "Anomalia",
      flex: 1.2,
      minWidth: 180,
      valueGetter: (_, row) => anomalyName(row),
    },
    {
      field: "cloud",
      headerName: "Nuvem",
      width: 110,
      valueGetter: (_, row) => {
        const provider = String(row.cloud || row.series_dimensions?.cloud_origem || "");
        return CLOUD_LABEL[provider.toLowerCase()] ?? provider;
      },
    },
    { field: "subaccount_name", headerName: "Subconta", flex: 1, minWidth: 140 },
    {
      field: "anomalous_cost_cloud",
      headerName: "Gasto anômalo",
      width: 130,
      valueFormatter: (v) => money(Number(v), currency),
    },
    {
      field: "projected_month_impact",
      headerName: "Projeção do mês",
      width: 120,
      valueFormatter: (v) => money(v == null ? null : Number(v), currency),
    },
    { field: "event_date", headerName: "Data do evento", width: 110 },
  ];

  return (
    <DataGrid
      autoHeight
      rows={cases}
      columns={columns}
      disableRowSelectionOnClick
      onRowClick={(params) => onOpen(Number(params.id))}
      pageSizeOptions={[50, 100, 500, 1000]}
      initialState={{ pagination: { paginationModel: { pageSize: 50 } } }}
      sx={{ border: 0, "& .MuiDataGrid-row": { cursor: "pointer" } }}
    />
  );
}
