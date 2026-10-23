import Plot from "../../components/Plot";
import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Chip, Divider, FormControl, IconButton, InputLabel, Menu, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material";
import { AddOutlined, AutoGraphOutlined, CloudOutlined, EditOutlined, EventOutlined, FileUploadOutlined, MoreVertOutlined } from "@mui/icons-material";
import PictureAsPdfOutlined from "@mui/icons-material/PictureAsPdfOutlined";
import html2canvas from "html2canvas";
import jsPDF from "jspdf";
import { getContractStoryBronzeConsumption, getContractStoryLedger, saveContractStoryLedger } from "../../services/api";

type Contract = { id: string; name: string; provider: string; agreementType: string; country: string; start: string; end: string; target: number; currency: string; summary: string };
type Monthly = { id: string; month: string; commitment: number; consumption: number; note: string; commitmentPresent?: boolean; consumptionPresent?: boolean };
type Milestone = { id: string; date: string; type: string; title: string; description: string; value: number };
type Ledger = { contracts: Contract[]; months: Record<string, Monthly[]>; milestones: Record<string, Milestone[]> };
type BronzeConsumption = {
  source: string;
  currency: string;
  totalConsumption: number;
  months: Array<{ month: string; consumptionUsd: number; invoiceRows: number }>;
  profiles: Array<{ billingAccountId: string; billingProfileId: string; billingProfileName: string; consumptionUsd: number; invoiceRows: number }>;
};

const STORAGE_KEY = "openfinops.contract-story.v1";
const seedId = "azure-brasil-mca";
const seed: Ledger = {
  contracts: [{ id: seedId, name: "Azure · compromisso no Brasil", provider: "Microsoft Azure", agreementType: "MCA", country: "Brasil", start: "2025-04", end: "2030-03", target: 100_000_000, currency: "USD", summary: "O Brasil aderiu ao acordo global Azure com objetivo de consumir US$ 100 milhões em cinco anos. A operação local separa Marketplace, contratado diretamente por TBRA com a Microsoft, dos demais serviços Azure, contratados via TCLOUD." }],
  months: { [seedId]: [] },
  milestones: { [seedId]: [
    { id: "m1", date: "2025-04", type: "Assinatura", title: "Início do compromisso global", description: "Começa a vigência do compromisso atribuído ao Brasil dentro do acordo global MCA.", value: 100_000_000 },
    { id: "m2", date: "2025-04", type: "Estrutura comercial", title: "Canais locais de contratação definidos", description: "Marketplace contratado diretamente entre TBRA e Microsoft; os demais serviços Azure seguem pela TCLOUD.", value: 0 },
  ] },
};

const readLedger = (): Ledger => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...seed, ...JSON.parse(raw) as Ledger };
  } catch { /* inicia com a história de exemplo quando o armazenamento não está disponível */ }
  return seed;
};
const money = (value: number, currency: string) => new Intl.NumberFormat("pt-BR", { style: "currency", currency, maximumFractionDigits: 0 }).format(value || 0);
const monthLabel = (value: string) => value ? new Intl.DateTimeFormat("pt-BR", { month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}-01T00:00:00Z`)) : "—";
const invoicesStillUpdating = (value: string) => {
  const now = new Date();
  const previousMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const previousMonthKey = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, "0")}`;
  return now.getDate() < 10 && value === previousMonthKey;
};
function MonthStatusLabel({ value }: { value: string }) {
  return <>{monthLabel(value)}{invoicesStillUpdating(value) && <Box component="span" sx={{ color: "warning.dark", fontWeight: 700 }}> (Atualizando invoices)</Box>}</>;
}
const monthRange = (start: string, end: string) => {
  if (!start || !end || end < start) return [];
  const [sy, sm] = start.split("-").map(Number); const [ey, em] = end.split("-").map(Number); const result: string[] = [];
  for (let year = sy, month = sm; year < ey || (year === ey && month <= em);) { result.push(`${year}-${String(month).padStart(2, "0")}`); month += 1; if (month > 12) { month = 1; year += 1; } }
  return result;
};

function Metric({ title, value, hint, color }: { title: string; value: string; hint: string; color: string }) {
  return <Card variant="outlined" sx={{ flex: "1 1 205px", borderTop: `3px solid ${color}` }}><CardContent sx={{ "&:last-child": { pb: 2 } }}><Typography variant="overline" color="text.secondary">{title}</Typography><Typography variant="h5" fontWeight={800}>{value}</Typography><Typography variant="body2" color="text.secondary">{hint}</Typography></CardContent></Card>;
}

function ProfileConsumptionChart({ profiles }: { profiles: BronzeConsumption["profiles"] }) {
  const total = profiles.reduce((sum, profile) => sum + profile.consumptionUsd, 0);
  const maximum = Math.max(1, ...profiles.map(profile => Math.abs(profile.consumptionUsd)));
  return <Box>
    {!profiles.length ? <Typography variant="body2" color="text.secondary">Ainda não há consumo por profile na base de origem.</Typography> :
      <Stack spacing={1.25}>
        {profiles.map((profile, index) => {
          const share = total > 0 ? Math.max(0, profile.consumptionUsd / total * 100) : 0;
          const relative = Math.max(0, Math.abs(profile.consumptionUsd) / maximum * 100);
          return <Paper key={`${profile.billingAccountId}:${profile.billingProfileId}`} variant="outlined" sx={{ p: { xs: 1.5, sm: 2 }, borderRadius: 2, bgcolor: "#fcfdff" }}>
            <Stack direction="row" alignItems="center" gap={{ xs: 1.25, sm: 2 }}>
              <Box sx={{ width: 34, height: 34, flexShrink: 0, display: "grid", placeItems: "center", borderRadius: "50%", bgcolor: index === 0 ? "#DCE8FF" : "#EEF3F8", color: index === 0 ? "#0F62FE" : "#526777", fontSize: 13, fontWeight: 800 }}>{String(index + 1).padStart(2, "0")}</Box>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="subtitle2" fontWeight={700} sx={{ overflowWrap: "anywhere" }}>{profile.billingProfileName}</Typography>
                <Typography variant="caption" color="text.secondary">Billing profile · {profile.billingProfileId}</Typography>
              </Box>
              <Box sx={{ textAlign: "right", flexShrink: 0 }}>
                <Typography variant="subtitle2" fontWeight={800}>{money(profile.consumptionUsd, "USD")}</Typography>
                <Typography variant="caption" color="text.secondary">{share.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% do total</Typography>
              </Box>
            </Stack>
            <Box sx={{ height: 8, borderRadius: 10, bgcolor: "#E1E8F0", overflow: "hidden", mt: 1.5 }}>
              <Box sx={{ width: `${relative}%`, height: "100%", bgcolor: "primary.main", borderRadius: 10, transition: "width .3s" }} />
            </Box>
          </Paper>;
        })}
      </Stack>}
  </Box>;
}

function ConsumptionChart({ contract, entries }: { contract: Contract; entries: Monthly[] }) {
  const contractPeriods = monthRange(contract.start, contract.end);
  const byMonth = new Map(entries.map(row => [row.month, row]));
  const cumulativeByMonth = new Map<string, number>();
  let cumulative = 0;
  const actualPeriods = contractPeriods.filter(month => byMonth.get(month)?.consumptionPresent !== false && byMonth.has(month));
  const latestActual = actualPeriods[actualPeriods.length - 1];
  for (const month of contractPeriods) {
    const row = byMonth.get(month);
    if (row && row.consumptionPresent !== false) cumulative += row.consumption;
    cumulativeByMonth.set(month, cumulative);
  }
  const latestCumulative = latestActual ? cumulativeByMonth.get(latestActual) || 0 : 0;
  const elapsedActualPeriods = latestActual ? contractPeriods.indexOf(latestActual) + 1 : 0;
  const observedMonthlyPace = elapsedActualPeriods > 0 ? latestCumulative / elapsedActualPeriods : 0;
  let estimatedMonth: string | null = null;
  if (contract.target > 0 && latestCumulative >= contract.target) {
    estimatedMonth = contractPeriods.find(month => (cumulativeByMonth.get(month) || 0) >= contract.target) || null;
  } else if (contract.target > 0 && observedMonthlyPace > 0 && latestActual) {
    const additionalPeriods = Math.ceil((contract.target - latestCumulative) / observedMonthlyPace);
    const [year, month] = latestActual.split("-").map(Number);
    const estimateDate = new Date(Date.UTC(year, month - 1 + additionalPeriods, 1));
    estimatedMonth = `${estimateDate.getUTCFullYear()}-${String(estimateDate.getUTCMonth() + 1).padStart(2, "0")}`;
  }
  const chartEnd = estimatedMonth && estimatedMonth > contract.end ? estimatedMonth : contract.end;
  const periods = monthRange(contract.start, chartEnd);
  const points = periods.map((month, index) => {
    const row = byMonth.get(month);
    const actual = entries.filter(item => item.month <= month && item.consumptionPresent !== false).reduce((sum, item) => sum + item.consumption, 0);
    const contractIndex = contractPeriods.indexOf(month);
    const target = contractIndex < 0 || !contractPeriods.length ? contract.target : Math.min(((contractIndex + 1) / contractPeriods.length) * contract.target, contract.target);
    return { month, x: periods.length <= 1 ? 0 : index / (periods.length - 1) * 100, target, actual, row };
  });
  return <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, mb: 2, overflow: "hidden" }}>
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" gap={1} sx={{ mb: 2 }}><Box><Typography variant="h6" fontWeight={800}>Evolução do compromisso</Typography><Typography variant="body2" color="text.secondary">Consumo no Brasil acumulado comparado à trajetória necessária para atingir 100%.</Typography></Box><Chip size="small" icon={<AutoGraphOutlined />} label={estimatedMonth ? <>Forecast commitment · {monthLabel(estimatedMonth)}</> : latestActual ? <>Atualizado até <MonthStatusLabel value={latestActual} /></> : "Aguardando consumo mensal"} color={estimatedMonth ? "error" : latestActual && invoicesStillUpdating(latestActual) ? "warning" : "default"} variant="outlined" /></Stack>
    {!entries.length ? <Alert severity="info">Ainda não há consumo com valor registrado no período. Registre um mês de consumo para acompanhar o compromisso.</Alert> : <>
      <Plot
        data={[
          { type: "scatter", mode: "lines", name: "Meta contratual", x: points.map(point => `${point.month}-01`), y: points.map(point => point.target), line: { dash: "dash", width: 2 }, hovertemplate: "%{x|%m/%Y}<br>Meta: %{y:,.2f}<extra></extra>" },
          { type: "scatter", mode: "lines+markers", name: "Consumo acumulado", x: points.map(point => `${point.month}-01`), y: points.map(point => point.actual), marker: { size: points.map(point => point.row ? 5 : 0) }, hovertemplate: "%{x|%m/%Y}<br>Consumo: %{y:,.2f}<extra></extra>" },
        ]}
        layout={{
          height: 320, margin: { l: 72, r: 24, t: 40, b: 80 }, hovermode: "x unified",
          xaxis: { type: "date", tickformat: "%m/%Y", nticks: 8, showgrid: false },
          yaxis: { tickprefix: contract.currency === "USD" ? "US$ " : "R$ ", tickformat: ".2s" },
          ...(estimatedMonth ? {
            shapes: [{ type: "line", xref: "x", yref: "paper", x0: `${estimatedMonth}-01`, x1: `${estimatedMonth}-01`, y0: 0, y1: 1, line: { color: "#da1e28", width: 2, dash: "dot" } }],
            annotations: [{ x: `${estimatedMonth}-01`, y: 1, yref: "paper", text: `Atingimento estimado: ${monthLabel(estimatedMonth)}`, showarrow: false, yshift: 16, font: { color: "#da1e28" } }],
          } : {}),
        }}
      />
      {!estimatedMonth && <Typography variant="caption" color="text.secondary">Sem ritmo positivo para estimar a data de atingimento.</Typography>}
    </>}
  </Paper>;
}

export default function ContractStoryDashboard() {
  const [ledger, setLedger] = useState<Ledger>(readLedger);
  const [ledgerLoaded, setLedgerLoaded] = useState(false);
  const [bronzeConsumption, setBronzeConsumption] = useState<BronzeConsumption | null>(null);
  const [bronzeLoading, setBronzeLoading] = useState(true);
  const [bronzeError, setBronzeError] = useState(false);
  const [selectedId, setSelectedId] = useState(seedId);
  const [dialog, setDialog] = useState<"contract" | "monthly" | "milestone" | null>(null);
  const [creatingContract, setCreatingContract] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [message, setMessage] = useState("");
  const [exporting, setExporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const exportRootRef = useRef<HTMLDivElement>(null);
  const contract = ledger.contracts.find(item => item.id === selectedId) || ledger.contracts[0];
  const ledgerEntries = ledger.months[contract?.id] || [];
  const entries = useMemo(() => {
    if (contract?.id !== seedId) return ledgerEntries;
    const actualByMonth = new Map((bronzeConsumption?.months || []).map(row => [row.month, row]));
    const manualCommitments = new Map(ledgerEntries.filter(row => row.commitment > 0).map(row => [row.month, row]));
    const months = new Set([...actualByMonth.keys(), ...manualCommitments.keys()]);
    return [...months].filter(month => month >= contract.start && month <= contract.end).sort().map(month => {
      const actual = actualByMonth.get(month);
      const commitment = manualCommitments.get(month);
      return {
        id: `bronze-${month}`, month, commitment: commitment?.commitment || 0,
        consumption: actual?.consumptionUsd || 0, note: commitment?.note || "",
        commitmentPresent: Boolean(commitment), consumptionPresent: Boolean(actual),
      };
    });
  }, [bronzeConsumption, contract, ledgerEntries]);
  const milestones = ledger.milestones[contract?.id] || [];
  const isBronzeContract = contract?.id === seedId;
  const used = entries.reduce((sum, row) => sum + (row.consumptionPresent === false ? 0 : row.consumption), 0);
  const hasActualData = !isBronzeContract || !bronzeLoading && !bronzeError;
  const pct = contract?.target && hasActualData ? Math.min(used / contract.target * 100, 100) : 0;
  const elapsedPeriods = Math.max(monthRange(contract.start, contract.end).filter(month => month <= new Date().toISOString().slice(0, 7)).length, 1);
  const forecast = used / elapsedPeriods * monthRange(contract.start, contract.end).length;
  const orderedEntries = useMemo(() => [...entries].sort((a, b) => a.month.localeCompare(b.month)), [entries]);
  const orderedMilestones = useMemo(() => [...milestones].sort((a, b) => a.date.localeCompare(b.date)), [milestones]);
  const visibleProfiles = useMemo(() => (bronzeConsumption?.profiles || []).filter(profile => profile.consumptionUsd !== 0), [bronzeConsumption]);

  useEffect(() => {
    let active = true;
    void getContractStoryLedger().then(async (saved: Ledger | null) => {
      if (!active) return;
      if (saved) {
        setLedger({ ...seed, ...saved });
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...seed, ...saved }));
      } else {
        const local = readLedger();
        const hasLocalData = Object.values(local.months).some(rows => rows.length > 0)
          || Object.values(local.milestones).some(rows => rows.length > 2)
          || local.contracts.length > seed.contracts.length;
        if (hasLocalData) {
          const migrated = await saveContractStoryLedger(local);
          if (!active) return;
          setLedger({ ...seed, ...migrated });
          localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...seed, ...migrated }));
        }
      }
      if (active) setLedgerLoaded(true);
    }).catch(() => {
      if (active) {
        setMessage("Não foi possível carregar o histórico compartilhado. Entre novamente no sistema para sincronizar os dados.");
        setLedgerLoaded(false);
      }
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    void getContractStoryBronzeConsumption().then((result: BronzeConsumption) => {
      if (active) { setBronzeConsumption(result); setBronzeError(false); }
    }).catch(() => { if (active) setBronzeError(true); })
      .finally(() => { if (active) setBronzeLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!ledgerLoaded) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ledger));
    void saveContractStoryLedger(ledger).then((saved: Ledger) => {
      const canonical = { ...seed, ...saved };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(canonical));
      setMessage("");
      setLedger(current => JSON.stringify(current) === JSON.stringify(canonical) ? current : canonical);
    }).catch(() => {
      setMessage("Não foi possível sincronizar com o servidor. Os dados continuam salvos neste navegador; entre novamente no sistema para tentar outra vez.");
    });
  }, [ledger, ledgerLoaded]);

  const saveContract = (event: ChangeEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const id = creatingContract ? crypto.randomUUID() : contract.id;
    const item: Contract = { id, name: String(form.get("name")), provider: String(form.get("provider")), agreementType: String(form.get("agreementType")), country: String(form.get("country")), start: String(form.get("start")), end: String(form.get("end")), target: Number(form.get("target")), currency: String(form.get("currency")), summary: String(form.get("summary")) };
    setLedger(current => ({ ...current, contracts: current.contracts.some(value => value.id === id) ? current.contracts.map(value => value.id === id ? item : value) : [...current.contracts, item], months: { ...current.months, [id]: current.months[id] || [] }, milestones: { ...current.milestones, [id]: current.milestones[id] || [] } })); setSelectedId(id); setCreatingContract(false); setDialog(null); setMessage("Contrato gravado. A linha do tempo já está disponível nesta tela.");
  };
  const saveMonthly = (event: ChangeEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const month = String(form.get("month"));
    const row: Monthly = { id: crypto.randomUUID(), month, commitment: Number(form.get("commitment") || 0), consumption: Number(form.get("consumption") || 0), note: String(form.get("note") || "") };
    setLedger(current => ({ ...current, months: { ...current.months, [contract.id]: [...(current.months[contract.id] || []).filter(value => value.month !== month), row] } })); setDialog(null); setMessage(`Consumo de ${monthLabel(month)} salvo e evolução recalculada.`);
  };
  const saveMilestone = (event: ChangeEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const item: Milestone = { id: crypto.randomUUID(), date: String(form.get("date")), type: String(form.get("type")), title: String(form.get("title")), description: String(form.get("description")), value: Number(form.get("value") || 0) };
    setLedger(current => ({ ...current, milestones: { ...current.milestones, [contract.id]: [...(current.milestones[contract.id] || []), item] } })); setDialog(null); setMessage("Marco incluído na linha do tempo do contrato.");
  };
  const importCsv = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    const text = await file.text(); const lines = text.split(/\r?\n/).filter(Boolean); const rows: Monthly[] = [];
    const headers = (lines.shift() || "").split(/[;,\t]/).map(value => value.trim().toLowerCase());
    const column = (...aliases: string[]) => headers.findIndex(value => aliases.includes(value));
    const monthCol = column("mes", "mês", "periodo", "período", "month"); const commitmentCol = column("commitment", "compromisso", "meta_mes", "meta do mes"); const consumptionCol = column("consumo", "consumption", "realizado", "consumo_brasil"); const noteCol = column("observacao", "observação", "nota", "note");
    if (monthCol < 0 || consumptionCol < 0) { setMessage("CSV não importado: inclua colunas de mês/período e de consumo/efetivado."); event.target.value = ""; return; }
    const number = (value: string) => Number(value.replace(/[R$\s]/g, "").replace(/\.(?=\d{3}(?:\D|$))/g, "").replace(",", ".")) || 0;
    for (const line of lines) { const cells = line.split(/[;,\t]/).map(value => value.trim().replace(/^"|"$/g, "")); const rawMonth = cells[monthCol] || ""; const month = /^\d{4}-\d{2}/.test(rawMonth) ? rawMonth.slice(0, 7) : /^\d{6}$/.test(rawMonth) ? `${rawMonth.slice(0, 4)}-${rawMonth.slice(4)}` : ""; if (!month) continue; rows.push({ id: crypto.randomUUID(), month, commitment: commitmentCol < 0 ? 0 : number(cells[commitmentCol] || "0"), consumption: number(cells[consumptionCol] || "0"), note: noteCol < 0 ? "Importado via CSV" : cells[noteCol] || "Importado via CSV" }); }
    if (rows.length) { setLedger(current => ({ ...current, months: { ...current.months, [contract.id]: [...(current.months[contract.id] || []).filter(item => !rows.some(row => row.month === item.month)), ...rows] } })); setMessage(`${rows.length} competência(s) importada(s) e evolução atualizada.`); } else setMessage("Nenhuma competência válida foi encontrada no CSV.");
    event.target.value = "";
  };

  const exportPdf = async () => {
    if (!exportRootRef.current || exporting) return;
    setExporting(true);
    try {
      const canvas = await html2canvas(exportRootRef.current, {
        scale: 1.5,
        backgroundColor: "#FAFBFD",
        useCORS: true,
        onclone: (documentClone) => {
          documentClone.querySelectorAll<HTMLElement>("[data-pdf-hide]").forEach((element) => { element.style.display = "none"; });
        },
      });
      const pdf = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4", compress: true });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 5;
      const availableWidth = pageWidth - margin * 2;
      const availableHeight = pageHeight - margin * 2;
      const scale = availableWidth / canvas.width;
      const sliceHeight = Math.max(1, Math.floor(availableHeight / scale));
      for (let sourceY = 0; sourceY < canvas.height; sourceY += sliceHeight) {
        const currentHeight = Math.min(sliceHeight, canvas.height - sourceY);
        const pageCanvas = document.createElement("canvas");
        pageCanvas.width = canvas.width;
        pageCanvas.height = currentHeight;
        const context = pageCanvas.getContext("2d");
        if (!context) throw new Error("Não foi possível montar a página do PDF.");
        context.drawImage(canvas, 0, sourceY, canvas.width, currentHeight, 0, 0, canvas.width, currentHeight);
        if (sourceY > 0) pdf.addPage();
        pdf.addImage(pageCanvas.toDataURL("image/jpeg", 0.9), "JPEG", margin, margin, availableWidth, currentHeight * scale);
      }
      pdf.save(`historia-contrato-${contract.id}.pdf`);
    } finally {
      setExporting(false);
    }
  };

  if (!contract) return null;
  return <Box ref={exportRootRef} sx={{ minWidth: 0 }}>
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={2} sx={{ mb: 2 }}>
      <Box><Typography variant="h4" fontWeight={800}>História do contrato</Typography><Typography color="text.secondary">Acordos, decisões e consumo numa linha do tempo única.</Typography></Box>
      <Stack direction="row" gap={1} alignItems="center"><Button data-pdf-hide variant="outlined" startIcon={<PictureAsPdfOutlined />} onClick={() => void exportPdf()} disabled={exporting}>{exporting ? "Gerando PDF..." : "Baixar PDF"}</Button><Button data-pdf-hide variant="contained" startIcon={<AddOutlined />} onClick={event => setMenuAnchor(event.currentTarget)}>Novo registro</Button><Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={() => setMenuAnchor(null)}><MenuItem onClick={() => { setCreatingContract(true); setMenuAnchor(null); setDialog("contract"); }}>Criar contrato</MenuItem><MenuItem onClick={() => { setMenuAnchor(null); setDialog("monthly"); }}>Anotar mês de consumo</MenuItem><MenuItem onClick={() => { setMenuAnchor(null); setDialog("milestone"); }}>Incluir marco na história</MenuItem><Divider /><MenuItem onClick={() => { setMenuAnchor(null); fileRef.current?.click(); }}><FileUploadOutlined fontSize="small" sx={{ mr: 1 }} />Carregar consumo por CSV</MenuItem></Menu><input ref={fileRef} type="file" accept=".csv,text/csv" hidden onChange={event => void importCsv(event)} /></Stack>
    </Stack>
    <FormControl size="small" sx={{ minWidth: 280, mb: 2 }} disabled={!ledger.contracts.length}>
      <InputLabel id="contract-story-select-label">Contrato</InputLabel>
      <Select labelId="contract-story-select-label" label="Contrato" value={contract.id} onChange={event => setSelectedId(String(event.target.value))}>
        {ledger.contracts.map(item => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
      </Select>
    </FormControl>
    {message && <Alert severity="success" onClose={() => setMessage("")} sx={{ mb: 2 }}>{dataDisplayText(message)}</Alert>}
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, mb: 2, background: "linear-gradient(120deg, #fff 0%, #EEF4FF 100%)" }}>
      <Stack direction={{ xs: "column", md: "row" }} gap={3} justifyContent="space-between" alignItems={{ md: "center" }}>
        <Box sx={{ maxWidth: 790 }}><Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><CloudOutlined color="primary" /><Typography variant="h5" fontWeight={800}>{contract.name}</Typography><Chip size="small" label={contract.agreementType} color="primary" variant="outlined" /><Chip size="small" label={contract.country} /></Stack><Typography sx={{ mt: 1.25, lineHeight: 1.7 }}>{contract.summary}</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Assinatura {monthLabel(contract.start)} · encerramento {monthLabel(contract.end)} · {contract.provider}</Typography></Box>
        <Stack direction="row" gap={1}><Button startIcon={<EditOutlined />} onClick={() => setDialog("contract")}>Editar contrato</Button><IconButton aria-label="Mais opções" onClick={event => setMenuAnchor(event.currentTarget)}><MoreVertOutlined /></IconButton></Stack>
      </Stack>
    </Paper>
    <Stack direction="row" gap={2} flexWrap="wrap" sx={{ mb: 2 }}>
      <Metric title="Objetivo do contrato" value={money(contract.target, contract.currency)} hint={`${monthLabel(contract.start)} – ${monthLabel(contract.end)}`} color="#0F62FE" />
      <Metric title="Consumo no Brasil anotado" value={isBronzeContract && bronzeLoading ? "Buscando…" : isBronzeContract && bronzeError ? "Indisponível" : money(used, contract.currency)} hint={`${entries.filter(row => row.consumptionPresent !== false).length} competência(s) com valor na base de origem`} color="#0F62FE" />
      <Metric title="Andamento do compromisso" value={hasActualData ? `${pct.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "—"} hint={`${hasActualData ? money(used, contract.currency) : "Consumo não disponível"} de ${money(contract.target, contract.currency)}`} color="#64748B" />
      <Metric title="Forecast no ritmo atual" value={hasActualData ? money(forecast, contract.currency) : "—"} hint={!hasActualData ? "Aguardando leitura da base de origem" : forecast >= contract.target ? "Ritmo atual alcança o objetivo" : "Forecast abaixo do objetivo total"} color={forecast >= contract.target ? "#059669" : "#d97706"} />
    </Stack>
    {isBronzeContract && bronzeError && <Alert severity="error" sx={{ mb: 2 }}>Não foi possível consultar a base de origem. Os valores de consumo não foram substituídos por estimativas; tente atualizar a página.</Alert>}
    {isBronzeContract && bronzeLoading && <Alert severity="info" sx={{ mb: 2 }}>Consultando o consumo registrado na base de origem…</Alert>}
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}><Stack direction="row" justifyContent="space-between" gap={1} alignItems="center"><Box><Typography variant="subtitle2" color="text.secondary">OBJETIVO DE CONSUMO BRASIL</Typography><Typography variant="h6" fontWeight={800}>{hasActualData ? money(used, contract.currency) : "—"} <Typography component="span" color="text.secondary" variant="body1">de {money(contract.target, contract.currency)}</Typography></Typography></Box><Typography variant="h5" fontWeight={800} color="primary.main">{hasActualData ? `${pct.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "—"}</Typography></Stack><Box sx={{ mt: 1.5, height: 12, borderRadius: 10, bgcolor: "#E1E8F0", overflow: "hidden" }}><Box sx={{ width: `${pct}%`, height: "100%", bgcolor: "#0F62FE", borderRadius: 10, transition: "width .35s" }} /></Box><Stack direction="row" justifyContent="space-between" sx={{ mt: .5 }}><Typography variant="caption" color="text.secondary">Início {monthLabel(contract.start)}</Typography><Typography variant="caption" color="text.secondary">Meta 100%</Typography><Typography variant="caption" color="text.secondary">Fim {monthLabel(contract.end)}</Typography></Stack></Paper>
    <ConsumptionChart contract={contract} entries={orderedEntries} />
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, gap: 2 }}>
      <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, flex: 1 }}><Stack direction="row" justifyContent="space-between" alignItems="start" gap={1} sx={{ mb: 1.5 }}><Box><Typography variant="h6" fontWeight={800}>Jornada do acordo</Typography><Typography variant="body2" color="text.secondary">A história fica mais completa a cada decisão registrada.</Typography></Box><Button size="small" startIcon={<AddOutlined />} onClick={() => setDialog("milestone")}>Incluir marco</Button></Stack>
        <Stack spacing={0}>{orderedMilestones.length ? orderedMilestones.map((item, index) => <Stack key={item.id} direction="row" gap={1.5} sx={{ minHeight: 86 }}><Stack alignItems="center" sx={{ width: 22, flexShrink: 0 }}><Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: index === orderedMilestones.length - 1 ? "primary.main" : "#8497A8", mt: .6, zIndex: 1 }} />{index < orderedMilestones.length - 1 && <Box sx={{ width: 2, flex: 1, bgcolor: "divider" }} />}</Stack><Box sx={{ pb: 2 }}><Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><Typography variant="caption" color="text.secondary">{monthLabel(item.date)}</Typography><Chip size="small" variant="outlined" label={item.type} /></Stack><Typography fontWeight={700}>{item.title}</Typography><Typography variant="body2" color="text.secondary">{item.description}</Typography>{item.value > 0 && <Typography variant="caption" fontWeight={700}>{money(item.value, contract.currency)}</Typography>}</Box></Stack>) : <Typography color="text.secondary">Nenhum marco registrado para este contrato.</Typography>}</Stack>
      </Paper>
      <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, flex: 1 }}><Stack direction="row" justifyContent="space-between" alignItems="start" gap={1} sx={{ mb: 1 }}><Box><Typography variant="h6" fontWeight={800}>Consumo por competência</Typography><Typography variant="body2" color="text.secondary">Efetivado da base de origem comparado ao compromisso mensal cadastrado.</Typography></Box><Button size="small" startIcon={<AddOutlined />} onClick={() => setDialog("monthly")}>Incluir mês</Button></Stack>
        {isBronzeContract && bronzeLoading ? <Alert severity="info">Carregando o efetivado mensal diretamente da base de origem.</Alert> : !orderedEntries.length ? <Alert severity="info">Ainda não há efetivado na base de origem nem compromisso mensal cadastrado neste período.</Alert> : <Box sx={{ overflowX: "auto" }}><Box sx={{ minWidth: 550 }}><Stack direction="row" sx={{ py: 1, borderBottom: 1, borderColor: "divider" }}>{["Competência", "Compromisso", "Consumo no Brasil", "Delta"].map((label, index) => <Typography key={label} variant="caption" color="text.secondary" fontWeight={700} sx={{ flex: index === 0 ? 1 : "0 0 25%", textAlign: index ? "right" : "left" }}>{label}</Typography>)}</Stack>{orderedEntries.slice().reverse().map(row => { const hasCommitment = row.commitmentPresent !== false; const hasConsumption = row.consumptionPresent !== false; const delta = row.consumption - row.commitment; return <Stack key={row.id} direction="row" sx={{ py: 1.1, borderBottom: 1, borderColor: "divider" }}><Typography variant="body2" sx={{ flex: 1 }}><MonthStatusLabel value={row.month} /></Typography><Typography variant="body2" sx={{ flex: "0 0 25%", textAlign: "right" }}>{hasCommitment ? money(row.commitment, contract.currency) : "—"}</Typography><Typography variant="body2" sx={{ flex: "0 0 25%", textAlign: "right" }}>{hasConsumption ? money(row.consumption, contract.currency) : "—"}</Typography><Typography variant="body2" fontWeight={700} color={delta >= 0 ? "success.dark" : "warning.dark"} sx={{ flex: "0 0 25%", textAlign: "right" }}>{hasCommitment && hasConsumption ? `${delta > 0 ? "+" : ""}${money(delta, contract.currency)}` : "—"}</Typography>{row.note && <Typography variant="caption" color="text.secondary" sx={{ display: "none" }}>{row.note}</Typography>}</Stack>; })}</Box></Box>}
      </Paper>
    </Box>
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, mt: 2 }}><Typography variant="h6" fontWeight={800} sx={{ mb: 2 }}>Consumo acumulado por perfil de cobrança</Typography><ProfileConsumptionChart profiles={visibleProfiles} /></Paper>
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 }, mt: 2 }}><Typography variant="h6" fontWeight={800} sx={{ mb: 2 }}>Como ler esta história</Typography><Stack spacing={1.25}><Typography variant="subtitle2" fontWeight={700}>O que os números contam</Typography><Typography variant="body2" color="text.secondary">• <b>Consumo no Brasil</b>: soma mensal das linhas de invoice guardadas na base de origem, agrupadas pelos dois billing accounts.</Typography><Typography variant="body2" color="text.secondary">• <b>Compromisso</b>: valor-meta mensal cadastrado para acompanhar o ritmo esperado.</Typography><Typography variant="body2" color="text.secondary">• <b>Delta</b>: Consumo no Brasil menos Compromisso. Positivo indica acima da meta do mês; negativo, abaixo.</Typography><Typography variant="body2" color="text.secondary">• Um traço (—) significa que não existe dado daquele tipo na competência; não significa consumo zero nem estimativa.</Typography><Divider /><Typography variant="caption" color="text.secondary">Fonte do efetivado: {bronzeConsumption?.source || "base de origem de contratos MCA"} · moeda {bronzeConsumption?.currency || "USD"} · somente valores encontrados na fonte.</Typography></Stack></Paper>

    <Dialog open={Boolean(dialog)} onClose={() => setDialog(null)} fullWidth maxWidth={dialog === "contract" ? "md" : "sm"}><Box component="form" onSubmit={dialog === "contract" ? saveContract : dialog === "monthly" ? saveMonthly : saveMilestone}>
      <DialogTitle>{dialog === "contract" ? (creatingContract ? "Criar contrato" : "Dados do contrato") : dialog === "monthly" ? "Anotar consumo mensal" : "Incluir capítulo na história"}</DialogTitle>
      <DialogContent dividers><Stack spacing={2} sx={{ pt: 1 }}>
        {dialog === "contract" ? <>
          <TextField name="name" label="Nome do contrato" required defaultValue={creatingContract ? "" : contract.name} />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}><TextField name="provider" label="Cloud provider" required defaultValue={creatingContract ? "" : contract.provider} fullWidth /><TextField name="agreementType" label="Tipo de acordo" defaultValue={creatingContract ? "MCA" : contract.agreementType} fullWidth /><TextField name="country" label="País / escopo" defaultValue={creatingContract ? "Brasil" : contract.country} fullWidth /></Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}><TextField name="start" label="Início da assinatura" type="month" required defaultValue={creatingContract ? "" : contract.start} InputLabelProps={{ shrink: true }} fullWidth /><TextField name="end" label="Fim da vigência" type="month" required defaultValue={creatingContract ? "" : contract.end} InputLabelProps={{ shrink: true }} fullWidth /></Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}><TextField name="target" label="Commitment total" type="number" required defaultValue={creatingContract ? "" : contract.target} inputProps={{ min: 0, step: "any" }} fullWidth /><FormControl fullWidth><InputLabel>Moeda</InputLabel><Select name="currency" label="Moeda" defaultValue={creatingContract ? "USD" : contract.currency}><MenuItem value="USD">USD · Dólar</MenuItem><MenuItem value="BRL">BRL · Real</MenuItem><MenuItem value="EUR">EUR · Euro</MenuItem></Select></FormControl></Stack>
          <TextField name="summary" label="Linha do tempo executiva do contrato" multiline minRows={3} defaultValue={creatingContract ? "" : contract.summary} helperText="Conte em linguagem simples o que foi contratado e como a estrutura funciona." />
        </> : dialog === "monthly" ? <>
          <Alert severity="info">O mês já existente será substituído, evitando duplicar a competência. A barra e o gráfico serão recalculados.</Alert>
          <TextField name="month" label="Competência" type="month" required defaultValue={new Date().toISOString().slice(0, 7)} InputLabelProps={{ shrink: true }} />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}><TextField name="commitment" label="Commitment do mês" type="number" inputProps={{ min: 0, step: "any" }} fullWidth /><TextField name="consumption" label="Consumo no Brasil" type="number" required inputProps={{ min: 0, step: "any" }} fullWidth /></Stack>
          <TextField name="note" label="Observação ou fonte" />
        </> : <>
          <TextField name="date" label="Data do marco" type="month" required defaultValue={new Date().toISOString().slice(0, 7)} InputLabelProps={{ shrink: true }} />
          <FormControl fullWidth><InputLabel>Tipo de evento</InputLabel><Select name="type" label="Tipo de evento" defaultValue="Decisão comercial">{["Assinatura", "Adesão", "Decisão comercial", "Mudança de canal", "Desconto ou taxa", "Revisão", "Renovação", "Outro"].map(type => <MenuItem key={type} value={type}>{type}</MenuItem>)}</Select></FormControl>
          <TextField name="title" label="Título do capítulo" required placeholder="Ex.: Marketplace passou a ser comprado diretamente" />
          <TextField name="description" label="O que aconteceu e por quê?" required multiline minRows={3} />
          <TextField name="value" label="Valor relacionado (opcional)" type="number" inputProps={{ min: 0, step: "any" }} />
        </>}
      </Stack></DialogContent>
      <DialogActions sx={{ p: 2 }}><Button onClick={() => { setDialog(null); setCreatingContract(false); }}>Cancelar</Button><Button variant="contained" type="submit" startIcon={dialog === "milestone" ? <EventOutlined /> : dialog === "monthly" ? <AutoGraphOutlined /> : <AddOutlined />}>{dialog === "contract" ? "Gravar contrato" : dialog === "monthly" ? "Gravar competência" : "Incluir na história"}</Button></DialogActions>
    </Box></Dialog>
  </Box>;
}
