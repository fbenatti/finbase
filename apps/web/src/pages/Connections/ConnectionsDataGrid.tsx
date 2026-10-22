import DataGrid from "../../components/FinbaseDataGrid";
import { useMemo, useState } from "react";
import {
  Box,
  Chip,
  Divider,
  ListItemIcon,
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
  History,
  FilterAlt, More,
} from "./ConnectionIcons";
import { GridColDef, GridFilterModel, GridPaginationModel, GridSortModel, useGridApiRef } from "@mui/x-data-grid";
import { SourceConnection } from "../../services/api";
import { RowAction } from "../../components/RowAction";

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
  onHistory: (connection: SourceConnection) => void;
  onTest: (connection: SourceConnection) => void;
  onDelete: (connection: SourceConnection) => void;
  onQuery: (query: { offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: ConnectionGridFilter[] }) => void;
};

const connectorLabels: Record<string, string> = {
  azure_blob: "Arquivos de custo Azure",
  azure_mca_invoice: "Faturas e compromissos Azure",
  azure_pricesheet: "Tabela de preços contratados (Azure)",
  azure_automation_jobs: "Jobs de automação Azure",
  azure_resource_graph: "Inventário de recursos Azure",
  manual_csv: "Carga manual de CSV",
  azure_openai_ptu: "Capacidade provisionada Azure",
  gcp_bigquery: "GCP: BigQuery",
  oci: "Oracle Cloud Infrastructure (OCI)",
  mongodb_atlas: "Atlas (MongoDB)",
  github_billing: "Faturamento GitHub",
  github_license: "GitHub: licenças (Copilot)",
  github_token: "GitHub: tokens (Copilot)",
  notebooklm_license: "NotebookLM: licenças",
  notebooklm_token: "NotebookLM: tokens",
  claude_license: "Claude: licenças",
  claude_token: "Claude: tokens",
  gemini_license: "Gemini: licenças",
  gemini_token: "Gemini: tokens",
  gemini_notebooklm: "Gemini e NotebookLM",
  databricks: "Consumo Databricks",
};

export const getConnectorLabel = (type: string) => connectorLabels[type] || type.replace(/_/g, " ");

const providerLabels: Record<string, string> = {
  azure: "Azure",
  gcp: "GCP",
  ibm: "IBM Cloud",
  aws: "AWS",
  oci: "Oracle Cloud Infrastructure (OCI)",
  atlas: "Atlas (MongoDB)",
  mongodb: "Atlas (MongoDB)",
  github: "GitHub Enterprise",
  notebooklm: "NotebookLM",
  gemini: "Gemini",
  claude: "Claude",
  databricks: "Databricks",
};

export function getProviderLabel(scope: string | null): string {
  if (!scope) return "Sem escopo informado";
  return providerLabels[scope] || scope;
}

function getConnectionSummary(connection: SourceConnection): string {
  const config = connection.config_json as Record<string, string>;
  switch (connection.connector_type) {
    case "azure_openai_ptu":
      return [config.credential_profile || "azure-4p", "PTU em produção"].join(" · ");
    case "azure_blob": {
      const location = [config.storage_account, config.container_name].filter(Boolean).join(" / ");
      return location || "Conta e container não definidos";
    }
    case "gcp_bigquery": {
      const dataset = [config.project_id, config.dataset_id].filter(Boolean).join(" / ");
      return dataset || config.table_name || "Projeto e dataset não definidos";
    }
    case "oci":
      return [config.region, config.tenancy_ocid || config.tenancy].filter(Boolean).join(" · ") || "Região não definida";
    case "mongodb_atlas":
      return [config.group_id, config.contract_type].filter(Boolean).join(" · ") || "Grupo e contrato não definidos";
    case "manual_csv":
      return "CSV carregado manualmente";
    case "github_billing":
      return [config.enterprise_slug, "Faturamento"].filter(Boolean).join(" · ") || "Enterprise não definida";
    case "github_license":
      return [config.enterprise_slug, "Licenças (Copilot)"].filter(Boolean).join(" · ") || "Enterprise não definida";
    case "github_token":
      return [config.enterprise_slug, "Tokens (Copilot)"].filter(Boolean).join(" · ") || "Enterprise não definida";
    case "notebooklm_license":
    case "notebooklm_token":
    case "gemini_license":
    case "gemini_token":
    case "gemini_notebooklm":
      return [config.project_id, config.project_number].filter(Boolean).join(" · ") || "Project ID não definido";
    case "claude_license":
    case "claude_token":
      return [config.org_id, config.org_name].filter(Boolean).join(" · ") || "Org não definida";
    case "databricks":
      return [config.server_hostname, config.table_name].filter(Boolean).join(" · ") || "Host não definido";
    default:
      return connection.cloud_name || "Detalhes visíveis ao editar";
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
  onHistory,
  onTest,
  onDelete,
  onQuery,
}: Props) {
  const apiRef = useGridApiRef();
  const [rowMenu, setRowMenu] = useState<{ anchor: HTMLElement; connection: SourceConnection } | null>(null);
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 50 });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, ConnectionGridFilter>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" fontWeight={600} noWrap>{label}</Typography>
      {activeFilters[field] && <Tooltip title="Filtro ativo nesta coluna"><FilterAlt fontSize="small" color="primary" /></Tooltip>}
    </Stack>
  );
  const columns = useMemo<GridColDef[]>(() => {
    const definitions: GridColDef[] = [
    {
      field: "name",
      headerName: "Nome",
      renderHeader: () => filteredHeader("Nome", "name"),
      minWidth: 220,
      flex: 1.15,
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          <Typography variant="body2" fontWeight={700} noWrap>{row.name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>{row.cloud_name || "Conta sem nome"}</Typography>
        </Stack>
      ),
    },
    { field: "code", headerName: "Identificador", renderHeader: () => filteredHeader("Identificador", "code"), minWidth: 145, flex: 0.7 },
    {
      field: "provider",
      headerName: "Provedor",
      renderHeader: () => filteredHeader("Provedor", "provider"),
      minWidth: 130,
      flex: 0.7,
      valueGetter: (_, row: SourceConnection) => getProviderLabel(row.cloud_scope),
      renderCell: ({ row }: { row: SourceConnection }) => <Chip label={getProviderLabel(row.cloud_scope)} size="small" variant="outlined" />,
    },
    {
      field: "connector_type",
      headerName: "Tipo de integração",
      renderHeader: () => filteredHeader("Tipo de integração", "connector_type"),
      minWidth: 180,
      flex: 0.9,
      valueGetter: (_, row: SourceConnection) => getConnectorLabel(row.connector_type),
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
          <Typography variant="body2" noWrap>{getConnectorLabel(row.connector_type)}</Typography>
          {row.config_json?.marketplace === true && <Chip label="Oferta no marketplace" size="small" color="secondary" />}
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
      headerName: "Última atualização",
      renderHeader: () => filteredHeader("Última atualização", "updated_at"),
      minWidth: 165,
      flex: 0.75,
      type: "dateTime",
      valueGetter: (_, row: SourceConnection) => row.updated_at ? new Date(row.updated_at) : null,
      valueFormatter: (value) => formatUpdatedAt(value),
    },
    {
      field: "test_status",
      headerName: "Resultado do último teste",
      minWidth: 135,
      sortable: false,
      filterable: false,
      renderCell: ({ row }: { row: SourceConnection }) => {
        const persistedMessage: ConnectionTestMessage | undefined = row.last_test_status ? {
          severity: row.last_test_status === "OK" ? "success" : row.last_test_status === "PENDING" ? "warning" : "error",
          msg: row.last_test_message || "Teste concluído, sem detalhes.",
        } : undefined;
        const message = testMessages[row.id] || persistedMessage;
        if (testingId === row.id) return <Chip label="Testando…" size="small" color="info" />;
        if (!message) return <Typography variant="caption" color="text.secondary">Sem teste</Typography>;
        const label = message.severity === "success" ? "Testada com sucesso" : message.severity === "warning" ? "Pendente" : "Falha no teste";
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
      headerName: "Operações",
      width: 104,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: SourceConnection }) => (
        <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
          <Tooltip title="Alterar conexão">
            <RowAction kind="edit" label={`Alterar ${row.name}`} onClick={() => onEdit(row)} />
          </Tooltip>
          <Tooltip title="Outras opções">
            <IconButton aria-label={`Outras opções de ${row.name}`} aria-haspopup="menu" onClick={event => setRowMenu({ anchor: event.currentTarget, connection: row })}><More /></IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ];
    return definitions.sort((left, right) => ["name", "provider", "connector_type", "test_status", "actions", "code", "origin", "updated_at"].indexOf(left.field) - ["name", "provider", "connector_type", "test_status", "actions", "code", "origin", "updated_at"].indexOf(right.field));
  }, [activeFilters, testMessages, testingId, onEdit]);

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
      <Box sx={{ height: 560, width: "100%" }} onContextMenu={onCellContextMenu}>
        <DataGrid
          aria-label="Lista de conexões de dados"
          apiRef={apiRef}
          initialState={{ columns: { columnVisibilityModel: { code: false, origin: false, updated_at: false } } }}
          rows={connections}
          columns={columns}
          loading={loading}
          rowCount={total}
          disableRowSelectionOnClick
          pagination
          paginationMode="server"
          sortingMode="server"
          filterMode="server"
          pageSizeOptions={[50, 100, 500, 1000]}
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
            border: 0,
            borderRadius: 0,
            bgcolor: "background.paper",
            "& .MuiDataGrid-columnHeaders": { bgcolor: "background.default", borderBottomColor: "divider" },
          }}
        />
      </Box>
      <Menu anchorEl={rowMenu?.anchor} open={Boolean(rowMenu)} onClose={() => setRowMenu(null)}>
        <MenuItem disabled={testingId === rowMenu?.connection.id} onClick={() => { if (rowMenu) onTest(rowMenu.connection); setRowMenu(null); }}><ListItemIcon><Cable /></ListItemIcon>Testar conexão</MenuItem>
        <MenuItem onClick={() => { if (rowMenu) onHistory(rowMenu.connection); setRowMenu(null); }}><ListItemIcon><History /></ListItemIcon>Abrir log de alterações</MenuItem>
        <Divider />
        <MenuItem sx={{ color: "error.main" }} onClick={() => { if (rowMenu) onDelete(rowMenu.connection); setRowMenu(null); }}><ListItemIcon><Delete color="error" /></ListItemIcon>Remover conexão</MenuItem>
      </Menu>
      <Menu
        open={context !== null}
        onClose={() => setContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={context ? { top: context.mouseY, left: context.mouseX } : undefined}
      >
        <MenuItem onClick={() => applyContextFilter("equals")}>Filtrar iguais</MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>Filtrar diferentes</MenuItem>
        <MenuItem disabled={!context || !activeFilters[context.field]} onClick={() => clearFilter(context?.field)}>Remover filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Remover todos os filtros</MenuItem>
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
        }}>Configurar coluna</MenuItem>
        <Divider />
        <MenuItem disabled={!headerContext || !activeFilters[headerContext.field]} onClick={() => clearFilter(headerContext?.field)}>Remover filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Remover todos os filtros</MenuItem>
      </Menu>
    </>
  );
}
