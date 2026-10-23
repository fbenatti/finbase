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
  TextField,
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
  createNegotiatedOffer,
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
  INSUFFICIENT_HOURLY_DATA: "Dado insuficiente",
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

// Spend commitments are negotiated: the saving needs the discount of the deal, never an assumed one.
function NegotiatedOfferForm({ item, onSaved }: { item: CommitmentRecommendation; onSaved: () => Promise<void> }) {
  const [discount, setDiscount] = useState("");
  const [term, setTerm] = useState(12);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Accept "15", "15%", "15,5" or "15.5 %".
  const value = Number(discount.replace("%", "").replace(",", ".").trim());
  const valid = discount.trim() !== "" && value > 0 && value < 100;
  const invalid = discount.trim() !== "" && !valid;

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await createNegotiatedOffer({ instrument: item.instrument, term_months: term, discount_pct: value / 100 });
      await onSaved();
    } catch (cause) {
      setError(formatApiError(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
      <TextField size="small" label="Desconto negociado (%)" value={discount} sx={{ width: 190 }}
                 onChange={(event) => setDiscount(event.target.value)} inputProps={{ inputMode: "decimal" }}
                 onKeyDown={(event) => { if (event.key === "Enter" && valid && !saving) void save(); }}
                 error={invalid} helperText={invalid ? "Informe um percentual entre 0 e 100" : undefined} />
      <FormControl size="small" sx={{ width: 130 }}>
        <InputLabel>Prazo</InputLabel>
        <Select label="Prazo" value={term} onChange={(event) => setTerm(Number(event.target.value))}>
          {[12, 36].map((months) => <MenuItem key={months} value={months}>{months} meses</MenuItem>)}
        </Select>
      </FormControl>
      <Button size="small" variant="outlined" disabled={!valid || saving} onClick={save}>
        {saving ? "Salvando…" : "Salvar e reanalisar"}
      </Button>
      {error && <Typography variant="body2" color="error">{error}</Typography>}
    </Stack>
  );
}

const SIMULATED_DISCOUNTS = [0.05, 0.10, 0.15, 0.20, 0.25];
// Share of the projected consumption each profile commits (same policy as the engine):
// conservative survives a 20% drop, balanced keeps a 10% margin, aggressive commits it all.
const PROFILE_SHARE: Record<CommitmentProfile, number> = { CONSERVATIVE: 0.8, BALANCED: 0.9, AGGRESSIVE: 1.0 };
const USAGE_DROP = 0.2;

// How a spend pool works, in the customer's words: it is a negotiation, not a purchase in the console.
const SPEND_POOL_GUIDE: Record<string, { title: string; points: string[] }> = {
  oci_universal_credits: {
    title: "Como funciona o Universal Credits da OCI",
    points: [
      "Você se compromete com um valor anual de consumo na OCI (os créditos), num contrato de 1 a 3 anos fechado com a Oracle.",
      "Em troca, a Oracle aplica um desconto sobre o preço dos serviços da OCI (compute, storage, rede, banco…), não só de compute. O percentual depende do valor comprometido e da negociação.",
      "O consumo do ano abate os créditos já com o desconto. Crédito não usado até o fim do ano se perde; alguns contratos permitem levar o saldo na renovação.",
      "O consumo acima do compromisso é cobrado como excedente (overage), em geral com o mesmo desconto. Confira as condições no seu contrato.",
      "Não há compra pelo console: é uma negociação ou renovação comercial com a Oracle. O motor sugere quanto comprometer; o desconto vem do contrato ou da proposta recebida.",
      "O consumo projetado é o da fatura inteira da tenancy, ao preço que você paga hoje. Se a fatura já tem desconto de contrato, informe o desconto adicional sobre o valor atual.",
    ],
  },
};

// Until the deal's discount is registered, show what the spend pool is worth per discount point.
function SpendSimulation({ item, money, profile, onSaved }: {
  item: CommitmentRecommendation; money: (value: unknown) => string; profile: CommitmentProfile;
  onSaved: () => Promise<void>;
}) {
  const projected = Number(item.projected_annual_spend || 0);
  const share = PROFILE_SHARE[profile];
  const profileLabel = PROFILES.find((option) => option.value === profile)?.label.toLowerCase();
  // Yearly saving of a commitment when the spend turns out to be ``spend``: the pool is paid even if unused.
  const savingAt = (spend: number, discount: number) =>
    spend - Math.max(projected * (1 - discount) * share, spend * (1 - discount));
  return (
    <Card sx={{ mb: 2, borderColor: "warning.light" }} variant="outlined"><CardContent>
      <Typography variant="overline" color="text.secondary">{item.instrument_label}</Typography>
      <Stack gap={2.5}>
        <Box sx={{ maxWidth: 720 }}>
          <Typography variant="body2" color="text.secondary">Consumo projetado / ano</Typography>
          <Typography variant="h4" sx={{ fontWeight: 700 }}>{money(projected)}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            O desconto vem do acordo com o provider: o motor não presume nenhum. Cada ponto percentual vale {money(projected / 100)} por ano.
            Cadastre o desconto negociado (ou a proposta recebida) para virar recomendação.
          </Typography>
          <NegotiatedOfferForm item={item} onSaved={onSaved} />
        </Box>
        {SPEND_POOL_GUIDE[item.instrument] && (
          <Box sx={{ maxWidth: 720, p: 1.5, bgcolor: "action.hover", borderRadius: 1 }}>
            <Typography variant="subtitle2" fontWeight={600} mb={0.5}>{SPEND_POOL_GUIDE[item.instrument].title}</Typography>
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {SPEND_POOL_GUIDE[item.instrument].points.map((point) => (
                <Typography key={point} component="li" variant="body2" sx={{ mb: 0.5 }}>{point}</Typography>
              ))}
            </Box>
          </Box>
        )}
        <Box sx={{ maxWidth: 820 }}>
          <Typography variant="subtitle2" fontWeight={600}>Simulação do acordo — perfil {profileLabel}</Typography>
          <Typography variant="body2" color="text.secondary" mb={1}>
            Hoje a tenancy paga {money(projected)} por ano. Com o acordo, todo o consumo passa a custar menos pelo desconto,
            e o contrato fixa um valor anual que é pago mesmo que não seja usado.
          </Typography>
          <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", "& td, & th": { py: 0.75, px: 1, borderBottom: "1px solid", borderColor: "divider", textAlign: "right", verticalAlign: "bottom" }, "& th:first-of-type, & td:first-of-type": { textAlign: "left" } }}>
            <thead><tr>
              <Typography component="th" variant="caption" color="text.secondary">Desconto do acordo</Typography>
              <Typography component="th" variant="caption" color="text.secondary">Valor no contrato / ano</Typography>
              <Typography component="th" variant="caption" color="text.secondary">Você paga / ano</Typography>
              <Typography component="th" variant="caption" color="text.secondary">Economia / ano</Typography>
              <Typography component="th" variant="caption" color="text.secondary">Economia se o uso cair 20%</Typography>
            </tr></thead>
            <tbody>
              <Box component="tr" sx={{ bgcolor: "action.hover" }}>
                <Typography component="td" variant="body2">Sem acordo (hoje)</Typography>
                <Typography component="td" variant="body2" color="text.secondary">—</Typography>
                <Typography component="td" variant="body2" fontWeight={600}>{money(projected)}</Typography>
                <Typography component="td" variant="body2" color="text.secondary">—</Typography>
                <Typography component="td" variant="body2" color="text.secondary">—</Typography>
              </Box>
              {SIMULATED_DISCOUNTS.map((discount) => {
                const contract = projected * (1 - discount) * share;
                const paid = Math.max(contract, projected * (1 - discount));
                const dropSaving = savingAt(projected * (1 - USAGE_DROP), discount);
                return (
                  <tr key={discount}>
                    <Typography component="td" variant="body2">{percent(discount)}</Typography>
                    <Typography component="td" variant="body2">{money(contract)}</Typography>
                    <Typography component="td" variant="body2">{money(paid)}</Typography>
                    <Typography component="td" variant="body2" fontWeight={700} color="success.main">{money(projected - paid)}</Typography>
                    <Typography component="td" variant="body2" color={dropSaving < 0 ? "error.main" : undefined}>{money(dropSaving)}</Typography>
                  </tr>
                );
              })}
            </tbody>
          </Box>
          <Box component="ul" sx={{ mt: 1, mb: 0, pl: 2.5, "& li": { mb: 0.25 } }}>
            <Typography component="li" variant="caption" color="text.secondary">
              <b>Valor no contrato:</b> o que você se compromete a pagar à Oracle no ano, usando ou não. O perfil {profileLabel} compromete {Math.round(share * 100)}% do consumo previsto já com desconto.
            </Typography>
            <Typography component="li" variant="caption" color="text.secondary">
              <b>Você paga:</b> com o uso previsto, o consumo inteiro com desconto (o contrato mais o excedente, que mantém o desconto).
            </Typography>
            <Typography component="li" variant="caption" color="text.secondary">
              <b>Economia:</b> hoje menos o que você paga com o acordo. Com o uso previsto é igual nos três perfis; o perfil muda só o risco.
            </Typography>
            <Typography component="li" variant="caption" color="text.secondary">
              <b>Se o uso cair 20%:</b> economia frente a ficar sem acordo nesse cenário. Negativo (vermelho) quer dizer que o acordo custaria mais, porque o contrato é pago mesmo sem uso.
            </Typography>
          </Box>
        </Box>
      </Stack>
    </CardContent></Card>
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
  const awaitingDiscount = notReady.filter((item) =>
    item.readiness_status === "MISSING_OFFER" && item.purchase_path === "negotiated" && item.projected_annual_spend);
  const otherNotReady = notReady.filter((item) => !awaitingDiscount.includes(item));
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
      <Stack direction={{ xs: "column", lg: "row" }} justifyContent="space-between" alignItems={{ lg: "center" }} gap={2} mb={1}>
        <Box sx={{ maxWidth: 640 }}>
          <Typography variant="subtitle1" fontWeight={700}>Recomendações do motor de compromissos</Typography>
          <Typography variant="body2" color="text.secondary">
            Saving gerado se a proposta for executada, calculado da quantidade faturada (por hora ou por dia, conforme a
            cloud) e do preço unitário efetivo. O motor só recomenda; nada é comprado.
          </Typography>
        </Box>
        {connections && connections.length > 0 && (
          <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
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
          Nenhuma conexão de faturamento com leitor do motor. Hoje o motor lê a OCI pela fatura diária da Silver.
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

      {connection && awaitingDiscount.map((item) => (
        <SpendSimulation key={item.id} item={item} money={moneyIn(item.currency || currency)} profile={profile} onSaved={execute} />
      ))}

      {connection && proposals.length > 0 && <>
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
      </>}

      {connection && otherNotReady.length > 0 && (
          <Card sx={{ mb: 2 }}><CardContent>
            <Typography variant="subtitle2" fontWeight={600} mb={1}>Sem proposta</Typography>
            <Stack spacing={0.75}>
              {otherNotReady.map((item) => (
                <Box key={item.id}>
                  <Typography variant="body2">
                    <strong>{item.instrument_label}</strong> — {READINESS_LABELS[item.readiness_status] || item.readiness_status}: {item.rationale[0]}
                  </Typography>
                  {item.readiness_status === "MISSING_OFFER" && item.purchase_path === "negotiated" &&
                    <NegotiatedOfferForm item={item} onSaved={execute} />}
                </Box>
              ))}
            </Stack>
          </CardContent></Card>
      )}
      {connection && run?.status === "COMPLETED" && !items.length && !loading && (
        <Alert severity="info" sx={{ mb: 2 }}>A última análise não encontrou uso elegível a compromisso nesta conexão.</Alert>
      )}
    </Box>
  );
}
