import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Box, FormControl, InputLabel, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Department, FinancialDashboardOverview, PlanningAreaRow, getDepartments, getFinancialDashboardOverview, getPlanningAreas } from "../../services/api";
import DepartmentTreePicker from "../../components/DepartmentTreePicker";
import { chartSemantic, colors } from "../../theme/tokens";

type Event = FinancialDashboardOverview["events"][number];

const money = (value: number) => Number(value || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const dateOf = (event: Event) => String(event.effective_date || event.created_at || "").slice(0, 10);
const brDate = (iso: string) => iso.split("-").reverse().join("/");
const sumOf = (list: Event[]) => list.reduce((total, event) => total + Number(event.annual_total || 0), 0);
const TYPE: Record<string, { label: string; color: string }> = {
  CARGA_INICIAL: { label: "Carga", color: colors.brand },
  DEFESA: { label: "Defesa", color: chartSemantic.budget },
  APROVACAO: { label: "Aprovação", color: chartSemantic.positive },
  CORTE: { label: "Corte", color: chartSemantic.negative },
  AJUSTE: { label: "Ajuste", color: colors.warning },
  INCLUSAO_PROJETO: { label: "Inclusão", color: colors.brandDark },
  REALOCACAO: { label: "Realocação", color: colors.warning },
  RESTAURACAO: { label: "Restauração", color: colors.brandDark },
  OUTRO: { label: "Outro", color: colors.textSecondary },
};

function Pill({ color, children }: { color: string; children: ReactNode }) {
  return <Box component="span" sx={{ display: "inline-block", px: 1, py: .25, borderRadius: 999, fontSize: 12, fontWeight: 700, whiteSpace: "nowrap", color, border: `1px solid ${color}55`, bgcolor: `${color}12` }}>{children}</Box>;
}

// Monthly trend of a budget. Draws nothing when there is no value to show.
function Spark({ values, label }: { values: number[]; label: string }) {
  const clean = values.map(value => Number(value) || 0);
  if (clean.length < 2 || clean.every(value => value === 0)) return <Typography variant="caption" color="text.secondary">Sem valores</Typography>;
  const w = 140, h = 32, pad = 3;
  const min = Math.min(...clean), max = Math.max(...clean), span = max - min || 1;
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (clean.length - 1);
  const y = (value: number) => pad + (1 - (value - min) / span) * (h - 2 * pad);
  const line = clean.map((value, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
  const last = clean.length - 1;
  return <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label} style={{ display: "block", width: w, maxWidth: "100%", height: h }}>
    <path d={`${line} L${x(last)},${h} L${x(0)},${h} Z`} fill={colors.brand} fillOpacity={0.1} />
    <path d={line} fill="none" stroke={colors.brand} strokeWidth={1.6} />
    <circle cx={x(last)} cy={y(clean[last])} r={2.5} fill={colors.brand} />
  </svg>;
}

function Stat({ label, value, caption, color = colors.text, divider = false, children }: { label: string; value: number; caption: string; color?: string; divider?: boolean; children?: ReactNode }) {
  return <Box sx={{ p: { xs: 2, md: 2.5 }, minWidth: 0, borderLeft: divider ? { md: `1px solid ${colors.border}` } : undefined, borderTop: divider ? { xs: `1px solid ${colors.border}`, md: "none" } : undefined }}>
    <Typography variant="overline" color="text.secondary">{label}</Typography>
    <Typography sx={{ fontSize: 24, fontWeight: 800, color, fontVariantNumeric: "tabular-nums", lineHeight: 1.2 }}>{money(value)}</Typography>
    <Typography variant="body2" color="text.secondary">{caption}</Typography>
    {children}
  </Box>;
}

const SITUATION = {
  below: { label: "Abaixo da carga", color: colors.warning },
  above: { label: "Acima da carga", color: chartSemantic.positive },
  flat: { label: "Sem movimento", color: colors.textSecondary },
} as const;

export default function FinancialDashboard() {
  const [year, setYear] = useState(2026);
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [data, setData] = useState<FinancialDashboardOverview | null>(null);
  const [areas, setAreas] = useState<PlanningAreaRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => { void getDepartments().then(setDepartments).catch(e => setError(String(e))); }, []);
  useEffect(() => { void getFinancialDashboardOverview(year, "orcamento,defesa-orcamentaria", departmentId).then(setData).catch(e => setError(String(e))); }, [year, departmentId]);
  useEffect(() => { void getPlanningAreas(year, departmentId).then(response => setAreas(response.areas)).catch(e => setError(String(e))); }, [year, departmentId]);

  const events = useMemo(() => (data?.events || []).slice().sort((a, b) => dateOf(a).localeCompare(dateOf(b)) || a.id - b.id), [data]);
  const requestedEvents = useMemo(() => events.filter(event => event.type_code === "defesa-orcamentaria" && event.event_type === "DEFESA"), [events]);
  const approvedEvents = useMemo(() => events.filter(event => event.type_code === "defesa-orcamentaria" && event.event_type === "APROVACAO"), [events]);
  const adjustments = useMemo(() => events.filter(event => event.type_code === "orcamento" && event.event_type !== "CARGA_INICIAL" && event.delta != null && Number(event.delta) !== 0), [events]);

  const requested = sumOf(requestedEvents);
  const approved = sumOf(approvedEvents);
  const budgetCard = data?.cards.find(card => card.code === "orcamento");
  const current = Number(budgetCard?.annual_total || 0);
  const monthly = budgetCard?.monthly || [];
  const cut = approved - requested;
  const approvalPct = requested ? Math.round((approved / requested) * 100) : 0;
  const cutPct = requested ? Math.round((Math.abs(cut) / requested) * 100) : 0;
  const countLabel = adjustments.length === 1 ? "movimentação" : "movimentações";
  const scopeLabel = `${data?.scope?.path?.join(" › ") || "Toda a organização"} · ${data?.scope?.included_departments || 0} linhas`;

  const sentence = requested
    ? `A defesa pediu ${money(requested)} e aprovamos ${money(approved)}. O vigente hoje é ${money(current)}.`
    : "Ainda não há pedido de defesa registrado para este ano.";

  return <Box sx={{ minWidth: 0 }}>
    <Stack direction={{ xs: "column", md: "row" }} gap={2} alignItems={{ md: "center" }} justifyContent="space-between" sx={{ mb: 3 }}>
      <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 120 }}><InputLabel>Ano</InputLabel><Select label="Ano" value={String(year)} onChange={e => setYear(Number(e.target.value))}>{[2025, 2026, 2027].map(value => <MenuItem key={value} value={String(value)}>{value}</MenuItem>)}</Select></FormControl>
        <Box sx={{ minWidth: 300 }}><DepartmentTreePicker departments={departments} value={departmentId} onChange={setDepartmentId} allowClear clearLabel="Visão global" label="Organograma" helperText="" /></Box>
      </Stack>
      <Typography variant="body2" color="text.secondary">{scopeLabel}</Typography>
    </Stack>

    {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}

    <Typography variant="h4" fontWeight={800} sx={{ letterSpacing: "-0.01em" }}>Livro do orçamento {year}</Typography>
    <Typography color="text.secondary" sx={{ mt: .5, mb: 3 }}>{sentence}</Typography>

    <Paper variant="outlined" sx={{ display: "grid", gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, minmax(0, 1fr))" }, overflow: "hidden", mb: 2 }}>
      <Stat label="Pedido da defesa" value={requested} caption={`Elaborado em ${year - 1}`} />
      <Stat label="Aprovado" value={approved} color={colors.accent} caption={requested ? `${approvalPct}% do pedido` : "Sem aprovação registrada"} divider />
      <Stat label="Cortes" value={cut} color={chartSemantic.negative} caption={cut < 0 ? `${cutPct}% do pedido` : "Nenhum corte registrado"} divider />
      <Stat label="Vigente" value={current} color={colors.brand} caption={`${adjustments.length} ${countLabel} após a carga`} divider>
        <Spark values={monthly} label={`Orçamento vigente por mês em ${year}`} />
      </Stat>
    </Paper>

    <Paper variant="outlined" sx={{ overflow: "hidden", mb: 2 }}>
      <Box sx={{ px: 2, py: 1.5, borderBottom: `1px solid ${colors.border}` }}>
        <Typography fontWeight={800}>Áreas</Typography>
        <Typography variant="body2" color="text.secondary">{areas.length ? "Orçamento vigente de cada área abaixo do nó selecionado, com a tendência mensal." : "Selecione uma diretoria no organograma para ver as áreas abaixo dela."}</Typography>
      </Box>
      {!areas.length
        ? <Typography color="text.secondary" sx={{ p: 2 }}>Nenhuma área abaixo deste nó.</Typography>
        : <Box sx={{ overflowX: "auto" }}>
          <Table size="small" sx={{ minWidth: 720 }}>
            <TableHead><TableRow sx={{ "& th": { bgcolor: colors.tableHead, color: colors.textSecondary, fontWeight: 600, fontSize: 12 } }}><TableCell>Área</TableCell><TableCell>Tendência mensal</TableCell><TableCell align="right">Carga inicial</TableCell><TableCell align="right">Vigente</TableCell><TableCell align="right">Variação</TableCell><TableCell>Situação</TableCell></TableRow></TableHead>
            <TableBody>{areas.map(area => {
              const situation = area.variacao < -0.5 ? SITUATION.below : area.variacao > 0.5 ? SITUATION.above : SITUATION.flat;
              const delta = area.variacao;
              return <TableRow key={area.department_id} hover sx={{ "& td": { borderColor: colors.border } }}>
                <TableCell><Typography fontWeight={700} variant="body2">{area.name}</Typography><Typography variant="caption" color="text.secondary">{area.path.slice(0, -1).join(" › ") || "Nível raiz"}</Typography></TableCell>
                <TableCell><Spark values={area.monthly} label={`Tendência de ${area.name}`} /></TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{money(area.carga_inicial)}</TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", fontWeight: 700 }}>{money(area.vigente)}</TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", fontWeight: 700, color: delta < -0.5 ? chartSemantic.negative : delta > 0.5 ? chartSemantic.positive : colors.textMuted }}>{Math.abs(delta) < 0.5 ? "—" : `${delta < 0 ? "−" : "+"}${money(Math.abs(delta))}`}</TableCell>
                <TableCell><Pill color={situation.color}>{situation.label}</Pill></TableCell>
              </TableRow>;
            })}</TableBody>
          </Table>
        </Box>}
    </Paper>

    <Paper variant="outlined" sx={{ overflow: "hidden" }}>
      <Box sx={{ px: 2, py: 1.5, borderBottom: `1px solid ${colors.border}` }}>
        <Typography fontWeight={800}>Registro de decisões</Typography>
        <Typography variant="body2" color="text.secondary">Da mais recente para a mais antiga. Valores anuais.</Typography>
      </Box>
      {!events.length
        ? <Typography color="text.secondary" sx={{ p: 2 }}>Nenhuma movimentação encontrada para {year}.</Typography>
        : <Box sx={{ overflowX: "auto" }}>
          <Table size="small" sx={{ minWidth: 680 }}>
            <TableHead><TableRow sx={{ "& th": { bgcolor: colors.tableHead, color: colors.textSecondary, fontWeight: 600, fontSize: 12 } }}><TableCell>Data</TableCell><TableCell>Decisão</TableCell><TableCell>Tipo</TableCell><TableCell align="right">Valor anual</TableCell><TableCell align="right">Variação</TableCell></TableRow></TableHead>
            <TableBody>{events.slice().reverse().map(event => {
              const type = TYPE[event.event_type || ""] || TYPE.OUTRO;
              const origin = event.type_code === "defesa-orcamentaria" ? `Defesa ${year}` : event.type_name;
              const delta = event.delta == null ? null : Number(event.delta);
              return <TableRow key={`${event.type_code}-${event.id}`} hover sx={{ "& td": { borderColor: colors.border, verticalAlign: "top" } }}>
                <TableCell sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums", color: colors.textSecondary }}>{brDate(dateOf(event))}</TableCell>
                <TableCell><Typography fontWeight={700} variant="body2">{event.title}</Typography>{event.description && <Typography variant="caption" color="text.secondary">{event.description}</Typography>}</TableCell>
                <TableCell><Stack gap={.5} alignItems="flex-start"><Pill color={type.color}>{type.label}</Pill><Typography variant="caption" color="text.secondary">{origin}</Typography></Stack></TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{money(event.annual_total || 0)}</TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", fontWeight: 700, color: delta == null ? colors.textMuted : delta < 0 ? chartSemantic.negative : chartSemantic.positive }}>{delta == null ? "—" : `${delta < 0 ? "−" : "+"}${money(Math.abs(delta))}`}</TableCell>
              </TableRow>;
            })}</TableBody>
          </Table>
        </Box>}
    </Paper>

    <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 2 }}>Narrativa simulada: a defesa de {year} foi elaborada em {year - 1}. Os valores usam janeiro/2026 como referência numérica e não são realizados.</Typography>
  </Box>;
}
