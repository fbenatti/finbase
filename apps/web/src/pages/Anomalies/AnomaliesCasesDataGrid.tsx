import { DataGrid, type GridColDef } from "@mui/x-data-grid";
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
    { field: "state", headerName: "Estado", width: 120 },
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
      valueGetter: (_, row) => row.cloud || row.series_dimensions?.cloud_origem || "",
    },
    { field: "subaccount_name", headerName: "Subaccount", flex: 1, minWidth: 140 },
    {
      field: "anomalous_cost_cloud",
      headerName: "Custo anômalo",
      width: 130,
      valueFormatter: (v) => money(Number(v), currency),
    },
    {
      field: "projected_month_impact",
      headerName: "Proj. mês",
      width: 120,
      valueFormatter: (v) => money(v == null ? null : Number(v), currency),
    },
    { field: "event_date", headerName: "Data", width: 110 },
  ];

  return (
    <DataGrid
      autoHeight
      rows={cases}
      columns={columns}
      disableRowSelectionOnClick
      onRowClick={(params) => onOpen(Number(params.id))}
      pageSizeOptions={[25, 50]}
      initialState={{ pagination: { paginationModel: { pageSize: 25 } } }}
      sx={{ border: 0, "& .MuiDataGrid-row": { cursor: "pointer" } }}
    />
  );
}
