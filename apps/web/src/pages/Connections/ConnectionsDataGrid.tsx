import { useMemo, useState } from "react";
import {
  Box,
  Chip,
  Divider,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Cable,
  Delete,
  Edit,
  FilterAlt,
} from "@mui/icons-material";
import { DataGrid, GridColDef, GridFilterModel, GridPaginationModel, GridSortModel, useGridApiRef } from "@mui/x-data-grid";
import { SourceConnection } from "../../services/api";

export type ConnectionTestMessage = {
  severity: "success" | "warning" | "error";
  msg: string;
};

type ConnectionGridFilter = { field: string; operator: "equals" | "notEquals"; value: string };

type Props = {
  connections: SourceConnection[];
  total: number;
  loading: boolean;
  testingId: number | null;
  testMessages: Record<number, ConnectionTestMessage>;
  onEdit: (connection: SourceConnection) => void;
  onTest: (connection: SourceConnection) => void;
  onDelete: (connection: SourceConnection) => void;
  onQuery: (query: { offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: ConnectionGridFilter[] }) => void;
};

const connectorLabels: Record<string, string> = {
  azure_blob: "Azure Blob Storage",
  gcp_bigquery: "GCP BigQuery",
  oci: "Oracle Cloud (OCI)",
  mongodb_atlas: "MongoDB Atlas",
  manual_csv: "Upload manual de CSV",
  github_billing: "GitHub Billing (Copilot)",
  github_license: "GitHub Licenças (Copilot)",
  github_token: "GitHub Tokens (Copilot)",
  notebooklm_license: "NotebookLM Licenças",
  notebooklm_token: "NotebookLM Tokens",
  claude_license: "Claude Licenças",
  claude_token: "Claude Tokens",
  gemini_license: "Gemini Licenças",
  gemini_token: "Gemini Tokens",
  gemini_notebooklm: "Gemini NotebookLM",
};

const providerLabels: Record<string, string> = {
  azure: "Azure",
  gcp: "GCP",
  ibm: "IBM Cloud",
  aws: "AWS",
  oci: "Oracle Cloud (OCI)",
  atlas: "MongoDB Atlas",
  mongodb: "MongoDB Atlas",
  github: "GitHub",
  notebooklm: "NotebookLM",
  gemini: "Gemini",
  claude: "Claude",
};

export function getProviderLabel(scope: string | null): string {
  if (!scope) return "Não informado";
  return providerLabels[scope] || scope;
}

function getConnectionSummary(connection: SourceConnection): string {
  const config = connection.config_json as Record<string, string>;
  switch (connection.connector_type) {
    case "azure_blob": {
      const location = [config.storage_account, config.container_name].filter(Boolean).join(" / ");
      return location || "Storage e container não informados";
    }
    case "gcp_bigquery": {
      const dataset = [config.project_id, config.dataset_id].filter(Boolean).join(" / ");
      return dataset || config.table_name || "Projeto e dataset não informados";
    }
    case "oci":
      return [config.region, config.tenancy_ocid || config.tenancy].filter(Boolean).join(" · ") || "Região não informada";
    case "mongodb_atlas":
      return [config.group_id, config.contract_type].filter(Boolean).join(" · ") || "Grupo e contrato não informados";
    case "manual_csv":
      return "Arquivo CSV enviado manualmente";
    case "github_billing":
      return [config.enterprise_slug, "Billing"].filter(Boolean).join(" · ") || "Enterprise não informada";
    case "github_license":
      return [config.enterprise_slug, "Licenças (Copilot)"].filter(Boolean).join(" · ") || "Enterprise não informada";
    case "github_token":
      return [config.enterprise_slug, "Tokens (Copilot)"].filter(Boolean).join(" · ") || "Enterprise não informada";
    case "notebooklm_license":
    case "notebooklm_token":
    case "gemini_license":
    case "gemini_token":
    case "gemini_notebooklm":
      return [config.project_id, config.project_number].filter(Boolean).join(" · ") || "Project ID não informado";
    case "claude_license":
    case "claude_token":
      return [config.org_id, config.org_name].filter(Boolean).join(" · ") || "Org não informada";
    default:
      return connection.cloud_name || "Configuração disponível ao editar";
  }
}

function formatUpdatedAt(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

export default function ConnectionsDataGrid({
  connections,
  total,
  loading,
  testingId,
  testMessages,
  onEdit,
  onTest,
  onDelete,
  onQuery,
}: Props) {
  const apiRef = useGridApiRef();
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 25 });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, ConnectionGridFilter>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>{label}</Typography>
      {activeFilters[field] && <Tooltip title="Esta coluna possui um filtro ativo"><FilterAlt fontSize="small" color="primary" /></Tooltip>}
    </Stack>
  );
  const columns = useMemo<GridColDef[]>(() => [
    {
      field: "name",
      headerName: "Conexão",
      renderHeader: () => filteredHeader("Conexão", "name"),
      minWidth: 230,
      flex: 1.15,
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          <Typography variant="body2" fontWeight={700} noWrap>{row.name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>{row.cloud_name || "Sem nome de conta"}</Typography>
        </Stack>
      ),
    },
    { field: "code", headerName: "Código", renderHeader: () => filteredHeader("Código", "code"), minWidth: 145, flex: 0.7 },
    {
      field: "provider",
      headerName: "Provedor",
      renderHeader: () => filteredHeader("Provedor", "provider"),
      minWidth: 145,
      flex: 0.7,
      valueGetter: (_, row: SourceConnection) => getProviderLabel(row.cloud_scope),
      renderCell: ({ row }: { row: SourceConnection }) => <Chip label={getProviderLabel(row.cloud_scope)} size="small" variant="outlined" />,
    },
    {
      field: "connector_type",
      headerName: "Tipo",
      renderHeader: () => filteredHeader("Tipo", "connector_type"),
      minWidth: 185,
      flex: 0.9,
      valueGetter: (_, row: SourceConnection) => connectorLabels[row.connector_type] || row.connector_type,
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
          <Typography variant="body2" noWrap>{connectorLabels[row.connector_type] || row.connector_type}</Typography>
          {row.config_json?.marketplace === true && <Chip label="Marketplace" size="small" color="secondary" />}
        </Stack>
      ),
    },
    {
      field: "origin",
      headerName: "Origem",
      filterable: false,
      minWidth: 245,
      flex: 1.15,
      valueGetter: (_, row: SourceConnection) => getConnectionSummary(row),
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Tooltip title={getConnectionSummary(row)}>
          <Typography variant="body2" noWrap>{getConnectionSummary(row)}</Typography>
        </Tooltip>
      ),
    },
    {
      field: "updated_at",
      headerName: "Atualizado em",
      renderHeader: () => filteredHeader("Atualizado em", "updated_at"),
      minWidth: 165,
      flex: 0.75,
      type: "dateTime",
      valueGetter: (_, row: SourceConnection) => row.updated_at ? new Date(row.updated_at) : null,
      valueFormatter: (value) => formatUpdatedAt(value),
    },
    {
      field: "test_status",
      headerName: "Último teste",
      minWidth: 135,
      sortable: false,
      filterable: false,
      renderCell: ({ row }: { row: SourceConnection }) => {
        const persistedMessage: ConnectionTestMessage | undefined = row.last_test_status ? {
          severity: row.last_test_status === "OK" ? "success" : row.last_test_status === "PENDING" ? "warning" : "error",
          msg: row.last_test_message || "Teste concluído sem mensagem.",
        } : undefined;
        const message = testMessages[row.id] || persistedMessage;
        if (testingId === row.id) return <Chip label="Testando…" size="small" color="info" />;
        if (!message) return <Typography color="text.secondary">—</Typography>;
        const label = message.severity === "success" ? "Conectado" : message.severity === "warning" ? "Pendente" : "Falhou";
        const color = message.severity === "success" ? "success" : message.severity === "warning" ? "warning" : "error";
        const testedAt = row.last_tested_at ? formatUpdatedAt(row.last_tested_at) : "Agora";
        return (
          <Tooltip title={`${message.msg} · ${testedAt}`}>
            <Stack justifyContent="center" sx={{ height: "100%" }}>
              <Chip label={label} size="small" color={color} />
              <Typography variant="caption" color="text.secondary" noWrap>{testedAt}</Typography>
            </Stack>
          </Tooltip>
        );
      },
    },
    {
      field: "actions",
      headerName: "Ações",
      width: 126,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
          <Tooltip title="Editar conexão">
            <IconButton size="small" aria-label={`Editar ${row.name}`} onClick={() => onEdit(row)}><Edit fontSize="small" /></IconButton>
          </Tooltip>
          <Tooltip title="Testar conexão">
            <span>
              <IconButton size="small" disabled={testingId === row.id} aria-label={`Testar ${row.name}`} onClick={() => onTest(row)}><Cable fontSize="small" /></IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Excluir conexão">
            <IconButton size="small" color="error" aria-label={`Excluir ${row.name}`} onClick={() => onDelete(row)}><Delete fontSize="small" /></IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ], [activeFilters, testMessages, testingId]);

  const query = (next: Partial<{ offset: number; limit: number; sort: { field: string; direction: "asc" | "desc" }; filters: ConnectionGridFilter[] }> = {}) => {
    const currentSort = sortModel[0]?.sort ? { field: sortModel[0].field, direction: sortModel[0].sort } : undefined;
    onQuery({
      offset: next.offset ?? paginationModel.page * paginationModel.pageSize,
      limit: next.limit ?? paginationModel.pageSize,
      ...(next.sort ?? currentSort ? { sort: next.sort ?? currentSort } : {}),
      filters: next.filters ?? Object.values(activeFilters),
    });
  };

  const onPagination = (model: GridPaginationModel) => {
    setPaginationModel(model);
    query({ offset: model.page * model.pageSize, limit: model.pageSize });
  };

  const onSort = (model: GridSortModel) => {
    setSortModel(model);
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const item = model[0];
    if (item?.sort) query({ offset: 0, sort: { field: item.field, direction: item.sort } });
    else query({ offset: 0 });
  };

  const onFilter = (model: GridFilterModel) => {
    setFilterModel(model);
    const item = model.items.find((candidate) => candidate.value);
    if (!item?.value || !item.field) {
      return;
    }
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filter = { field: item.field, operator: "equals" as const, value: String(item.value) };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
  };

  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const field = cell?.getAttribute("data-field") || "";
    const rowId = Number(cell?.parentElement?.getAttribute("data-id"));
    const row = connections.find((connection) => connection.id === rowId);
    const allowedFields = new Set(["name", "code", "provider", "connector_type"]);
    const value = field === "provider"
      ? row?.cloud_scope || ""
      : field === "connector_type"
        ? row?.connector_type || ""
        : field === "name"
          ? row?.name || ""
          : field === "code"
            ? row?.code || ""
        : cell?.textContent?.trim() || "";
    if (allowedFields.has(field) && value) setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field, value });
  };

  const applyContextFilter = (operator: "equals" | "notEquals") => {
    if (!context) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filter = { field: context.field, operator, value: context.value };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
  };

  const clearFilter = (field?: string) => {
    if (field && !activeFilters[field]) {
      setContext(null);
      setHeaderContext(null);
      return;
    }
    const filters = { ...activeFilters };
    if (field) delete filters[field];
    else Object.keys(filters).forEach((key) => delete filters[key]);
    setActiveFilters(filters);
    setFilterModel({ items: Object.values(filters).map((filter) => ({ id: filter.field, field: filter.field, operator: "equals", value: filter.value })) });
    setPaginationModel((current) => ({ ...current, page: 0 }));
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
  };

  return (
    <>
      <Box sx={{ height: 570, width: "100%" }} onContextMenu={onCellContextMenu}>
        <DataGrid
          apiRef={apiRef}
          rows={connections}
          columns={columns}
          loading={loading}
          rowCount={total}
          disableRowSelectionOnClick
          pagination
          paginationMode="server"
          sortingMode="server"
          filterMode="server"
          pageSizeOptions={[10, 25, 50]}
          paginationModel={paginationModel}
          sortModel={sortModel}
          filterModel={filterModel}
          onPaginationModelChange={onPagination}
          onSortModelChange={onSort}
          onFilterModelChange={onFilter}
          onColumnHeaderContextMenu={(params, event) => {
            event.preventDefault();
            setHeaderContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field: params.field });
          }}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 2,
            bgcolor: "background.paper",
            "& .MuiDataGrid-columnHeaders": { bgcolor: "#f8fafc", borderBottomColor: "divider" },
            "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus": { outline: "none" },
          }}
        />
      </Box>
      <Menu
        open={context !== null}
        onClose={() => setContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={context ? { top: context.mouseY, left: context.mouseX } : undefined}
      >
        <MenuItem onClick={() => applyContextFilter("equals")}>Mostrar iguais</MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>Mostrar diferentes</MenuItem>
        <MenuItem disabled={!context || !activeFilters[context.field]} onClick={() => clearFilter(context?.field)}>Limpar filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Limpar todos os filtros</MenuItem>
      </Menu>
      <Menu
        open={headerContext !== null}
        onClose={() => setHeaderContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={headerContext ? { top: headerContext.mouseY, left: headerContext.mouseX } : undefined}
      >
        <MenuItem onClick={() => {
          const field = headerContext?.field;
          setHeaderContext(null);
          if (field) apiRef.current.showColumnMenu(field);
        }}>Opções da coluna</MenuItem>
        <Divider />
        <MenuItem disabled={!headerContext || !activeFilters[headerContext.field]} onClick={() => clearFilter(headerContext?.field)}>Limpar filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Limpar todos os filtros</MenuItem>
      </Menu>
    </>
  );
}
