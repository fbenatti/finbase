/**
 * Saving Actions — dashboard completo: custo, tendência e antes/depois.
 *
 * Uma única fonte de verdade: `resource_cost` (custo canônico) e
 * `rightsizing_candidate` (oportunidade DE→PARA). Antes/depois compara o custo
 * atual com o projetado, por recurso, ordenado pelo que mais economiza.
 */
import { useEffect, useState } from "react";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, Stack,
  Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import {
  RightsizingOverview, getRightsizingOverview,
} from "../../services/api";

const brl = (v: unknown) =>
  v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });

const pct = (v: unknown) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);

export default function SavingActions() {
  const [d, setD] = useState<RightsizingOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try { setD(await getRightsizingOverview()); setErro(null); }
      catch { setErro("Não foi possível carregar o dashboard."); }
      finally { setLoading(false); }
    })();
  }, []);

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;
  if (!d) return <Alert severity="error">{erro}</Alert>;

  const c = d.cost;
  const o = d.opportunity;
  const total = Number(c.total.cost || 0);
  const custoMaxPeriodo = Math.max(1, ...c.by_period.map((x) => Number(x.cost || 0)));
  const custoMaxServico = Math.max(1, ...c.by_service.map((x) => Number(x.cost || 0)));
  const savingMax = Math.max(1, ...o.before_after.map((x) => Number(x.saving_monthly || 0)));

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" sx={{ mb: 0.5 }}>Saving Actions</Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        Custo, tendência e oportunidade — antes/depois de cada recurso, ordenado pelo
        que mais economiza. Fonte: Silver canônica (`resource_cost`) e candidatos (P8).
      </Typography>

      {erro && <Alert severity="error" sx={{ mb: 2 }}>{erro}</Alert>}

      {/* Resumo executivo */}
      <Stack direction="row" spacing={2} sx={{ mb: 3 }} flexWrap="wrap">
        <Card sx={{ minWidth: 210 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Custo total (17 períodos)</Typography>
            <Typography variant="h4">{brl(total)}</Typography>
            <Typography variant="caption" color="text.secondary">{String(c.total.resources)} recursos</Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 210 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Oportunidade identificada</Typography>
            <Typography variant="h4" sx={{ color: "success.main" }}>{brl(o.saving_valid_total)}/mês</Typography>
            <Typography variant="caption" color="text.secondary">{o.by_type.length} tipo(s) de recurso</Typography>
          </CardContent>
        </Card>
        <Card sx={{ minWidth: 230 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Maior oportunidade</Typography>
            {o.before_after[0] ? (
              <>
                <Typography variant="h6" sx={{ fontWeight: 700 }}>{o.before_after[0].resource_name}</Typography>
                <Typography variant="body2">{o.before_after[0].sku_from} → {o.before_after[0].sku_to}</Typography>
                <Typography variant="body2" color="success.main">{brl(o.before_after[0].saving_monthly)}/mês</Typography>
              </>
            ) : <Typography variant="body2" color="text.secondary">—</Typography>}
          </CardContent>
        </Card>
      </Stack>

      <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 3 }}>
        {/* Custo por mês: tendência. */}
        <Card sx={{ flex: 1, minWidth: 360 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Custo por mês</Typography>
            <Box sx={{ mt: 1 }}>
              {c.by_period.map((x) => (
                <Stack key={x.period} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.4 }}>
                  <Typography variant="caption" sx={{ width: 48 }}>{x.period.slice(0, 6)}</Typography>
                  <Box sx={{ flex: 1, bgcolor: "#f1f5f9", borderRadius: 1, height: 14 }}>
                    <Box sx={{
                      width: `${(Number(x.cost) / custoMaxPeriodo) * 100}%`,
                      bgcolor: "#2563eb", height: 14, borderRadius: 1,
                    }} />
                  </Box>
                  <Typography variant="caption" sx={{ width: 84, textAlign: "right" }}>{brl(x.cost)}</Typography>
                </Stack>
              ))}
            </Box>
          </CardContent>
        </Card>

        {/* Custo por serviço. */}
        <Card sx={{ flex: 1, minWidth: 360 }}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Custo por serviço</Typography>
            <Box sx={{ mt: 1 }}>
              {c.by_service.slice(0, 10).map((x) => (
                <Stack key={x.name} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.4 }}>
                  <Typography variant="caption" sx={{ width: 150, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{x.name}</Typography>
                  <Box sx={{ flex: 1, bgcolor: "#f1f5f9", borderRadius: 1, height: 14 }}>
                    <Box sx={{ width: `${(Number(x.cost) / custoMaxServico) * 100}%`, bgcolor: "#7c3aed", height: 14, borderRadius: 1 }} />
                  </Box>
                  <Typography variant="caption" sx={{ width: 84, textAlign: "right" }}>{brl(x.cost)}</Typography>
                </Stack>
              ))}
            </Box>
          </CardContent>
        </Card>
      </Stack>

      {/* Antes / depois: a peça central. */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Antes / depois por recurso</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
            Barra: custo atual (DE, azul) contra o projetado (PARA, verde). Ordenado pelo saving.
          </Typography>
          <Box>
            {o.before_after.slice(0, 30).map((b) => (
              <Stack key={b.resource_name + b.sku_to} sx={{ mb: 1 }}>
                <Stack direction="row" justifyContent="space-between">
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {b.resource_name}
                    <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                      {b.resource_type?.replace("microsoft.compute/", "")}
                    </Typography>
                  </Typography>
                  <Typography variant="caption">
                    {b.sku_from} → {b.sku_to} · economiza {brl(b.saving_monthly)}/mês ({pct(b.saving_pct)})
                  </Typography>
                </Stack>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Box sx={{ flex: 1, bgcolor: "#f1f5f9", borderRadius: 1, height: 18, position: "relative" }}>
                    <Box sx={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(Number(b.cost_de) / (Number(b.cost_de) + Number(b.cost_para))) * 100}%`, bgcolor: "#2563eb", borderRadius: "4px 0 0 4px" }} />
                    <Box sx={{ position: "absolute", right: 0, top: 0, bottom: 0, width: `${(Number(b.cost_para) / (Number(b.cost_de) + Number(b.cost_para))) * 100}%`, bgcolor: "#10b981", borderRadius: "0 4px 4px 0", opacity: 0.9 }} />
                  </Box>
                  <Typography variant="caption" sx={{ width: 100, textAlign: "right" }}>
                    {brl(b.cost_de)} → {brl(b.cost_para)}
                  </Typography>
                </Stack>
              </Stack>
            ))}
            {!o.before_after.length && (
              <Typography variant="body2" color="text.secondary">Nenhuma oportunidade com preço comparável.</Typography>
            )}
          </Box>
        </CardContent>
      </Card>

      {/* Detalhe do dado. */}
      <Card>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Detalhe</Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Recurso</TableCell>
                <TableCell>Tipo</TableCell>
                <TableCell>DE</TableCell>
                <TableCell>PARA</TableCell>
                <TableCell align="right">CPU p95</TableCell>
                <TableCell align="right">Custo atual</TableCell>
                <TableCell align="right">Projetado</TableCell>
                <TableCell align="right">Saving/mês</TableCell>
                <TableCell>Estado</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {o.before_after.map((b) => (
                <TableRow key={b.resource_name + b.sku_to} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>{b.resource_name}</Typography>
                    <Typography variant="caption" color="text.secondary">{b.veto_reason ? b.veto_reason.slice(0, 60) : "—"}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">{b.resource_type?.replace("microsoft.compute/", "")}</Typography>
                  </TableCell>
                  <TableCell>{b.sku_from} <Typography variant="caption" sx={{ display: "block" }}>{b.vcpus_from} vCPU · {b.memory_gb_from} GB</Typography></TableCell>
                  <TableCell>{b.sku_to} <Typography variant="caption" sx={{ display: "block", color: "primary.main", fontStyle: "italic" }}>{b.vcpus_to} vCPU · {b.memory_gb_to} GB</Typography></TableCell>
                  <TableCell align="right">{pct(b.cpu_p95)}</TableCell>
                  <TableCell align="right">{brl(b.cost_de)}</TableCell>
                  <TableCell align="right">{brl(b.cost_para)}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700, color: "success.main" }}>{brl(b.saving_monthly)}</TableCell>
                  <TableCell>
                    <Chip size="small" color={b.verdict === "VALID" ? "success" : "warning"} label={b.verdict === "VALID" ? "Válido" : "Requer validação"} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </Box>
  );
}
