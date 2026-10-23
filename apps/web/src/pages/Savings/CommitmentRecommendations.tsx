import DataGrid from "../../components/FinbaseDataGrid";
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { GridColDef } from "@mui/x-data-grid";
import { formatApiError } from "../../services/api";
import {
  CommitmentConnection,
  CommitmentProfile,
  CommitmentRecommendation,
  listCommitmentConnections,
  listCommitmentRecommendations,
  runCommitmentEngine,
  setCommitmentRecommendationState,
} from "../../services/commitmentEngine";

const HOURS_PER_YEAR = 8760;
const PROFILES: { value: CommitmentProfile; label: string }[] = [
  { value: "CONSERVATIVE", label: "Conservador" },
  { value: "BALANCED", label: "Balanceado" },
  { value: "AGGRESSIVE", label: "Agressivo" },
];
const READINESS_LABELS: Record<string, string> = {
  INSUFFICIENT_HOURLY_DATA: "Dado horário insuficiente",
  MISSING_OFFER: "Sem oferta cadastrada",
  NO_UNCOVERED_USAGE: "Uso já coberto",
  NO_ALWAYS_ON_USAGE: "Sem recurso ligado 24h",
};
const STATE_LABELS: Record<string, string> = {
  NEW: "Nova", APPROVED: "Aprovada", REJECTED: "Rejeitada", PURCHASED: "Contratada", REALIZED: "Realizada",
};

const moneyIn = (currency: string | null | undefined) => (value: unknown) =>
  new Intl.NumberFormat("pt-BR", { style: "currency", currency: currency || "BRL" }).format(Number(value || 0));
const percent = (value: unknown) =>
  value == null ? "—" : `${(Number(value) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}%`;
const dateTime = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
// The window is whole UTC days: show them as days, not shifted to local time.
const utcDay = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString("pt-BR", { timeZone: "UTC" }) : "—";

function Metric({ title, value, detail }: { title: string; value: string; detail?: string }) {
  return (
    <Card sx={{ flex: 1, minWidth: 210 }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">{title}</Typography>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>{value}</Typography>
        {detail && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{detail}</Typography>}
      </CardContent>
    </Card>
  );
}

export default function CommitmentRecommendations() {
  const [connections, setConnections] = useState<CommitmentConnection[] | null>(null);
  const [connectionId, setConnectionId] = useState<number | "">("");
  const [profile, setProfile] = useState<CommitmentProfile>("BALANCED");
  const [items, setItems] = useState<CommitmentRecommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const loadConnections = useCallback(async () => {
    const result = await listCommitmentConnections();
    setConnections(result);
    setConnectionId((current) => current || result[0]?.id || "");
  }, []);

  useEffect(() => {
    loadConnections().catch((cause) => setError(formatApiError(cause)));
  }, [loadConnections]);

  useEffect(() => {
    if (!connectionId) return;
    let active = true;
    setLoading(true);
    listCommitmentRecommendations(connectionId)
      .then((result) => { if (active) setItems(result.items); })
      .catch((cause) => { if (active) setError(formatApiError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [connectionId]);

  const connection = connections?.find((item) => item.id === connectionId);
  const run = connection?.latest_run;

  const execute = async () => {
    if (!connectionId) return;
    setRunning(true);
    setError("");
    try {
      await runCommitmentEngine(connectionId);
      await loadConnections();
      setItems((await listCommitmentRecommendations(connectionId)).items);
    } catch (cause) {
      setError(formatApiError(cause));
    } finally {
      setRunning(false);
    }
  };

  const changeState = async (row: CommitmentRecommendation, state: "NEW" | "APPROVED" | "REJECTED") => {
    try {
      const updated = await setCommitmentRecommendationState(row.id, state);
      setItems((current) => current.map((item) => (item.id === row.id ? { ...item, ...updated } : item)));
    } catch (cause) {
      setError(formatApiError(cause));
    }
  };

  const proposals = items.filter((item) => item.profile === profile && item.action_type === "COMMIT");
  const counted = proposals.filter((item) => item.state !== "REJECTED");
  const notReady = items.filter((item) => item.profile === "NONE");
  const currency = proposals[0]?.currency || run?.currency || "BRL";
  const money = moneyIn(currency);
  const sum = (pick: (item: CommitmentRecommendation) => number | null) =>
    counted.reduce((total, item) => total + Number(pick(item) || 0), 0);

  const columns: GridColDef<CommitmentRecommendation>[] = [
    {
      field: "instrument_label", headerName: "Compromisso", minWidth: 240, flex: 1,
      renderCell: ({ row }) => (
        <Tooltip title={[...row.rationale, ...row.risks].join(" ")}>
          <span>{row.instrument_label}</span>
        </Tooltip>
      ),
    },
    {
      field: "purchase_path", headerName: "Contratação", width: 150,
      renderCell: ({ row }) => row.purchase_path === "negotiated"
        ? <Chip size="small" color="warning" label="Negociação" />
        : <Chip size="small" color="primary" label="Self-service" />,
    },
    { field: "term_months", headerName: "Prazo", width: 90, valueFormatter: (value) => (value ? `${value} meses` : "—") },
    { field: "discount_pct", headerName: "Desconto", width: 100, valueFormatter: (value) => percent(value) },
    {
      field: "commit_cost_hour", headerName: "Compromisso / ano", width: 160,
      valueGetter: (_value, row) => Number(row.commit_cost_hour || 0) * HOURS_PER_YEAR, valueFormatter: (value) => money(value),
    },
    { field: "monthly_saving", headerName: "Saving / mês", width: 140, valueFormatter: (value) => money(value) },
    { field: "term_saving", headerName: "Saving no prazo", width: 150, valueFormatter: (value) => money(value) },
    { field: "expected_utilization", headerName: "Uso previsto", width: 115, valueFormatter: (value) => percent(value) },
    { field: "saving_if_usage_drops", headerName: "Se o uso cair 20%", width: 150, valueFormatter: (value) => money(value) },
    { field: "confidence", headerName: "Confiança", width: 105 },
    {
      field: "state", headerName: "Estado", width: 230, sortable: false,
      renderCell: ({ row }) => (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ height: "100%" }}>
          <Typography variant="body2" sx={{ minWidth: 70 }}>{STATE_LABELS[row.state] || row.state}</Typography>
          {row.state !== "APPROVED" && ["NEW", "REJECTED"].includes(row.state) &&
            <Button size="small" onClick={() => changeState(row, "APPROVED")}>Aprovar</Button>}
          {row.state !== "REJECTED" && ["NEW", "APPROVED"].includes(row.state) &&
            <Button size="small" color="inherit" onClick={() => changeState(row, "REJECTED")}>Rejeitar</Button>}
        </Stack>
      ),
    },
  ];

  if (connections === null && !error) {
    return <Stack alignItems="center" sx={{ py: 4 }}><CircularProgress size={24} /></Stack>;
  }

  return (
    <Box>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} gap={2} mb={1}>
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Recomendações do motor de compromissos</Typography>
          <Typography variant="body2" color="text.secondary">
            Saving gerado se a proposta for executada, calculado da fatura horária. O motor só recomenda; nada é comprado.
          </Typography>
        </Box>
        {connections && connections.length > 0 && (
          <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} alignItems={{ sm: "center" }}>
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel>Conexão</InputLabel>
              <Select label="Conexão" value={connectionId} onChange={(event) => setConnectionId(Number(event.target.value))}>
                {connections.map((item) => (
                  <MenuItem key={item.id} value={item.id}>{item.name} ({item.provider.toUpperCase()})</MenuItem>
                ))}
              </Select>
            </FormControl>
            <ToggleButtonGroup size="small" exclusive value={profile} onChange={(_event, value) => value && setProfile(value)}>
              {PROFILES.map((item) => <ToggleButton key={item.value} value={item.value}>{item.label}</ToggleButton>)}
            </ToggleButtonGroup>
            <Button variant="contained" size="small" disabled={!connectionId || running} onClick={execute}>
              {running ? "Analisando…" : "Executar análise"}
            </Button>
          </Stack>
        )}
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {connections && connections.length === 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Nenhuma conexão de faturamento com dado horário. Hoje o motor lê a OCI pelos relatórios de custo (CSV).
        </Alert>
      )}
      {connection && !run && (
        <Alert severity="info" sx={{ mb: 2 }}>Nenhuma análise executada para {connection.name}. Use "Executar análise".</Alert>
      )}
      {run && (
        <Typography variant="body2" color="text.secondary" mb={1.5}>
          Última análise: {dateTime(run.finished_at)} · janela de {utcDay(run.window_start_utc)} a {utcDay(run.window_end_utc)}
          {run.status === "COMPLETED" && ` · ${run.keys_analyzed} chave(s) analisada(s)`}
        </Typography>
      )}
      {run?.status === "FAILED" && <Alert severity="warning" sx={{ mb: 2 }}>A última análise não terminou: {run.error_message}</Alert>}

      {connection && items.length > 0 && <>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <Metric title="Saving / mês se executado" value={money(sum((item) => item.monthly_saving))}
                  detail={`Perfil ${PROFILES.find((item) => item.value === profile)?.label.toLowerCase()} · sem as rejeitadas`} />
          <Metric title="Saving no prazo" value={money(sum((item) => item.term_saving))} detail="Soma do prazo de cada proposta" />
          <Metric title="Compromisso / ano" value={money(sum((item) => Number(item.commit_cost_hour || 0) * HOURS_PER_YEAR))}
                  detail="Valor a contratar ou negociar" />
          <Metric title="Propostas" value={String(proposals.length)} detail={`${counted.length} em aberto ou aprovadas`} />
        </Stack>

        {proposals.some((item) => item.purchase_path === "negotiated") && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {proposals.find((item) => item.purchase_path === "negotiated")?.process_note}
          </Alert>
        )}

        <Card sx={{ mb: 2 }}><CardContent>
          <DataGrid
            rows={proposals}
            columns={columns}
            loading={loading}
            autoHeight
            hideFooter
            disableRowSelectionOnClick
            initialState={{ sorting: { sortModel: [{ field: "monthly_saving", sort: "desc" }] } }}
            localeText={{ noRowsLabel: "Nenhuma proposta com economia neste perfil." }}
          />
        </CardContent></Card>

        {notReady.length > 0 && (
          <Card sx={{ mb: 2 }}><CardContent>
            <Typography variant="subtitle2" fontWeight={600} mb={1}>Sem proposta</Typography>
            <Stack spacing={0.75}>
              {notReady.map((item) => (
                <Typography key={item.id} variant="body2">
                  <strong>{item.instrument_label}</strong> — {READINESS_LABELS[item.readiness_status] || item.readiness_status}: {item.rationale[0]}
                </Typography>
              ))}
            </Stack>
          </CardContent></Card>
        )}
      </>}
    </Box>
  );
}
