import { useEffect, useMemo, useState } from "react";
import { Alert, Autocomplete, Box, Button, Checkbox, Chip, FormControlLabel, Paper, Stack, Switch, TextField, Typography } from "@mui/material";
import { FilterAltOff, Refresh } from "@mui/icons-material";
import ExpectedViewV2 from "../Dashboards/ExpectedViewV2";
import ExecutiveWidgetsV2 from "./ExecutiveWidgetsV2";
import { Department, ForecastAck, GoldInfo, GoldQueryFilter, formatApiError, getDepartments, getDimonInfo, getExecutiveManualAcks, getGoldFilterOptions } from "../../services/api";
import { flattenDepartments } from "../../components/DepartmentTreePicker";

const TABLE = "dest_dm_cost_visao_completa";
type DimFilter = { column: string; values: string[] };
const FRIENDLY: Record<string, string> = { cloud_origem: "Cloud", organograma_nivel_1: "Segmento", organograma_nivel_2: "Modelo", organograma_nivel_3: "VP", organograma_nivel_4: "Diretoria", organograma_nivel_5: "Projeto", organograma_nivel_6: "SubProjeto", servicename: "Serviço", servicecategory: "Família de serviço", regionname: "Região", subaccountname: "Subconta" };

export default function GoldDashboardV2() {
  const year = new Date().getFullYear();
  const [info, setInfo] = useState<GoldInfo | null>(null);
  const [org, setOrg] = useState<Department[]>([]);
  const flat = useMemo(() => flattenDepartments(org), [org]);
  const [monthFrom, setMonthFrom] = useState(`${year}-01`);
  const [monthTo, setMonthTo] = useState(`${year}-12`);
  const [selectedNodes, setSelectedNodes] = useState<Array<Department & { path: string }>>([]);
  const selectedNodePaths = useMemo(() => selectedNodes.map((node) => node.path), [selectedNodes]);
  const [directorView, setDirectorView] = useState(false);
  const [filters, setFilters] = useState<DimFilter[]>([]);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [sharedCost, setSharedCost] = useState(false);
  const [highlights, setHighlights] = useState<ForecastAck[]>([]);
  const [highlightsLoading, setHighlightsLoading] = useState(true);
  const validRange = Boolean(monthFrom && monthTo && monthFrom <= monthTo);

  useEffect(() => {
    if (!validRange) { setHighlights([]); setHighlightsLoading(false); return; }
    let active = true;
    Promise.all([getDimonInfo(TABLE), getDepartments()]).then(([gold, departments]) => {
      if (active) { setInfo(gold); setOrg(departments); }
    }).catch((reason) => { if (active) setError(formatApiError(reason, "Falha ao carregar o contexto do dashboard")); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    setHighlightsLoading(true);
    getExecutiveManualAcks(monthFrom.replace("-", ""), monthTo.replace("-", ""), selectedNodePaths)
      .then((result) => { if (active) setHighlights(result.items); })
      .catch((reason) => { if (active) setError(formatApiError(reason, "Falha ao carregar Executive Highlights")); })
      .finally(() => { if (active) setHighlightsLoading(false); });
    return () => { active = false; };
  }, [monthFrom, monthTo, selectedNodePaths, validRange]);

  const chooseNodes = (_: unknown, values: Array<Department & { path: string }>) => {
    const compressed = values.filter((node) => !values.some((parent) => parent.id !== node.id && node.path.startsWith(`${parent.path} > `)));
    setSelectedNodes(compressed);
  };
  const setDimensionValues = (column: string, values: string[]) => setFilters((current) => {
    const remaining = current.filter((filter) => filter.column !== column);
    return values.length ? [...remaining, { column, values }] : remaining;
  });
  const clear = () => {
    setMonthFrom(`${year}-01`); setMonthTo(`${year}-12`); setSelectedNodes([]); setFilters([]); setDirectorView(false); setSharedCost(false);
  };
  const dimensionColumns = (info?.dimensions || []).filter((column) => !["_billing_period", "tipo"].includes(column.toLowerCase()));
  const dimensionFilters = useMemo(() => Object.fromEntries(filters.map((item) => [item.column, item.values])), [filters]);
  const selectedNodeIds = useMemo(() => selectedNodes.map((node) => node.id), [selectedNodes]);
  const scopeFilters = useMemo<GoldQueryFilter[]>(() => {
    const result: GoldQueryFilter[] = [];
    const [fromYear, fromMonth] = monthFrom.split("-").map(Number); const [toYear, toMonth] = monthTo.split("-").map(Number);
    if (fromYear && toYear && fromYear * 100 + fromMonth <= toYear * 100 + toMonth) {
      const periods: string[] = [];
      for (let y = fromYear, m = fromMonth; y < toYear || (y === toYear && m <= toMonth);) { periods.push(`${y}${String(m).padStart(2, "0")}`); if (++m > 12) { y++; m = 1; } }
      result.push({ column: "_billing_period", operator: "IN", values: periods });
    }
    filters.forEach((item) => result.push({ column: item.column, operator: "IN", values: item.values }));
    return result;
  }, [monthFrom, monthTo, filters]);
  const organizationGroups = useMemo<GoldQueryFilter[][]>(() => selectedNodes.map((node) => {
    const path = node.path.split(" > ");
    if (directorView && path.length >= 4) [path[2], path[3]] = [path[3], path[2]];
    const levels = directorView ? [1, 2, 4, 3, 5, 6] : [1, 2, 3, 4, 5, 6];
    return path.slice(0, 6).map((value, index) => ({ column: `organograma_nivel_${levels[index] || index + 1}`, operator: "IN", values: [value] }));
  }), [selectedNodes, directorView]);
  const filterContextKey = JSON.stringify({ monthFrom, monthTo, nodes: selectedNodes.map((n) => n.id), directorView, filters, sharedCost, refresh });

  return <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
    <Box sx={{ px: 3, pt: 3, pb: 2, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
        <Box><Typography variant="h4">FinOps Intelligence v2</Typography><Typography color="text.secondary">Visão Executiva · clone isolado da experiência atual</Typography></Box>
        <Button startIcon={<Refresh />} onClick={() => setRefresh((value) => value + 1)}>Atualizar</Button>
      </Stack>
    </Box>
    <Box sx={{ p: { xs: 1.5, md: 3 } }}><Paper elevation={0} sx={{ p: { xs: 1.5, md: 2.5 }, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
        <Stack direction="row" flexWrap="wrap" gap={1.2} alignItems="center">
          <TextField size="small" type="month" label="De" value={monthFrom} onChange={(event) => setMonthFrom(event.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField size="small" type="month" label="Até" value={monthTo} onChange={(event) => setMonthTo(event.target.value)} InputLabelProps={{ shrink: true }} />
          <Autocomplete multiple disableCloseOnSelect size="small" options={flat.filter((node) => node.active)} value={selectedNodes} onChange={chooseNodes} getOptionLabel={(node) => node.path} isOptionEqualToValue={(a, b) => a.id === b.id} renderOption={(props, option, state) => <li {...props}><Checkbox checked={state.selected} sx={{ mr: 1 }} />{option.path}</li>} renderInput={(params) => <TextField {...params} label="Organograma · buscar e selecionar" placeholder="Nome ou caminho" />} sx={{ minWidth: 320, flex: "1 1 320px" }} />
          <FormControlLabel control={<Switch checked={directorView} onChange={(_, checked) => setDirectorView(checked)} />} label="Visão Diretoria" />
          <FormControlLabel control={<Switch checked={sharedCost} onChange={(_, checked) => setSharedCost(checked)} />} label="Incluir rateio" />
          <Button startIcon={<FilterAltOff />} onClick={clear}>Limpar</Button>
        </Stack>
        {selectedNodes.length > 0 && <Stack direction="row" flexWrap="wrap" gap={.5} mt={1}>{selectedNodes.map((node) => <Chip key={node.id} label={node.path} onDelete={() => setSelectedNodes((current) => current.filter((item) => item.id !== node.id))} />)}</Stack>}
        <Stack direction="row" flexWrap="wrap" gap={1} alignItems="center" mt={1}>
          <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(205px, 1fr))", gap: 1, width: "100%" }}>
            {dimensionColumns.filter((column) => FRIENDLY[column]).map((column) => <CascadeDimensionFilter key={column} column={column} label={FRIENDLY[column]} table={TABLE} value={filters.find((filter) => filter.column === column)?.values || []} filters={scopeFilters.filter((filter) => filter.column !== column)} filterGroups={organizationGroups} onChange={(values) => setDimensionValues(column, values)} />)}
          </Box>
        </Stack>
        <Typography variant="caption" color="text.secondary" display="block" mt={1}>Período e filtros dimensionais são compartilhados pelos widgets executivos. Uma mesma coluna aceita vários valores; colunas diferentes refinam o contexto em conjunto.</Typography>
        {monthFrom > monthTo && <Alert severity="warning" sx={{ mt: 1 }}>O mês inicial precisa ser anterior ou igual ao final.</Alert>}
      </Paper>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="h6" fontWeight={750}>Executive Highlights</Typography>
        <Typography variant="caption" color="text.secondary">Anotações administrativas ativas no intervalo e nos caminhos de organograma selecionados; filtros dimensionais sem campo no cadastro não alteram esta lista.</Typography>
        {highlightsLoading ? <Typography display="block" color="text.secondary" mt={1}>Carregando registros do período…</Typography> : highlights.length ? <Stack spacing={1} mt={1}>{[...highlights].sort((a, b) => Number(a.ordem ?? 999) - Number(b.ordem ?? 999) || severityRank(a.severity) - severityRank(b.severity) || String(a.registered_at || a.created_at).localeCompare(String(b.registered_at || b.created_at))).map((ack) => <Alert key={ack.id} severity={ack.severity?.toLocaleLowerCase().includes("crít") ? "error" : ack.severity?.toLocaleLowerCase().includes("perigo") ? "warning" : "info"}><b>{ack.target_month} · {ack.department_name} · {ack.category}</b><br />{ack.department_path.join(" > ")}<br />{ack.reason}<Typography variant="caption" display="block">{ack.registered_at ? new Date(ack.registered_at).toLocaleDateString("pt-BR") : ""}</Typography></Alert>)}</Stack> : <Typography display="block" color="text.secondary" mt={1}>Nenhum highlight ativo nos meses e caminhos selecionados.</Typography>}
      </Paper>
      {validRange ? <ExpectedViewV2 embedded table={TABLE} director={selectedNodes[0]?.path || ""} departmentId={selectedNodes.length === 1 ? selectedNodeIds[0] : undefined} organizationNodeIds={selectedNodeIds} directorView={directorView} periodFrom={monthFrom.replace("-", "")} periodTo={monthTo.replace("-", "")} scopeKey={filterContextKey} dimensionFilters={dimensionFilters} includeSharedCost={sharedCost} footer={<ExecutiveWidgetsV2 table={TABLE} info={info} filters={scopeFilters} filterGroups={organizationGroups} refreshKey={refresh} year={Number(monthFrom.slice(0, 4)) || year} onAddFilter={(column, value) => setFilters((current) => { const existing = current.find((filter) => filter.column === column); const values = [...new Set([...(existing?.values || []), value])]; return [...current.filter((filter) => filter.column !== column), { column, values }]; })} />} /> : <Alert severity="warning">O mês inicial precisa ser anterior ou igual ao mês final. Os dados não serão consultados até corrigir o intervalo.</Alert>}
    </Paper></Box>
  </Box>;
}

function CascadeDimensionFilter({ column, label, table, value, filters, filterGroups, onChange }: {
  column: string; label: string; table: string; value: string[]; filters: GoldQueryFilter[]; filterGroups: GoldQueryFilter[][]; onChange: (values: string[]) => void;
}) {
  const [options, setOptions] = useState<string[]>(value);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const contextKey = JSON.stringify([column, filters, filterGroups, search]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    const timer = window.setTimeout(() => getGoldFilterOptions({ table, column, filters, filter_groups: filterGroups.length ? filterGroups : undefined, search, limit: 100 })
      .then((result) => { if (active) setOptions([...new Set([...value, ...result.values])]); })
      .catch(() => { if (active) setOptions(value); }), 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [contextKey, open]);
  return <Autocomplete multiple size="small" open={open} onOpen={() => setOpen(true)} onClose={() => setOpen(false)} options={options} value={value} onChange={(_, values) => onChange(values)} onInputChange={(_, text) => setSearch(text)} filterOptions={(items) => items} renderInput={(params) => <TextField {...params} label={label} placeholder={value.length ? "Adicionar" : "Todos"} />} />;
}

function severityRank(value?: string) {
  const severity = (value || "").toLocaleLowerCase();
  if (severity.includes("crít") || severity.includes("crit")) return 0;
  if (severity.includes("perigo")) return 1;
  if (severity.includes("sucesso")) return 3;
  return 2;
}
