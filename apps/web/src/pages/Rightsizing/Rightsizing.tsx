/**
 * P10 — Rightsizing: resumo executivo, decisões e revisão humana.
 *
 * O cubo Gold (`dest_dm_cost_mvp_oportunidade` e correlatos) alimenta o resumo;
 * o detalhe clica no recurso com o snapshot DE/PARA. A projeção aparece com
 * estilo distinto do observado, e o saving realizado fica **ausente** até haver
 * validação — o plano proíbe exibi-lo antes.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, MenuItem, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import {
  ReviewDecision, getRightsizingDecisions, getSavingDashboard, SavingDashboard,
} from "../../services/api";

/** Cadência de cor por veredito: VALID é oportunidade, INVALID é veto. */
const VERDICT_CHIP: Record<string, { color: "success" | "error" | "warning"; label: string }> = {
  VALID: { color: "success", label: "Válido" },
  INVALID: { color: "error", label: "Vetado" },
  REQUIRES_VALIDATION: { color: "warning", label: "Requer validação" },
};

const REVIEW_CHIP: Record<string, "success" | "error" | "default" | "warning"> = {
  APPROVED: "success",
  REJECTED: "error",
  DEFERRED: "warning",
  PENDING: "default",
};

const brl = (v: unknown) =>
  v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

export default function Rightsizing() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [resumo, setResumo] = useState<Record<string, unknown> | null>(null);
  const [dash, setDash] = useState<SavingDashboard | null>(null);
  const [clusterSel, setClusterSel] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [revisando, setRevisando] = useState<Record<string, unknown> | null>(null);
  const [nota, setNota] = useState("");
  const [salvando, setSalvando] = useState(false);

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getRightsizingDecisions(500);
      setItems(d.items || []);
      setResumo(d as unknown as Record<string, unknown>);
      getSavingDashboard().then(setDash).catch(() => setDash(null));
      setErro(null);
    } catch {
      setErro("Não foi possível carregar as decisões.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void carregar(); }, [carregar]);

  const revisar = async (status: string) => {
    if (!revisando) return;
    setSalvando(true);
    try {
      await ReviewDecision(String(revisando.evidence_hash), {
        status,
        reviewer: "ui",
        note: nota || undefined,
      });
      setRevisando(null);
      setNota("");
      await carregar();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setErro(msg || "Não foi possível registrar a revisão.");
    } finally {
      setSalvando(false);
    }
  };

  // Drilldown: clusters do cubo e as alocações do selecionado. Os níveis
  // (node_pool × namespace) NÃO somam entre si — cada nível reconcilia ao pai.
  const clustersDisponiveis = Array.from(
    new Set((dash?.aks || []).map((a) => String(a.parent_resource_id))),
  ).filter(Boolean).sort();
  const aksn = (dash?.aks || []).filter((a) => String(a.parent_resource_id) === clusterSel);
  useEffect(() => {
    if (!clusterSel && clustersDisponiveis.length) setClusterSel(clustersDisponiveis[0]);
  }, [clusterSel, clustersDisponiveis.join("|")]);

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  const resumoObj = (resumo?.review_summary || {}) as Record<string, number>;
  const aprovadas = Number(resumoObj.APPROVED || 0);
  const pendentes = Number(resumoObj.PENDING || 0);
  const savingAprovado = Number(resumo?.saving_approved || 0);
  const savingPendente = Number(resumo?.saving_pending || 0);

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" sx={{ mb: 0.5 }}>Saving Actions</Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        Candidatos do motor determinístico (P8), com revisão humana (P9). O saving
        realizado só aparece após a validação — antes disso fica ausente.
      </Typography>

      {erro && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErro(null)}>{erro}</Alert>}

      <Stack direction="row" spacing={2} sx={{ mb: 3 }} flexWrap="wrap">
        <Card sx={{ minWidth: 200 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Aprovadas</Typography>
            <Typography variant="h4">{aprovadas}</Typography>
            <Typography variant="body2" color="success.main">{brl(savingAprovado)}/mês</Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 200 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Pendentes de revisão</Typography>
            <Typography variant="h4">{pendentes}</Typography>
            <Typography variant="body2" color="text.secondary">{brl(savingPendente)}/mês em análise</Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 200 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Saving realizado</Typography>
            <Typography variant="h4" color="text.disabled">—</Typography>
            <Typography variant="caption" color="text.secondary">disponível após execução e validação</Typography>
          </CardContent>
        </Card>
      </Stack>



      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Recurso</TableCell>
            <TableCell>DE</TableCell>
            <TableCell>PARA</TableCell>
            <TableCell align="right">CPU p95</TableCell>
            <TableCell align="right">Dias obs.</TableCell>
            <TableCell>Veredito</TableCell>
            <TableCell>Revisão</TableCell>
            <TableCell align="right">Saving</TableCell>
            <TableCell align="right">Ação</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {items.map((i, idx) => {
            const verdict = String(i.deterministic_verdict);
            const chip = VERDICT_CHIP[verdict] || VERDICT_CHIP.REQUIRES_VALIDATION;
            const saving = i.saving_monthly == null ? null : Number(i.saving_monthly);
            return (
              <TableRow key={String(i.evidence_hash) + idx} hover>
                <TableCell>
                  <Typography variant="body2" fontWeight={600}>
                    {String(i.resource_name || "—")}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {String(i._source_connection_name || "")}
                  </Typography>
                </TableCell>
                {/* Configuração atual: dado observado. */}
                <TableCell>
                  <Typography variant="body2">{String(i.sku_from || "—")}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {brl(i.cost_from_monthly)}/mês
                  </Typography>
                </TableCell>
                {/* Projeção: estilo distinto de propósito — é estimativa. */}
                <TableCell>
                  {i.sku_to ? (
                    <Typography variant="body2" sx={{ fontStyle: "italic", color: "primary.main" }}>
                      {String(i.sku_to)}
                      <Typography variant="caption" sx={{ display: "block" }}>
                        proj. {brl(i.cost_to_monthly)}/mês
                      </Typography>
                    </Typography>
                  ) : <Typography variant="caption" color="text.secondary">—</Typography>}
                </TableCell>
                <TableCell align="right">{i.cpu_p95 == null ? "—" : `${Number(i.cpu_p95).toFixed(1)}%`}</TableCell>
                <TableCell align="right">{String(i.observed_days ?? "—")}</TableCell>
                <TableCell>
                  <Chip size="small" color={chip.color} label={chip.label} />
                  {i.veto_reason ? (
                    <Typography variant="caption" sx={{ display: "block", maxWidth: 260 }}
                      title={String(i.veto_reason)}>
                      {String(i.veto_reason).slice(0, 60)}
                    </Typography>
                  ) : null}
                </TableCell>
                <TableCell>
                  <Chip size="small" variant="outlined"
                    color={REVIEW_CHIP[String(i.review_status)] || "default"}
                    label={String(i.review_status)} />
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>
                  {saving == null ? <Typography variant="caption" color="text.secondary">sem preço comparável</Typography> : brl(saving)}
                </TableCell>
                <TableCell align="right">
                  {String(i.review_status) === "PENDING" && (
                    <Button size="small" onClick={() => setRevisando(i)}>Revisar</Button>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <Dialog open={!!revisando} onClose={() => setRevisando(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Revisar decisão</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ mt: 1 }}>
            <Typography variant="body2">
              {String(revisando?.resource_name)}:{" "}
              {String(revisando?.sku_from)} → {String(revisando?.sku_to) || "(sem destino)"}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              O veredito determinístico ({String(revisando?.deterministic_verdict)}) não muda
              com a revisão — ela registra a sua decisão sobre ele.
            </Typography>
            <TextField
              label="Nota (opcional)"
              value={nota}
              onChange={(e) => setNota(e.target.value)}
              multiline rows={2} size="small" fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRevisando(null)}>Cancelar</Button>
          <Button color="warning" disabled={salvando} onClick={() => void revisar("DEFERRED")}>
            Deferir
          </Button>
          <Button color="error" disabled={salvando} onClick={() => void revisar("REJECTED")}>
            Rejeitar
          </Button>
          <Button variant="contained" color="success" disabled={salvando}
            onClick={() => void revisar("APPROVED")}>
            Aprovar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
