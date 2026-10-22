import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState } from "react";
import { Alert, Autocomplete, Box, Button, Chip, FormControlLabel, IconButton, Paper, Popover, Stack, Switch, TextField, Typography } from "@mui/material";
import { AddOutlined, CalendarMonthOutlined, CloseOutlined, FilterAltOffOutlined, RefreshOutlined } from "@mui/icons-material";
import ExpectedViewV2 from "../Dashboards/ExpectedViewV2";
import ExecutiveWidgetsV2 from "./ExecutiveWidgetsV2";
import { Department, ForecastAck, GoldInfo, GoldQueryFilter, formatApiError, getDepartments, getDimonInfo, getExecutiveManualAcks, getGoldFilterOptions } from "../../services/api";
import { DepartmentTreeMultiPicker, flattenDepartments } from "../../components/DepartmentTreePicker";

const TABLE = "dest_dm_cost_visao_completa";
const EXECUTIVE_DEPARTMENT_STORAGE_KEY = "openfinops.executiveDepartmentId";
const EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY = "openfinops.executiveDirectorView";
type DimFilter = { column: string; values: string[] };
const FRIENDLY: Record<string, string> = { cloud_origem: "Cloud", hierarquia_1: "Segmento", hierarquia_2: "Modelo", hierarquia_3: "VP", hierarquia_4: "Diretoria", hierarquia_5: "Projeto", hierarquia_6: "SubProjeto", servicename: "Serviço", servicecategory: "Família de serviço", regionname: "Região", subaccountname: "Subconta" };
const MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const toMonthValue = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
const shiftMonth = (value: string, delta: number) => { const [y, m] = value.split("-").map(Number); return toMonthValue(new Date(y, m - 1 + delta, 1)); };
const labelMonth = (value: string) => { const [y, m] = value.split("-").map(Number); return `${MONTHS_PT[m - 1] || ""} ${y}`; };
const labelColumn = (column: string) => FRIENDLY[column] || column.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());

export default function GoldDashboardV2() {
  const year = new Date().getFullYear();
  const [info, setInfo] = useState<GoldInfo | null>(null);
  const [org, setOrg] = useState<Department[]>([]);
  const flat = useMemo(() => flattenDepartments(org), [org]);
  const [monthFrom, setMonthFrom] = useState(`${year}-01`);
  const [monthTo, setMonthTo] = useState(`${year}-12`);
  const [dateAnchor, setDateAnchor] = useState<HTMLElement | null>(null);
  const [draftFrom, setDraftFrom] = useState(`${year}-01`);
  const [draftTo, setDraftTo] = useState(`${year}-12`);
  const [filterMenuAnchor, setFilterMenuAnchor] = useState<HTMLElement | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<Array<Department & { path: string }>>([]);
  const selectedNodePaths = useMemo(() => selectedNodes.map((node) => node.path), [selectedNodes]);
  const [directorView, setDirectorView] = useState(
    () => window.localStorage.getItem(EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY) === "1",
  );
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
      if (active) {
        setInfo(gold);
        setOrg(departments);
        const nodes = flattenDepartments(departments);
        const savedId = Number(window.localStorage.getItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY));
        const saved = Number.isInteger(savedId) && savedId > 0
          ? nodes.find((node) => node.id === savedId && node.active)
          : undefined;
        const root = nodes.find((node) => node.path === "Consumo de nuvem");
        if (saved) setSelectedNodes([saved]);
        else {
          if (savedId) window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
          if (root) setSelectedNodes([root]);
        }
      }
    }).catch((reason) => { if (active) setError(formatApiError(reason, "Não foi possível carregar o contexto do painel")); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    setHighlightsLoading(true);
    getExecutiveManualAcks(monthFrom.replace("-", ""), monthTo.replace("-", ""), selectedNodePaths)
      .then((result) => { if (active) setHighlights(result.items); })
      .catch((reason) => { if (active) setError(formatApiError(reason, "Não foi possível carregar os destaques executivos")); })
      .finally(() => { if (active) setHighlightsLoading(false); });
    return () => { active = false; };
  }, [monthFrom, monthTo, selectedNodePaths, validRange]);

  const chooseNodes = (ids: number[]) => {
    const values = ids.map((id) => flat.find((node) => node.id === id)).filter((node): node is Department & { path: string } => Boolean(node));
    setSelectedNodes(values);
    if (values.length === 1) window.localStorage.setItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY, String(values[0].id));
    else window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
  };
  const setDimensionValues = (column: string, values: string[]) => setFilters((current) => {
    const remaining = current.filter((filter) => filter.column !== column);
    return [...remaining, { column, values }];
  });
  const addDimension = (column: string) => {
    setFilters((current) => current.some((filter) => filter.column === column) ? current : [...current, { column, values: [] }]);
    setFilterMenuAnchor(null);
  };
  const applyPreset = (preset: "this_month" | "last_month" | "last_3" | "last_6" | "last_12" | "this_year" | "last_year") => {
    const now = new Date(); const current = toMonthValue(now); const thisYear = now.getFullYear();
    const ranges: Record<typeof preset, [string, string]> = {
      this_month: [current, current],
      last_month: [shiftMonth(current, -1), shiftMonth(current, -1)],
      last_3: [shiftMonth(current, -2), current],
      last_6: [shiftMonth(current, -5), current],
      last_12: [shiftMonth(current, -11), current],
      this_year: [`${thisYear}-01`, `${thisYear}-12`],
      last_year: [`${thisYear - 1}-01`, `${thisYear - 1}-12`],
    };
    setMonthFrom(ranges[preset][0]); setMonthTo(ranges[preset][1]); setDateAnchor(null);
  };
  const clear = () => {
    const root = flat.find((node) => node.path === "Consumo de nuvem");
    setMonthFrom(`${year}-01`); setMonthTo(`${year}-12`); setSelectedNodes(root ? [root] : []); setFilters([]); setDirectorView(false); setSharedCost(false);
    window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
    window.localStorage.setItem(EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY, "0");
  };
  // `/gold/info` exposes both the curated dimension list and the complete
  // physical schema. Some older Gold tables omit dimensions, so use the
  // schema as a fallback instead of rendering an empty dimension menu.
  const dimensionColumns = useMemo(() => {
    const fromDimensions = info?.dimensions || [];
    const fromSchema = info?.columns?.filter((column) => column.type !== "measure").map((column) => column.name) || [];
    return [...new Set([...fromDimensions, ...fromSchema])]
      .filter((column) => !["_billing_period", "tipo"].includes(column.toLowerCase()))
      .sort((a, b) => labelColumn(a).localeCompare(labelColumn(b), "pt-BR"));
  }, [info]);
  const availableDimensionColumns = dimensionColumns.filter((column) => !filters.some((item) => item.column === column));
  const dimensionFilters = useMemo(() => Object.fromEntries(filters.filter((item) => item.values.length > 0).map((item) => [item.column, item.values])), [filters]);
  const selectedNodeIds = useMemo(() => selectedNodes.map((node) => node.id), [selectedNodes]);
  const scopeFilters = useMemo<GoldQueryFilter[]>(() => {
    const result: GoldQueryFilter[] = [];
    const [fromYear, fromMonth] = monthFrom.split("-").map(Number); const [toYear, toMonth] = monthTo.split("-").map(Number);
    if (fromYear && toYear && fromYear * 100 + fromMonth <= toYear * 100 + toMonth) {
      const periods: string[] = [];
      for (let y = fromYear, m = fromMonth; y < toYear || (y === toYear && m <= toMonth);) { periods.push(`${y}${String(m).padStart(2, "0")}`); if (++m > 12) { y++; m = 1; } }
      result.push({ column: "_billing_period", operator: "IN", values: periods });
    }
    filters.filter((item) => item.values.length > 0).forEach((item) => result.push({ column: item.column, operator: "IN", values: item.values }));
    return result;
  }, [monthFrom, monthTo, filters]);
  const chartFilters = scopeFilters;
  const organizationGroups = useMemo<GoldQueryFilter[][]>(() => selectedNodes.map((node) => {
    const path = node.path.split(" > ");
    // O caminho da árvore já começa no valor físico de hierarquia_1
    // ("Consumo de nuvem"). Removê-lo deslocava todos os níveis: Showback era
    // enviado como nivel_1, Azure como nivel_2 etc., zerando agrupamentos e
    // Cost Concentration assim que um nó abaixo da raiz era selecionado.
    if (directorView && path.length >= 4) [path[2], path[3]] = [path[3], path[2]];
    const levels = directorView ? [1, 2, 4, 3, 5, 6] : [1, 2, 3, 4, 5, 6];
    return path.slice(0, 6).map((value, index) => ({ column: `hierarquia_${levels[index] || index + 1}`, operator: "IN" as const, values: [value] }));
  }).filter((group) => group.length > 0), [selectedNodes, directorView]);
  const filterContextKey = JSON.stringify({ monthFrom, monthTo, nodes: selectedNodes.map((n) => n.id), directorView, filters, sharedCost, refresh });
  const highlightsWidget = <Paper variant="outlined" sx={{ p: 2, mt: 2, borderRadius: "18px", borderColor: "#E1E8F0", boxShadow: "0 6px 20px rgba(24, 39, 75, 0.035)" }}>
    <Typography variant="overline" color="primary" fontWeight={800} sx={{ letterSpacing: ".08em" }}>DESTAQUES EXECUTIVOS</Typography>
    <Typography variant="caption" color="text.secondary" display="block">Anotações administrativas vigentes no intervalo e nos caminhos de hierarquia selecionados; filtros dimensionais sem campo no cadastro não alteram esta lista.</Typography>
    {highlightsLoading ? <Typography display="block" color="text.secondary" mt={1}>Buscando registros do período…</Typography> : highlights.length ? <Stack spacing={1} mt={1}>{[...highlights].sort((a, b) => Number(a.ordem ?? 999) - Number(b.ordem ?? 999) || severityRank(a.severity) - severityRank(b.severity) || String(a.registered_at || a.created_at).localeCompare(String(b.registered_at || b.created_at))).map((ack) => <Alert key={ack.id} severity={ack.severity?.toLocaleLowerCase().includes("crít") ? "error" : ack.severity?.toLocaleLowerCase().includes("perigo") ? "warning" : "info"}><b>{ack.target_month} · {ack.department_name} · {ack.category}</b><br />{ack.department_path.join(" > ")}<br />{ack.reason}<Typography variant="caption" display="block">{ack.registered_at ? new Date(ack.registered_at).toLocaleDateString("pt-BR") : ""}</Typography></Alert>)}</Stack> : <Typography display="block" color="text.secondary" mt={1}>Nenhum destaque ativo nos meses e caminhos selecionados.</Typography>}
  </Paper>;

  return <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#FAFBFD" }}>
    <Box sx={{ px: 3, pt: 3, pb: 2, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
        <Box><Typography variant="h4">FinOps Intelligence v2 <Chip label="BETA" size="small" color="warning" sx={{ verticalAlign: "middle", ml: .75, fontWeight: 900 }} /></Typography><Typography color="text.secondary">Visão executiva · versão beta</Typography></Box>
        <Button startIcon={<RefreshOutlined />} onClick={() => setRefresh((value) => value + 1)}>Recarregar dados</Button>
      </Stack>
    </Box>
    <Box sx={{ p: { xs: 1.5, md: 3 } }}><Paper elevation={0} sx={{ p: { xs: 1.5, md: 2.5 }, borderRadius: "20px", border: "1px solid #E1E8F0", bgcolor: "#fff", boxShadow: "0 8px 28px rgba(24, 39, 75, 0.045)" }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
      <Paper variant="outlined" sx={{ p: 1.5, mb: 2, borderRadius: "18px", borderColor: "#E1E8F0", boxShadow: "0 6px 20px rgba(24, 39, 75, 0.035)" }}>
        <Stack direction="row" flexWrap="wrap" gap={1.2} alignItems="flex-end">
          <Box sx={{ width: { xs: "100%", md: 330 }, minWidth: { xs: 0, md: 330 }, flex: { xs: "1 1 100%", md: "0 0 330px" } }}>
            <Typography variant="caption" color="text.secondary">Competência · mensal</Typography>
            <Button fullWidth variant="outlined" startIcon={<CalendarMonthOutlined />} onClick={(event) => { setDraftFrom(monthFrom); setDraftTo(monthTo); setDateAnchor(event.currentTarget); }} sx={{ mt: .5, minHeight: 40, px: 1.5, borderRadius: "12px", justifyContent: "flex-start", textTransform: "none", color: "#162B3A", borderColor: "#E1E8F0", bgcolor: "#fff", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{labelMonth(monthFrom)} – {labelMonth(monthTo)}</Button>
          </Box>
          <Box sx={{ minWidth: { xs: "100%", md: 320 }, flex: "1 1 360px" }}><DepartmentTreeMultiPicker departments={org} values={selectedNodes.map((node) => node.id)} onChange={chooseNodes} label="Hierarquia · selecione níveis" placeholder="Percorra e marque níveis" /></Box>
          <FormControlLabel sx={{ pb: .35, m: 0 }} control={<Switch checked={directorView} onChange={(_, checked) => { setDirectorView(checked); window.localStorage.setItem(EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY, checked ? "1" : "0"); }} />} label="Visão Diretoria" />
          <FormControlLabel sx={{ pb: .35, m: 0 }} control={<Switch checked={sharedCost} onChange={(_, checked) => setSharedCost(checked)} />} label="Rateio de custo compartilhado" />
          <Button startIcon={<FilterAltOffOutlined />} onClick={clear}>Limpar seleção</Button>
        </Stack>
        <Popover open={Boolean(dateAnchor)} anchorEl={dateAnchor} onClose={() => setDateAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "left" }} transformOrigin={{ vertical: "top", horizontal: "left" }} PaperProps={{ sx: { mt: .5, borderRadius: "14px", border: "1px solid #E1E8F0", boxShadow: "0 16px 44px rgba(24,39,75,.16)" } }}>
          <Stack direction={{ xs: "column", sm: "row" }} sx={{ p: 2 }} gap={2}>
            <Box sx={{ minWidth: 180 }}><Typography variant="overline" color="text.secondary" fontWeight={800}>Atalhos de competência</Typography><Stack spacing={.25} mt={.5}>
              <Button size="small" onClick={() => applyPreset("this_month")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Mês atual</Button>
              <Button size="small" onClick={() => applyPreset("last_month")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Mês passado</Button>
              <Button size="small" onClick={() => applyPreset("last_3")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Últimos três meses</Button>
              <Button size="small" onClick={() => applyPreset("last_6")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Últimos seis meses</Button>
              <Button size="small" onClick={() => applyPreset("last_12")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Últimos doze meses</Button>
              <Button size="small" onClick={() => applyPreset("this_year")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Ano corrente</Button>
              <Button size="small" onClick={() => applyPreset("last_year")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Exercício anterior</Button>
            </Stack></Box>
            <Box sx={{ minWidth: { sm: 330 }, borderLeft: { sm: "1px solid #EEF3F8" }, pl: { sm: 2 } }}><Typography variant="overline" color="text.secondary" fontWeight={800}>Período personalizado · mensal</Typography><Stack direction={{ xs: "column", sm: "row" }} gap={1} mt={1}><TextField size="small" type="month" label="Mês inicial" value={draftFrom} onChange={(event) => setDraftFrom(event.target.value)} InputLabelProps={{ shrink: true }} /><TextField size="small" type="month" label="Mês final" value={draftTo} onChange={(event) => setDraftTo(event.target.value)} InputLabelProps={{ shrink: true }} /></Stack>{draftFrom > draftTo && <Typography variant="caption" color="error" display="block" mt={.5}>O mês inicial deve ser anterior ou igual ao mês final.</Typography>}<Stack direction="row" justifyContent="flex-end" gap={1} mt={2}><Button size="small" onClick={() => setDateAnchor(null)}>Cancelar</Button><Button size="small" variant="contained" disabled={!draftFrom || !draftTo || draftFrom > draftTo} onClick={() => { setMonthFrom(draftFrom); setMonthTo(draftTo); setDateAnchor(null); }} sx={{ bgcolor: "#0B3FA8" }}>Aplicar</Button></Stack></Box>
          </Stack>
        </Popover>
        {selectedNodes.length > 0 && <Stack direction="row" flexWrap="wrap" gap={.5} mt={1}>{selectedNodes.map((node) => <Chip key={node.id} label={node.path} onDelete={() => setSelectedNodes((current) => current.filter((item) => item.id !== node.id))} />)}</Stack>}
        <Box sx={{ mt: 1 }}>
          <Button variant="outlined" startIcon={<AddOutlined />} onClick={(event) => setFilterMenuAnchor(event.currentTarget)} sx={{ minHeight: 38, borderRadius: "12px", textTransform: "none", color: "#162B3A", borderColor: "#E1E8F0", whiteSpace: "nowrap" }}>Incluir filtro</Button>
          <Popover anchorEl={filterMenuAnchor} open={Boolean(filterMenuAnchor)} onClose={() => setFilterMenuAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "left" }} PaperProps={{ sx: { p: 1.25, width: 340, borderRadius: "12px" } }}>
            <Typography variant="caption" color="text.secondary" sx={{ px: .5, pb: .5, display: "block" }}>CAMPOS FILTRÁVEIS DO MODELO</Typography>
            <Autocomplete
              autoHighlight
              open
              disablePortal
              options={availableDimensionColumns}
              getOptionLabel={labelColumn}
              onChange={(_, column) => { if (column) addDimension(column); }}
              noOptionsText={!info ? "Carregando estrutura da base consolidada…" : dimensionColumns.length ? "Todos os campos já foram incluídos" : "Nenhum eixo filtrável foi encontrado."}
              renderInput={(params) => <TextField {...params} autoFocus size="small" placeholder="Procurar coluna" />}
            />
          </Popover>
        </Box>
        {filters.length > 0 && <Stack direction="row" flexWrap="wrap" gap={1} alignItems="center" mt={1}>
          <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 1, width: "100%" }}>
            {filters.map((filter) => <Stack key={filter.column} direction="row" alignItems="flex-start" gap={.5}><Box sx={{ minWidth: 0, flex: 1 }}><CascadeDimensionFilter column={filter.column} label={labelColumn(filter.column)} table={TABLE} value={filter.values} filters={scopeFilters.filter((item) => item.column !== filter.column)} filterGroups={organizationGroups} onChange={(values) => setDimensionValues(filter.column, values)} /></Box><IconButton size="small" aria-label={`Remover filtro ${labelColumn(filter.column)}`} onClick={() => setFilters((current) => current.filter((item) => item.column !== filter.column))}><CloseOutlined fontSize="small" /></IconButton></Stack>)}
          </Box>
        </Stack>}
        <Typography variant="caption" color="text.secondary" display="block" mt={1}>Competência e filtros dimensionais são compartilhados pelos widgets executivos. Selecione colunas e valores; vários valores na mesma coluna usam OU e colunas diferentes usam E. As opções são pesquisadas no serviço de consultas considerando os demais filtros.</Typography>
        {monthFrom > monthTo && <Alert severity="warning" sx={{ mt: 1 }}>A competência inicial precisa ser igual ou anterior à final.</Alert>}
      </Paper>
      {validRange ? <ExpectedViewV2 embedded table={TABLE} director={selectedNodes[0]?.path || ""} departmentId={selectedNodes.length === 1 ? selectedNodeIds[0] : undefined} organizationNodeIds={selectedNodeIds} directorView={directorView} periodFrom={monthFrom.replace("-", "")} periodTo={monthTo.replace("-", "")} scopeKey={filterContextKey} dimensionFilters={dimensionFilters} includeSharedCost={sharedCost} groupByOptions={dimensionColumns} chartHasType={Boolean(info?.columns.some((column) => column.name.toLowerCase() === "tipo"))} chartFilters={chartFilters} chartFilterGroups={organizationGroups} footer={<ExecutiveWidgetsV2 table={TABLE} info={info} filters={scopeFilters} filterGroups={organizationGroups} refreshKey={refresh} year={Number(monthFrom.slice(0, 4)) || year} onAddFilter={(column, value) => setFilters((current) => { const existing = current.find((filter) => filter.column === column); const values = [...new Set([...(existing?.values || []), value])]; return [...current.filter((filter) => filter.column !== column), { column, values }]; })} />} /> : <Alert severity="warning">A competência inicial precisa ser igual ou anterior à final. Os dados não serão consultados até corrigir o intervalo.</Alert>}
      {highlightsWidget}
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
