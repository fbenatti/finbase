import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { Alert, Box, Button, Chip, CircularProgress, IconButton, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from "@mui/material";
import { ContentCopyOutlined, InfoOutlined, ManageSearchOutlined, RestoreOutlined, VerifiedUserOutlined, VisibilityOutlined } from "@mui/icons-material";
import {
  getOrphans, getOrphanSavings, getOrphanSummary, setOrphanAnnotation,
  OrphanRow, OrphanSavings,
} from "../../services/api";

const RULE_LABELS: Record<string, string> = {
  disk_unattached: "Discos sem conexão",
  vm_deallocated: "VMs paradas",
  public_ip_idle: "IPs públicos ociosos",
  snapshot_orphan: "Snapshots sem origem",
  lb_idle: "Load balancers / gateways ociosos",
};

const TIPO_LABELS: Record<string, string> = {
  DISCO: "Discos",
  VM: "VMs",
  IP: "Endereços IP públicos",
  SNAPSHOT: "Snapshots",
  LOAD_BALANCER: "Balanceadores de carga",
};

const TIPO_COLORS: Record<string, "primary" | "secondary" | "success" | "warning" | "info"> = {
  DISCO: "primary",
  VM: "secondary",
  IP: "success",
  SNAPSHOT: "warning",
  LOAD_BALANCER: "info",
};

const STATUS_COLORS: Record<string, "error" | "warning" | "success"> = {
  ORFAO: "error",
  EM_ANALISE: "warning",
  EXCECAO: "success",
};

const STATUS_LABELS: Record<string, string> = {
  ORFAO: "Pendente",
  EM_ANALISE: "Em investigação",
  EXCECAO: "Exceção aprovada",
};

function formatBRL(value: number | null | undefined): string {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number) || number === 0) return "—";
  return number.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function formatAge(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 365) return `${value} d`;
  const years = Math.floor(value / 365);
  const days = value % 365;
  return days > 0 ? `${years} a · ${days} d` : `${years} a`;
}

export default function OrphanedResources() {
  const [searchParams] = useSearchParams();
  const [summary, setSummary] = useState<Awaited<ReturnType<typeof getOrphanSummary>> | null>(null);
  const [rows, setRows] = useState<OrphanRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [period, setPeriod] = useState("");
  const [snapshotDate, setSnapshotDate] = useState("");
  const view = searchParams.get("subview") === "savings" ? "savings" : "current";
  const [savings, setSavings] = useState<OrphanSavings | null>(null);
  const [savingsLoading, setSavingsLoading] = useState(true);
  // Multi-seleção: selecionar "Discos" mantém os demais tipos/regras visíveis
  // (arrays — clique no chip adiciona/remove; sem remover os outros).
  const [orphanRule, setOrphanRule] = useState<string[]>([]);
  const [tipo, setTipo] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState("");
  // Recurso selecionado para o diálogo de detalhes completos.
  const [detailRow, setDetailRow] = useState<OrphanRow | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const copyValue = async (field: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 1500);
    } catch { /* clipboard indisponível */ }
  };

  const loadSummary = useCallback(async () => {
    try {
      const data = await getOrphanSummary({
        period: period || undefined,
        snapshot_date: snapshotDate || undefined,
        orphan_rule: orphanRule.length ? orphanRule.join(",") : undefined,
        tipo_recurso: tipo.length ? tipo.join(",") : undefined,
      });
      setSummary(data);
      // Sem filtro manual, a API mostra o snapshot mais recente de cada origem.
      // Selecionar o período/snapshot global mais recente esconderia origens
      // cuja última coleta ocorreu antes (ex.: Azure-4P em setembro, TI em outubro).
      if (data.error) setError(data.error);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar o resumo");
    }
  }, [period, snapshotDate, orphanRule, tipo]);

  const loadRows = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getOrphans({
        period: period || undefined,
        snapshot_date: snapshotDate || undefined,
        orphan_rule: orphanRule.length ? orphanRule.join(",") : undefined,
        tipo_recurso: tipo.length ? tipo.join(",") : undefined,
        status: status || undefined,
        limit: 1000,
      });
      setRows(data.rows || []);
      if (data.error) setError(data.error);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar os recursos órfãos");
    } finally {
      setLoading(false);
    }
  }, [period, snapshotDate, orphanRule, tipo, status]);

  const loadSavings = useCallback(async () => {
    setSavingsLoading(true);
    try {
      const data = await getOrphanSavings({
        orphan_rule: orphanRule.length ? orphanRule.join(",") : undefined,
        tipo_recurso: tipo.length ? tipo.join(",") : undefined,
        limit: 5000,
      });
      setSavings(data);
      if (data.error) setError(data.error);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar o saving apurado");
    } finally {
      setSavingsLoading(false);
    }
  }, [orphanRule, tipo]);

  // Summary (widgets de KPI/custo) e rows recarregam juntos quando os filtros mudam.
  useEffect(() => { void loadSummary(); }, [loadSummary]);
  useEffect(() => { void loadRows(); }, [loadRows]);
  useEffect(() => { void loadSavings(); }, [loadSavings]);

  const changeStatus = async (row: OrphanRow, nextStatus: string) => {
    setSavingId(row.resource_id);
    setSaveError("");
    try {
      await setOrphanAnnotation({
        resource_id: row.resource_id,
        orphan_rule: row.orphan_rule,
        snapshot_date: row.snapshot_date,
        status: nextStatus as "ORFAO" | "EM_ANALISE" | "EXCECAO",
      });
      setRows((prev) => prev.map((r) =>
        r.resource_id === row.resource_id && r.orphan_rule === row.orphan_rule && r.snapshot_date === row.snapshot_date
          ? { ...r, governance_status: nextStatus }
          : r
      ));
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Não foi possível atualizar a situação");
    } finally {
      setSavingId(null);
    }
  };

  const kpis = useMemo(() => {
    const byStatus = new Map((summary?.by_status || []).map((s) => [s.status, s.count]));
    return {
      total: summary?.total ?? rows.length,
      orfao: byStatus.get("ORFAO") ?? 0,
      analise: byStatus.get("EM_ANALISE") ?? 0,
      excecao: byStatus.get("EXCECAO") ?? 0,
      custo: summary?.cost_brl ?? 0,
      gold: summary?.gold_ready ?? false,
    };
  }, [summary, rows]);

  // Multi-seleção: toggla o valor no array, mantendo os demais selecionados.
  const toggleInList = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  if (view === "savings") {
    return (
      <Box>
        {error && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
        <Alert severity="info" sx={{ mb: 2 }}>
          Um recurso entra aqui quando estava no snapshot anterior e deixa de aparecer no snapshot diário seguinte.
          Se voltar a aparecer em uma coleta posterior, ele não é contabilizado como deletado. O saving efetivado é
          proporcional aos dias observados desde o desaparecimento, usando o último custo mensal da base consolidada.
        </Alert>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
          <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "success.main", borderRadius: 2, bgcolor: "rgba(46,125,50,0.04)" }}>
            <Typography variant="h5" color="success.main">{formatBRL(savings?.saving_generated_brl)}</Typography>
            <Typography variant="caption" color="text.secondary">Saving efetivado até {savings?.as_of || "o último snapshot"}</Typography>
          </Paper>
          <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
            <Typography variant="h5">{formatBRL(savings?.monthly_saving_brl)}</Typography>
            <Typography variant="caption" color="text.secondary">Saving mensal consolidado</Typography>
          </Paper>
          <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
            <Typography variant="h5">{savings?.total ?? 0}</Typography>
            <Typography variant="caption" color="text.secondary">Recursos desligados</Typography>
          </Paper>
        </Stack>
        <Paper elevation={0} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "hidden" }}>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Recurso desligado</TableCell>
                  <TableCell>Tipo de recurso</TableCell>
                  <TableCell>Fonte</TableCell>
                  <TableCell>Visto pela última vez</TableCell>
                  <TableCell>Sumiu em</TableCell>
                  <TableCell align="right">Dias de saving</TableCell>
                  <TableCell align="right">Saving por mês</TableCell>
                  <TableCell align="right">Saving efetivado</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {savingsLoading ? (
                  <TableRow><TableCell colSpan={8} align="center"><CircularProgress size={28} sx={{ my: 2 }} /></TableCell></TableRow>
                ) : !savings?.rows.length ? (
                  <TableRow><TableCell colSpan={8} align="center" sx={{ py: 4, color: "text.secondary" }}>
                    Ainda não há desaparecimentos confirmados entre snapshots diários.
                  </TableCell></TableRow>
                ) : savings.rows.map((row) => (
                  <TableRow key={`${row.resource_id}|${row.orphan_rule}|${row.disappeared_on}`} hover>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{row.resource_name || row.resource_id}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ wordBreak: "break-all" }}>{row.resource_id}</Typography>
                    </TableCell>
                    <TableCell>{TIPO_LABELS[row.tipo_recurso || ""] || row.tipo_recurso || "—"}</TableCell>
                    <TableCell>{row.cloud_origem || "—"}</TableCell>
                    <TableCell>{row.last_seen_date}</TableCell>
                    <TableCell>{row.disappeared_on}</TableCell>
                    <TableCell align="right">{row.days_saving}</TableCell>
                    <TableCell align="right">{formatBRL(row.monthly_saving_brl)}</TableCell>
                    <TableCell align="right"><Typography color="success.main" fontWeight={700}>{formatBRL(row.saving_generated_brl)}</Typography></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      </Box>
    );
  }

  return (
    <Box>
      {error && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {saveError && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(saveError)}</Alert>}

      {/* KPIs — Saving em primeiro */}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
        <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "#0EA5C6", borderRadius: 2, bgcolor: "rgba(25,118,210,0.04)" }}>
          <Tooltip title={kpis.gold ? "Saving potencial = custo efetivado mensal dos recursos órfãos, incluindo taxas e ajustes." : "Atualize o modelo de custos por recurso órfão para calcular o saving potencial mensal."}>
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Typography variant="h5" color="primary.main">{formatBRL(kpis.custo)}</Typography>
              <InfoOutlined fontSize="small" color="disabled" />
            </Stack>
          </Tooltip>
          <Typography variant="caption" color="text.secondary">Oportunidade do mês atual</Typography>
        </Paper>
        <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
          <Typography variant="h5">{kpis.total}</Typography>
          <Typography variant="caption" color="text.secondary">Recursos sem uso</Typography>
        </Paper>
        <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "#f44336", borderRadius: 2, bgcolor: "rgba(244,67,54,0.04)" }}>
          <Typography variant="h5" color="error">{kpis.orfao}</Typography>
          <Typography variant="caption" color="text.secondary">Pendentes de análise</Typography>
        </Paper>
        <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "#ed6c02", borderRadius: 2, bgcolor: "rgba(237,108,2,0.04)" }}>
          <Typography variant="h5" color="warning.main">{kpis.analise}</Typography>
          <Typography variant="caption" color="text.secondary">Em investigação</Typography>
        </Paper>
        <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "#2e7d32", borderRadius: 2, bgcolor: "rgba(46,125,50,0.04)" }}>
          <Typography variant="h5" color="success.main">{kpis.excecao}</Typography>
          <Typography variant="caption" color="text.secondary">Exceções aprovadas</Typography>
        </Paper>
      </Stack>

      {/* Segmentação por tipo — multi-seleção: clicar mantém os demais */}
      {(summary?.by_tipo || []).length > 0 && (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
          {summary!.by_tipo.map((item) => (
            <Chip
              key={item.tipo_recurso}
              label={`${TIPO_LABELS[item.tipo_recurso] || item.tipo_recurso}: ${item.cnt}`}
              color={TIPO_COLORS[item.tipo_recurso] || "default"}
              variant={tipo.includes(item.tipo_recurso) ? "filled" : "outlined"}
              onClick={() => setTipo((prev) => toggleInList(prev, item.tipo_recurso))}
            />
          ))}
        </Stack>
      )}

      {/* Contagem por regra — multi-seleção */}
      {(summary?.by_rule || []).length > 0 && (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
          {summary!.by_rule.map((rule) => (
            <Chip
              key={rule.orphan_rule}
              label={`${RULE_LABELS[rule.orphan_rule] || rule.orphan_rule}: ${rule.cnt}`}
              variant={orphanRule.includes(rule.orphan_rule) ? "filled" : "outlined"}
              onClick={() => setOrphanRule((prev) => toggleInList(prev, rule.orphan_rule))}
            />
          ))}
        </Stack>
      )}

      {/* Filtros por chips — sem selects (padrão da página) */}
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
        <Typography variant="overline" color="text.secondary" sx={{ alignSelf: "center" }}>Competência:</Typography>
        {(summary?.periods || []).map((p) => (
          <Chip key={p} size="small" label={p}
            variant={period === p ? "filled" : "outlined"}
            onClick={() => { setPeriod(period === p ? "" : p); setSnapshotDate(""); }} />
        ))}
        <Chip size="small" label="Todos os períodos" variant={period === "" ? "filled" : "outlined"}
          onClick={() => { setPeriod(""); setSnapshotDate(""); }} />
      </Stack>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
        <Typography variant="overline" color="text.secondary" sx={{ alignSelf: "center" }}>Data do snapshot:</Typography>
        {(summary?.snapshot_dates || []).map((snapshot) => (
          <Chip key={snapshot} size="small" label={snapshot}
            variant={snapshotDate === snapshot ? "filled" : "outlined"}
            onClick={() => setSnapshotDate(snapshot)} />
        ))}
      </Stack>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        <Typography variant="overline" color="text.secondary" sx={{ alignSelf: "center" }}>Estado:</Typography>
        {Object.entries(STATUS_LABELS).map(([value, label]) => (
          <Chip key={value} size="small" label={label}
            color={STATUS_COLORS[value] || "default"}
            variant={status === value ? "filled" : "outlined"}
            onClick={() => setStatus(status === value ? "" : value)} />
        ))}
        <Chip size="small" label="Todos os estados" variant={status === "" ? "filled" : "outlined"}
          onClick={() => setStatus("")} />
      </Stack>

      {/* Tabela — sem barras de rolagem internas (layout fixo, texto truncado); o portal rola a página */}
      <Paper elevation={0} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "hidden" }}>
        <TableContainer sx={{ maxHeight: "none", overflowX: "hidden" }}>
          <Table size="small" stickyHeader sx={{ tableLayout: "fixed" }}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: "26%" }}>Recurso (nome)</TableCell>
                <TableCell sx={{ width: "11%" }}>Tipo de recurso</TableCell>
                <TableCell sx={{ width: "11%" }}>Critério</TableCell>
                <TableCell sx={{ width: "10%" }}>Fonte</TableCell>
                <TableCell align="right" sx={{ width: "8%" }}>Porte</TableCell>
                <TableCell sx={{ width: "10%" }}>Assinatura</TableCell>
                <TableCell sx={{ width: "10%" }}>Grupo de recursos</TableCell>
                <TableCell align="right" sx={{ width: "6%" }}>Tempo parado</TableCell>
                <TableCell align="right" sx={{ width: "9%" }}>Custo por mês</TableCell>
                <TableCell sx={{ width: "9%" }}>Estado</TableCell>
                <TableCell align="center" sx={{ width: "13%" }}>Opções</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={11} align="center"><CircularProgress size={28} sx={{ my: 2 }} /></TableCell></TableRow>
              ) : rows.length === 0 ? (
                <TableRow><TableCell colSpan={11} align="center" sx={{ py: 4, color: "text.secondary" }}>
                  Nenhum recurso órfão encontrado para os filtros atuais.
                </TableCell></TableRow>
              ) : rows.map((row) => (
                <TableRow key={`${row.resource_id}|${row.orphan_rule}|${row.snapshot_date}`} hover>
                  <TableCell>
                    <Box component="button" type="button"
                      onClick={() => setDetailRow(row)}
                      sx={{ textAlign: "left", bgcolor: "transparent", border: "none", p: 0, m: 0,
                        cursor: "pointer", maxWidth: "100%", display: "block",
                        "&:hover .res-name": { color: "primary.main" } }}>
                      <Typography variant="body2" fontWeight={600} noWrap className="res-name"
                        color="inherit">{row.resource_name || row.resource_id}</Typography>
                    </Box>
                    <Typography variant="caption" color="text.secondary" noWrap
                      sx={{ display: "block", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", cursor: "pointer" }}
                      title={row.resource_id}
                      onClick={() => setDetailRow(row)}>{row.resource_id}</Typography>
                  </TableCell>
                  <TableCell>
                    <Chip size="small" label={(row.tipo_recurso && TIPO_LABELS[row.tipo_recurso]) || row.tipo_recurso || "—"}
                      color={(row.tipo_recurso && TIPO_COLORS[row.tipo_recurso]) || "default"} variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" noWrap title={RULE_LABELS[row.orphan_rule] || row.orphan_rule}>
                      {RULE_LABELS[row.orphan_rule] || row.orphan_rule}
                    </Typography>
                  </TableCell>
                  <TableCell>{row.cloud_origem || "—"}</TableCell>
                  <TableCell align="right">
                    {row.disk_size_gb ? `${Number(row.disk_size_gb).toLocaleString("pt-BR")} GB` : "—"}
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{row.subscription_id?.slice(0, 8)}…</TableCell>
                  <TableCell>
                    <Typography variant="body2" noWrap title={row.resource_group || "—"}>{row.resource_group || "—"}</Typography>
                  </TableCell>
                  <TableCell align="right">{formatAge(row.age_days)}</TableCell>
                  <TableCell align="right">
                    <Typography variant="body2" fontWeight={row.cost_brl > 0 ? 700 : 400}
                      color={row.cost_brl > 0 ? "primary.main" : "text.secondary"}>
                      {formatBRL(row.cost_brl)}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip size="small" label={STATUS_LABELS[row.governance_status] || row.governance_status}
                      color={STATUS_COLORS[row.governance_status] || "default"} />
                  </TableCell>
                  <TableCell align="center">
                    <Stack direction="row" alignItems="center" justifyContent="center" spacing={0.25}>
                      <Tooltip title="Abrir detalhes">
                        <span>
                          <IconButton size="small" color="info"
                            aria-label={`Detalhes ${row.resource_name}`}
                            onClick={() => setDetailRow(row)}>
                            <VisibilityOutlined fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title={row.governance_status === "EM_ANALISE" ? "Já está sob análise" : "Colocar em análise"}>
                        <span>
                          <IconButton size="small" color="warning"
                            disabled={savingId === row.resource_id || row.governance_status === "EM_ANALISE"}
                            aria-label={`Analisar ${row.resource_name}`}
                            onClick={() => void changeStatus(row, "EM_ANALISE")}>
                            <ManageSearchOutlined fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title={row.governance_status === "EXCECAO" ? "Já é uma exceção" : "Classificar como exceção"}>
                        <span>
                          <IconButton size="small" color="success"
                            disabled={savingId === row.resource_id || row.governance_status === "EXCECAO"}
                            aria-label={`Exceção ${row.resource_name}`}
                            onClick={() => void changeStatus(row, "EXCECAO")}>
                            <VerifiedUserOutlined fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title={row.governance_status === "ORFAO" ? "Já consta como órfão" : "Voltar para órfão (remover anotação)"}>
                        <span>
                          <IconButton size="small" color="error"
                            disabled={savingId === row.resource_id || row.governance_status === "ORFAO"}
                            aria-label={`Pendente: ${row.resource_name}`}
                            onClick={() => void changeStatus(row, "ORFAO")}>
                            <RestoreOutlined fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Diálogo de detalhes completos do recurso selecionado */}
      <Dialog open={detailRow !== null} onClose={() => setDetailRow(null)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1, pr: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0 }}>
            <Typography variant="h6" noWrap sx={{ maxWidth: 520 }}>{detailRow?.resource_name || "Detalhes do recurso"}</Typography>
            {detailRow && (
              <Chip size="small"
                label={STATUS_LABELS[detailRow.governance_status] || detailRow.governance_status}
                color={STATUS_COLORS[detailRow.governance_status] || "default"} />
            )}
          </Stack>
          <Chip size="small" variant="outlined"
            label={detailRow ? ((detailRow.tipo_recurso && TIPO_LABELS[detailRow.tipo_recurso]) || detailRow.tipo_recurso || "—") : ""} />
        </DialogTitle>
        <DialogContent dividers sx={{ pt: 2 }}>
          {detailRow && (
            <Stack spacing={2}>
              <Box>
                <Typography variant="overline" color="text.secondary">Identificação do recurso</Typography>
                <DetailRow label="ID do recurso" value={detailRow.resource_id} copyKey="resource_id" onCopy={copyValue} copied={copiedField === "resource_id"} mono />
                <DetailRow label="Tipo do recurso" value={detailRow.resource_type || "—"} />
                <DetailRow label="ID da assinatura" value={detailRow.subscription_id || "—"} copyKey="subscription_id" onCopy={copyValue} copied={copiedField === "subscription_id"} mono />
                <DetailRow label="Grupo de recursos" value={detailRow.resource_group || "—"} />
                <DetailRow label="Região" value={detailRow.location || "—"} />
                <DetailRow label="Fonte" value={detailRow.cloud_origem || "—"} />
              </Box>
              <Box>
                <Typography variant="overline" color="text.secondary">Classificação do achado</Typography>
                <DetailRow label="Critério" value={detailRow.orphan_rule || "—"} />
                <DetailRow label="Motivo do alerta" value={detailRow.orphan_reason || "—"} />
                <DetailRow label="Tipo de recurso" value={(detailRow.tipo_recurso && TIPO_LABELS[detailRow.tipo_recurso]) || detailRow.tipo_recurso || "—"} />
              </Box>
              <Box>
                <Typography variant="overline" color="text.secondary">Ficha técnica</Typography>
                <DetailRow label="SKU" value={detailRow.sku || "—"} />
                <DetailRow label="Porte do disco" value={detailRow.disk_size_gb ? `${Number(detailRow.disk_size_gb).toLocaleString("pt-BR")} GB` : "—"} />
                <DetailRow label="IP" value={detailRow.ip_address || "—"} />
                <DetailRow label="Disco de origem (snapshot)" value={detailRow.source_disk || "—"} />
                <DetailRow label="Registrado em" value={detailRow.created_at || "—"} />
                <DetailRow label="Tempo de existência" value={formatAge(detailRow.age_days)} />
              </Box>
              <Box>
                <Typography variant="overline" color="text.secondary">Custo mensal efetivado dos recursos órfãos</Typography>
                <DetailRow label="Custo efetivado" value={formatBRL(detailRow.cost_brl)} />
                <DetailRow label="BilledCost" value={formatBRL(detailRow.billedcost)} />
                <DetailRow label="EffectiveCost" value={formatBRL(detailRow.effectivecost)} />
                <DetailRow label="Registros na base" value={String(detailRow.focus_rows ?? 0)} />
              </Box>
              {detailRow.tags && (
                <Box>
                  <Typography variant="overline" color="text.secondary">Tags</Typography>
                  <Box component="pre" sx={{
                    bgcolor: "#FAFBFD", border: "1px solid", borderColor: "divider", borderRadius: 1,
                    p: 1.5, m: 0, maxHeight: 220, overflow: "auto", fontSize: 12,
                    fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-word",
                  }}>
                    {(() => {
                      try { return JSON.stringify(JSON.parse(detailRow.tags), null, 2); }
                      catch { return detailRow.tags; }
                    })()}
                  </Box>
                </Box>
              )}
              <Box>
                <Typography variant="overline" color="text.secondary">Snapshot</Typography>
                <DetailRow label="Data do snapshot" value={detailRow.snapshot_date || "—"} />
                <DetailRow label="Competência" value={detailRow._billing_period || "—"} />
              </Box>
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetailRow(null)}>Fechar</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

function DetailRow({ label, value, copyKey, onCopy, copied, mono }: {
  label: string;
  value: string;
  copyKey?: string;
  onCopy?: (key: string, value: string) => void;
  copied?: boolean;
  mono?: boolean;
}) {
  return (
    <Stack direction="row" spacing={1} sx={{ py: 0.6, alignItems: "flex-start" }}>
      <Typography variant="body2" color="text.secondary" sx={{ width: 200, flexShrink: 0, pt: 0.4 }}>{label}</Typography>
      <Typography variant="body2" sx={{
        flex: 1, minWidth: 0, wordBreak: "break-word",
        fontFamily: mono ? "monospace" : "inherit", fontSize: mono ? 12.5 : "inherit",
      }}>{value}</Typography>
      {copyKey && onCopy && (
        <Tooltip title={copied ? "Copiado com sucesso!" : "Copiar"}>
          <span>
            <IconButton size="small" onClick={() => onCopy(copyKey, value)}>
              {copied ? <VerifiedUserOutlined fontSize="small" color="success" /> : <ContentCopyOutlined fontSize="small" />}
            </IconButton>
          </span>
        </Tooltip>
      )}
    </Stack>
  );
}
