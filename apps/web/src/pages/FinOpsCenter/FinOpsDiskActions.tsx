import { useEffect, useState } from "react";
import Plot from "@/components/Plot";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogContent, DialogTitle, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { FinOpsDiskAction, FinOpsDiskActions as DiskData, getFinopsDiskActionDetail, getFinopsDiskActions } from "../../services/api";

const money = (v: number | null, ccy: string | null) => v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: ccy || "BRL", maximumFractionDigits: 2 });
const dateTime = (v: string | null) => v ? new Date(v).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" }) : "—";
const metricName: Record<string, string> = {
  "Composite Disk Read Operations/sec": "Leitura IOPS",
  "Composite Disk Write Operations/sec": "Gravação IOPS",
  "Composite Disk Read Bytes/sec": "Leitura throughput",
  "Composite Disk Write Bytes/sec": "Gravação throughput",
};
function withHourlyGaps(rows: Array<{ bucket_start_utc: string; value: number }>) {
  const x: string[] = []; const y: Array<number | null> = [];
  let previous: number | null = null;
  for (const row of [...rows].sort((a, b) => a.bucket_start_utc.localeCompare(b.bucket_start_utc))) {
    const current = Date.parse(row.bucket_start_utc);
    if (previous != null && current - previous > 90 * 60 * 1000) {
      x.push(new Date(previous + 60 * 60 * 1000).toISOString()); y.push(null);
    }
    x.push(row.bucket_start_utc); y.push(Number(row.value)); previous = current;
  }
  return { x, y };
}

export default function FinOpsDiskActions() {
  const [data, setData] = useState<DiskData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<FinOpsDiskAction | null>(null);
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof getFinopsDiskActionDetail>> | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  useEffect(() => { getFinopsDiskActions().then(setData).catch(() => setError("Não foi possível carregar as ações de disco.")); }, []);
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let current = true;
    setLoadingDetail(true);
    getFinopsDiskActionDetail(selected.resource_id).then((value) => { if (current) setDetail(value); }).catch(() => { if (current) setDetail(null); }).finally(() => { if (current) setLoadingDetail(false); });
    return () => { current = false; };
  }, [selected]);
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 6 }} />;
  const waitingForMetrics = data.disks_with_direct_metrics === 0;
  return <Box>
    <Typography variant="h6" sx={{ mb: 0.5 }}>Ações de armazenamento</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
      Comparação entre HDD padrão, SSD padrão, SSD Premium e Premium SSD v2. A análise cruza telemetria, inventário e faturamento pelo resource ID.
    </Typography>
    {waitingForMetrics && <Alert severity="warning" sx={{ mb: 2 }}>
      Ainda não há telemetria vinculada diretamente aos resource IDs dos managed disks nesta janela. As séries atuais de IOPS são agregadas por VM e não permitem decidir qual disco mudar. A coleta foi ampliada para capturar métricas no próprio disco; após a próxima execução do Azure Monitor, esta lista poderá gerar opções com base no p95 por disco.
    </Alert>}
    <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
      <Card sx={{ minWidth: 180, flex: 1 }}><CardContent><Typography variant="caption" color="text.secondary">Discos no inventário</Typography><Typography variant="h4" fontWeight={700}>{data.total_disks.toLocaleString("pt-BR")}</Typography></CardContent></Card>
      <Card sx={{ minWidth: 180, flex: 1 }}><CardContent><Typography variant="caption" color="text.secondary">Com IOPS/throughput próprios</Typography><Typography variant="h4" fontWeight={700}>{data.disks_with_direct_metrics.toLocaleString("pt-BR")}</Typography></CardContent></Card>
      <Card sx={{ minWidth: 180, flex: 1 }}><CardContent><Typography variant="caption" color="text.secondary">Comparáveis por capacidade e p95</Typography><Typography variant="h4" fontWeight={700}>{data.disks_ready_for_comparison.toLocaleString("pt-BR")}</Typography></CardContent></Card>
    </Stack>
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
      {Object.entries(data.tier_counts).map(([tier, count]) => <Chip key={tier} variant="outlined" label={`${tier}: ${count.toLocaleString("pt-BR")}`} />)}
    </Stack>
    {data.notes.map((note) => <Typography key={note} variant="caption" color="text.secondary" display="block" sx={{ mb: 0.5 }}>• {note}</Typography>)}
    <Card sx={{ mt: 2 }}><CardContent>
      <Typography variant="subtitle1" fontWeight={700}>Discos e opções que atendem à demanda observada</Typography>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        Métricas por disco · {data.metric_window.observed_days ?? 0} de {data.metric_window.requested_days} dias com amostras ({data.metric_window.from_day}–{data.metric_window.to_day}) · faturamento de {data.billing_period ?? "—"}
      </Typography>
      <Table size="small"><TableHead><TableRow>
        <TableCell>Disco</TableCell><TableCell>Tier atual</TableCell><TableCell align="right">Tamanho</TableCell><TableCell align="right">IOPS p95</TableCell><TableCell align="right">Throughput p95</TableCell><TableCell>Opções com 25% de folga</TableCell><TableCell align="right">Custo atual</TableCell>
      </TableRow></TableHead><TableBody>
        {data.actions.map((disk) => <TableRow key={disk.resource_id} hover onClick={() => setSelected(disk)} sx={{ cursor: "pointer" }}>
          <TableCell><Typography variant="body2" fontWeight={600}>{disk.resource_name || disk.resource_id}</Typography><Typography variant="caption" color="text.secondary">{disk.region || "região —"}</Typography></TableCell>
          <TableCell>{disk.tier}</TableCell><TableCell align="right">{disk.size_gib == null ? "Tamanho não coletado" : `${disk.size_gib} GiB`}</TableCell>
          <TableCell align="right">{disk.total_iops_p95_conservative == null ? "—" : `${disk.total_iops_p95_conservative.toLocaleString("pt-BR")} IOPS`}</TableCell>
          <TableCell align="right">{disk.throughput_mbps_p95_conservative == null ? "—" : `${disk.throughput_mbps_p95_conservative.toLocaleString("pt-BR")} MB/s`}</TableCell>
          <TableCell>{disk.compatible_target_tiers.length ? disk.compatible_target_tiers.map((x) => `${x.tier} ${x.redundancy || ""}${x.provisioned_iops ? ` (${x.provisioned_iops} IOPS)` : ""}`).join(", ") : <Chip size="small" label={disk.action_state === "NO_SAFE_TARGET" ? "Sem tier seguro" : "Aguardando dados"} />}</TableCell>
          <TableCell align="right">{money(disk.effective_cost, disk.currency)}</TableCell>
        </TableRow>)}
        {!data.actions.length && <TableRow><TableCell colSpan={7}>Nenhum managed disk no inventário atual.</TableCell></TableRow>}
      </TableBody></Table>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>Custo observado do período é referência; o saving de uma mudança exige preço comparável para SKU, região, redundância e desempenho configurado.</Typography>
    </CardContent></Card>
    <Dialog open={!!selected} onClose={() => setSelected(null)} fullWidth maxWidth="lg">
      <DialogTitle>Perfil de disco · {selected?.resource_name || ""}</DialogTitle><DialogContent dividers>
        {loadingDetail && <CircularProgress />}
        {detail && <Stack spacing={2}>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap"><Chip label={`Tier ${selected?.tier}`} /><Chip label={`Tamanho ${selected?.size_gib ?? "—"} GiB`} /><Chip label={`Região ${selected?.region ?? "—"}`} /><Chip label={`${detail.metric_window.observed_days ?? 0}/${detail.metric_window.requested_days} dias · ${detail.metric_window.from_day}–${detail.metric_window.to_day}`} /></Stack>
          {detail.series.length ? <>
            <Typography variant="subtitle2">Operações de entrada/saída · IOPS</Typography>
            <Plot data={Object.entries(metricName).filter(([metric]) => metric.includes("Operations/sec")).map(([metric, name]) => {
              const rows = detail.series.filter((row) => row.metric === metric);
              const points = withHourlyGaps(rows);
              return { type: "scatter", mode: "lines", connectgaps: false, name, x: points.x, y: points.y };
            }).filter((trace) => trace.x.length)} layout={{ autosize: true, height: 300, margin: { l: 65, r: 20, t: 20, b: 55 }, xaxis: { title: "Horário UTC" }, yaxis: { title: "Operações por segundo (IOPS)", rangemode: "tozero" }, legend: { orientation: "h", y: -0.2 } }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} />
            <Typography variant="subtitle2">Volume transferido · MB/s</Typography>
            <Plot data={Object.entries(metricName).filter(([metric]) => metric.includes("Bytes/sec")).map(([metric, name]) => {
              const rows = detail.series.filter((row) => row.metric === metric);
              const points = withHourlyGaps(rows);
              return { type: "scatter", mode: "lines", connectgaps: false, name, x: points.x, y: points.y.map((value) => value == null ? null : value / 1_000_000) };
            }).filter((trace) => trace.x.length)} layout={{ autosize: true, height: 300, margin: { l: 65, r: 20, t: 20, b: 55 }, xaxis: { title: "Horário UTC" }, yaxis: { title: "Megabytes por segundo (MB/s)", rangemode: "tozero" }, legend: { orientation: "h", y: -0.2 } }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} />
          </> : <Alert severity="info">Sem métrica direta deste disco na janela de {detail.metric_window_days} dias. Ações por disco não usam a série agregada da VM.</Alert>}
          <Button onClick={() => setSelected(null)} sx={{ alignSelf: "flex-end" }}>Fechar</Button>
        </Stack>}
      </DialogContent>
    </Dialog>
  </Box>;
}
