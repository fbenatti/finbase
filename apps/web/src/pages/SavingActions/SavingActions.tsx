/**
 * P10 — Saving Actions: dashboards do ciclo de economia.
 *
 * Le **exclusivamente dos cubos Gold materializados pela governança**
 * (`dest_dm_cost_mvp_*`) — nunca da Silver inteira por request. O aceite do plano
 * é que o resumo reconcilie com o detalhe sobre a MESMA fonte; o detalhe
 * operacional (revisão de cada decisão) fica em /rightsizing.
 *
 * Os níveis de AKS NÃO somam entre si: node_pool e namespace são visões distintas
 * do mesmo custo, e somá-los duplicaria o gasto.
 */
import { useEffect, useState } from "react";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, MenuItem, Stack,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { Button } from "@mui/material";
import { SavingDashboard, getSavingDashboard } from "../../services/api";

const VERDICT_LABEL: Record<string, { label: string; color: "success" | "error" | "warning" }> = {
  VALID: { label: "Válido", color: "success" },
  INVALID: { label: "Vetado", color: "error" },
  REQUIRES_VALIDATION: { label: "Requer validação", color: "warning" },
};

const REVIEW_LABEL: Record<string, "success" | "error" | "default" | "warning"> = {
  APPROVED: "success",
  REJECTED: "error",
  DEFERRED: "warning",
  PENDING: "default",
};

const brl = (v: unknown) =>
  v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

export default function SavingActions() {
  const [dash, setDash] = useState<SavingDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [clusterSel, setClusterSel] = useState<string>("");

  useEffect(() => {
    (async () => {
      try {
        setDash(await getSavingDashboard());
        setErro(null);
      } catch {
        setErro("Não foi possível carregar os cubos do dashboard.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  const oportunidade = dash?.opportunity || [];
  const revisao = dash?.review || [];
  const aks = dash?.aks || [];
  const custo = dash?.cost_by_period || [];

  const totalSavingValid = oportunidade
    .filter((o) => String(o.verdict) === "VALID")
    .reduce((s, o) => s + Number(o.saving || 0), 0);
  const aprovado = revisao
    .filter((r) => String(r.review_status) === "APPROVED")
    .reduce((s, r) => s + Number(r.saving || 0), 0);
  const pendente = revisao
    .filter((r) => String(r.review_status) === "PENDING")
    .reduce((s, r) => s + Number(r.saving || 0), 0);

  // Drilldown AKS: os níveis não somam entre si — cada nível reconcilia ao pai.
  const clusters = Array.from(new Set(aks.map((a) => String(a.parent_resource_id))))
    .filter(Boolean).sort();
  const doCluster = aks.filter((a) => String(a.parent_resource_id) === clusterSel);
  if (!clusterSel && clusters.length) setClusterSel(clusters[0]);

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
        <Box>
          <Typography variant="h4" sx={{ mb: 0.5 }}>Saving Actions</Typography>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            Ciclo de economia: oportunidade identificada, decisão revisada e resultado.
            Fonte: cubos Gold materializados pela governança.
          </Typography>
        </Box>
        <Button component={RouterLink} to="/rightsizing" variant="outlined" size="small">
          Revisar decisões
        </Button>
      </Stack>

      {erro && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErro(null)}>{erro}</Alert>}

      {/* Resumo executivo. O realizado fica ausente até haver execução validada. */}
      <Stack direction="row" spacing={2} sx={{ mb: 3 }} flexWrap="wrap">
        <Card sx={{ minWidth: 200 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Oportunidade válida</Typography>
            <Typography variant="h4">{brl(totalSavingValid)}/mês</Typography>
            <Typography variant="caption" color="text.secondary">
              {oportunidade.filter((o) => String(o.verdict) === "VALID").length} grupo(s)
            </Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 200 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Aprovado em revisão</Typography>
            <Typography variant="h4" sx={{ color: "success.main" }}>{brl(aprovado)}/mês</Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 200 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Pendente de revisão</Typography>
            <Typography variant="h4" sx={{ color: "warning.main" }}>{brl(pendente)}/mês</Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 220 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Saving realizado</Typography>
            <Typography variant="h4" color="text.disabled">—</Typography>
            <Typography variant="caption" color="text.secondary">
              após execução e validação (P13)
            </Typography>
          </CardContent>
        </Card>
      </Stack>

      <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 3 }}>
        {/* Oportunidade por veredito: veredito é dimensão — nunca somado junto. */}
        <Card sx={{ minWidth: 340, flex: 1 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Oportunidade por veredito</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Veredito</TableCell>
                  <TableCell>Conexão</TableCell>
                  <TableCell align="right">Decisões</TableCell>
                  <TableCell align="right">Saving</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {oportunidade.map((o, i) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Chip size="small"
                        color={VERDICT_LABEL[String(o.verdict)]?.color || "default"}
                        label={VERDICT_LABEL[String(o.verdict)]?.label || String(o.verdict)} />
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">{String(o.source_connection || "—")}</Typography>
                    </TableCell>
                    <TableCell align="right">{String(o.decisions)}</TableCell>
                    <TableCell align="right" sx={{ fontWeight: 700 }}>{brl(o.saving)}</TableCell>
                  </TableRow>
                ))}
                {!oportunidade.length && (
                  <TableRow><TableCell colSpan={4}>Sem oportunidade no cubo.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Estado do fluxo de revisão. */}
        <Card sx={{ minWidth: 300, flex: 1 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Estado das decisões</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Revisão</TableCell>
                  <TableCell align="right">Decisões</TableCell>
                  <TableCell align="right">Saving</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {revisao.map((r, i) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Chip size="small" variant="outlined"
                        color={REVIEW_LABEL[String(r.review_status)] || "default"}
                        label={String(r.review_status)} />
                    </TableCell>
                    <TableCell align="right">{String(r.decisions)}</TableCell>
                    <TableCell align="right">{brl(r.saving)}</TableCell>
                  </TableRow>
                ))}
                {!revisao.length && (
                  <TableRow><TableCell colSpan={3}>Sem decisões registradas.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </Stack>

      {/* AKS: os níveis não somam entre si; cada nível reconcilia ao pai. */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="caption" color="text.secondary">
            Consumo AKS (pesos validados somam 1 por cluster, por nível)
          </Typography>
          <Stack direction="row" spacing={2} sx={{ mt: 1, mb: 1.5 }} alignItems="center">
            <TextField
              select size="small" sx={{ minWidth: 340 }}
              label="Cluster" value={clusterSel}
              onChange={(e) => setClusterSel(e.target.value)}
            >
              {clusters.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
            </TextField>
            <Typography variant="caption" color="text.secondary">
              {doCluster.length} alocação(ões)
            </Typography>
          </Stack>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Nível</TableCell>
                <TableCell>Grupo</TableCell>
                <TableCell>Namespace</TableCell>
                <TableCell align="right">Peso</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {doCluster.slice(0, 15).map((a, i) => (
                <TableRow key={i}>
                  <TableCell>{String(a.allocation_level)}</TableCell>
                  <TableCell>
                    <Typography variant="caption">{String(a.allocation_key || "—")}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{String(a.namespace || "—")}</Typography>
                  </TableCell>
                  <TableCell align="right">{Number(a.allocation_weight).toFixed(4)}</TableCell>
                </TableRow>
              ))}
              {!doCluster.length && (
                <TableRow><TableCell colSpan={4}>Sem alocação no cluster selecionado.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Custo por período e conexão: reconcilia com o detalhe da Silver. */}
      <Card>
        <CardContent>
          <Typography variant="caption" color="text.secondary">Custo por período e conexão</Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Período</TableCell>
                <TableCell>Conexão</TableCell>
                <TableCell align="right">Custo efetivo</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {custo.slice(-12).reverse().map((c, i) => (
                <TableRow key={i}>
                  <TableCell>{String(c.period)}</TableCell>
                  <TableCell>
                    <Typography variant="caption">{String(c.source_connection || "—")}</Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{brl(c.cost)}</TableCell>
                </TableRow>
              ))}
              {!custo.length && (
                <TableRow><TableCell colSpan={3}>Sem custo no cubo.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </Box>
  );
}
