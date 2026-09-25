/**
 * FinOps Simples — visão no estilo CloudZero/Turbonomic.
 *
 * Três perguntas, uma tela cada, navegação por clique — sem KPI montado no
 * navegador e sem sete abas. Os dados vêm pré-digeridos dos cubos Gold:
 * `/finops/overview`, `/finops/actions` e `/finops/navigate`.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress,
  Stack, Tab, Tabs, Table, TableBody, TableCell, TableHead, TableRow,
  Typography,
} from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import {
  FinOpsAcao, FinOpsActions, FinOpsNavigate, FinOpsOverview,
  getFinopsActions, getFinopsOverview, getFinopsNavigate,
} from "../../services/api";
import FinOpsActionDialog from "./FinOpsActionDialog";
import FinOpsVendorComparison from "./FinOpsVendorComparison";
import FinOpsDiskActions from "./FinOpsDiskActions";

const brl = (v: unknown) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });

const pctNum = (v: unknown) =>
  v == null ? "—" : `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(1)}%`;
const periodLabel = (period: string | null | undefined) => {
  if (!period || !/^\d{6}$/.test(period)) return period ?? "—";
  return new Date(Number(period.slice(0, 4)), Number(period.slice(4, 6)) - 1, 1)
    .toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
};
const utcDateTime = (value: string | null | undefined) => {
  if (!value) return "sem observação recente";
  const iso = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  return new Date(iso).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
};
const utcDayLabel = (value: string | null | undefined) => {
  if (!value || !/^\d{8}$/.test(value)) return value ?? "—";
  return `${value.slice(6, 8)}/${value.slice(4, 6)}/${value.slice(0, 4)}`;
};

const STATUS_COR: Record<string, "success" | "warning" | "info" | "default"> = {
  COMPLETE: "success",
  PARTIAL: "warning",
  CURRENT_OPEN: "info",
};

function CardKpi({ titulo, valor, nota, cor }: { titulo: string; valor: string; nota?: string; cor?: string }) {
  return (
    <Card sx={{ minWidth: 230, flex: 1 }}>
      <CardContent>
        <Typography variant="caption" color="text.secondary">{titulo}</Typography>
        <Typography variant="h4" sx={{ fontWeight: 700, color: cor }}>{valor}</Typography>
        {nota && <Typography variant="caption" color="text.secondary">{nota}</Typography>}
      </CardContent>
    </Card>
  );
}

export default function FinOpsCenter() {
  const [aba, setAba] = useState(0);
  const [ov, setOv] = useState<FinOpsOverview | null>(null);
  const [acoes, setAcoes] = useState<FinOpsActions | null>(null);
  const [resourceDetail, setResourceDetail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  // Navegação: caminho de ancestrais + nível atual.
  const [caminho, setCaminho] = useState<Array<{ dim: string; valor: string }>>([]);
  const [nav, setNav] = useState<FinOpsNavigate | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [o, a] = await Promise.all([getFinopsOverview(), getFinopsActions()]);
        setOv(o); setAcoes(a);
      } catch {
        setErro("Não foi possível carregar a visão FinOps.");
      } finally { setLoading(false); }
    })();
  }, []);

  const navegar = useCallback(
    async (dim: string, ancestrais: Record<string, string | undefined>) => {
      const filtros: Record<string, string | undefined> = { ...ancestrais };
      const n = await getFinopsNavigate(dim, filtros);
      setNav(n);
    },
    []
  );

  useEffect(() => {
    if (aba === 2) {
      const ancestrais = Object.fromEntries(caminho.map((c) => [c.dim, c.valor]));
      navegar(caminho[caminho.length - 1]?.dim ?? "service", ancestrais).catch(() => setErro("Falha ao navegar."));
    }
  }, [aba, caminho, navegar]);

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;
  if (erro) return <Alert severity="error" sx={{ m: 3 }}>{erro}</Alert>;
  if (!ov || !acoes) return null;

  const c = ov.data.cards;
  const moeda = ov.currency ?? "BRL";
  const fmt = (v: number | null | undefined) =>
    v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: moeda, maximumFractionDigits: 0 });

  const descer = (nivel: string, valor: string) => {
    setCaminho((atual) => [...atual, { dim: nivel, valor }]);
  };
  const voltar = () => setCaminho((atual) => atual.slice(0, -1));
  const ancestrais = Object.fromEntries(caminho.map((x) => [x.dim, x.valor]));
  const nivelAtual = caminho.length ? caminho[caminho.length - 1].dim : "service";

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4">FinOps</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Custo, mudança e ação — no cubo reconciliado ({moeda}).
      </Typography>
      {ov.quality.warnings.map((warning) => <Alert key={warning} severity="warning" sx={{ mb: 2 }}>{warning}</Alert>)}
      <Alert severity="info" sx={{ mb: 2 }}>
        Métricas de uso: janela UTC de {utcDayLabel(c.freshness.metric_from_day)} a {utcDayLabel(c.freshness.metric_to_day)} ({c.freshness.metric_window_days} dias pedidos); há amostras em {c.freshness.metric_observed_days} dias, de {utcDateTime(c.freshness.metric_observations_from_utc)} a {utcDateTime(c.freshness.metric_observations_through_utc)}. Há dados em {c.freshness.metric_observed_days} de {c.freshness.metric_window_days} dias; dias sem amostras permanecem como lacunas. {c.freshness.metric_resources.toLocaleString("pt-BR")} resource IDs têm métricas. Inventário, custo e série são vinculados por resource ID.
      </Alert>

      <Tabs value={aba} onChange={(_e, v: number) => setAba(v)} sx={{ mb: 2 }}>
        <Tab label="Visão geral" />
        <Tab label={`Ações (${acoes.data.resumo.quantidade} · ${acoes.data.resumo.quantidade_validas} válidas)`} />
        <Tab label="Onde está o custo" />
        <Tab label="Intel x AMD" />
        <Tab label="Ações de disco" />
      </Tabs>

      {/* ── Visão geral ─────────────────────────────────────────── */}
      {aba === 0 && (
        <>
          <Stack direction="row" spacing={2} sx={{ mb: 3, flexWrap: "wrap", gap: 2 }}>
            <CardKpi
              titulo={`Última competência completa (${periodLabel(c.custo_mes_completo.periodo)})`}
              valor={brl(c.custo_mes_completo.valor)}
            />
            <CardKpi
              titulo={`Variação ${periodLabel(c.variacao_mom.periodo_atual)} vs ${periodLabel(c.variacao_mom.periodo_anterior)}`}
              valor={c.variacao_mom.comparavel ? `${brl(c.variacao_mom.delta)} (${pctNum(c.variacao_mom.delta_pct)})` : "—"}
              cor={c.variacao_mom.comparavel && (c.variacao_mom.delta ?? 0) > 0 ? "error.main" : "success.main"}
              nota={c.variacao_mom.comparavel ? undefined : c.variacao_mom.reason}
            />
            <CardKpi
              titulo="Run rate anual"
              valor={brl(c.run_rate.anual)}
              nota={`${c.run_rate.meses} meses consecutivos${c.run_rate.periodos?.length ? ` · ${periodLabel(c.run_rate.periodos[0])}–${periodLabel(c.run_rate.periodos[c.run_rate.periodos.length - 1])}` : ""}`}
            />
            <CardKpi
              titulo="Economia identificada/mês"
              valor={brl(c.economia.identificada)}
              cor="success.main"
              nota={`${c.economia.acoes} ações; aprovada ${brl(c.economia.aprovada)}`}
            />
          </Stack>

          <Stack direction="row" spacing={1} sx={{ mb: 3, flexWrap: "wrap", gap: 1 }}>
            {ov.data.periodos.map((p) => (
              <Chip
                key={p.periodo}
                size="small"
                label={`${periodLabel(p.periodo)} · ${brl(p.custo)} · ${p.status === "COMPLETE" ? "fechado" : p.status === "CURRENT_OPEN" ? "em andamento" : "incompleto"}`}
                color={STATUS_COR[p.status] ?? "default"}
                variant={p.status === "COMPLETE" ? "outlined" : "filled"}
              />
            ))}
          </Stack>

          <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ gap: 2 }}>
            <Card sx={{ flex: 1, minWidth: 340 }}>
              <CardContent>
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>O que mudou no mês</Typography>
                <Typography variant="caption" color="text.secondary">
                  variação por serviço, no último par comparável
                </Typography>
                <Box sx={{ mt: 1 }}>
                  {ov.data.principais_mudancas.map((m) => (
                    <Stack key={m.key} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Typography variant="body2" sx={{ width: 190, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.key}</Typography>
                      <Typography variant="body2" sx={{ width: 110, textAlign: "right", color: m.delta > 0 ? "error.main" : "success.main", fontWeight: 600 }}>
                        {brl(m.delta)}
                      </Typography>
                      <Typography variant="caption" sx={{ width: 60, textAlign: "right" }}>{pctNum(m.delta_pct)}</Typography>
                    </Stack>
                  ))}
                  {!ov.data.principais_mudancas.length && (
                    <Typography variant="body2" color="text.secondary">Sem variação comparável.</Typography>
                  )}
                </Box>
              </CardContent>
            </Card>

            <Card sx={{ flex: 1, minWidth: 340 }}>
              <CardContent>
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Ações que valem mais</Typography>
                <Typography variant="caption" color="text.secondary">clique em "Ações" para o detalhe</Typography>
                <Box sx={{ mt: 1 }}>
                  {ov.data.acoes_top.map((a) => (
                    <Stack key={a.recurso} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Typography variant="body2" sx={{ width: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.recurso}</Typography>
                      <Typography variant="body2" sx={{ width: 100, textAlign: "right", color: "success.main", fontWeight: 600 }}>{brl(a.economia_mensal)}</Typography>
                      <Chip size="small" label={`confiança ${a.confianca}`} />
                    </Stack>
                  ))}
                </Box>
              </CardContent>
            </Card>
          </Stack>
        </>
      )}

      {/* ── Ações propostas (Turbonomic) ────────────────────────── */}
      {aba === 1 && (
        <Card>
          <CardContent>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
              {acoes.data.resumo.quantidade} recomendações — {brl(acoes.data.resumo.economia_identificada_mensal)}/mês identificados
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
              {acoes.data.resumo.quantidade_validas} válidas · {acoes.data.resumo.quantidade_requer_validacao} requerem validação · savings contam somente vereditos válidos · série atual: {acoes.data.resumo.dias_observados}/{acoes.data.resumo.janela_solicitada_dias} dias com amostras{acoes.data.resumo.perfil_atualizado_em ? ` · snapshots de recomendação atualizados ${utcDateTime(acoes.data.resumo.perfil_atualizado_em)}` : ""}
            </Typography>
            {acoes.data.resumo.perfis_antigos > 0 && <Alert severity="warning" sx={{ mb: 2 }}>
              {acoes.data.resumo.perfis_antigos} recomendações usam um perfil anterior à amostra 4P mais recente. Elas continuam visíveis como snapshot, mas não foram recalculadas com a janela diária atual.
            </Alert>}
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell width={36} />
                  <TableCell>Recurso</TableCell>
                  <TableCell>Reduzir para</TableCell>
                  <TableCell align="right">Economia/mês</TableCell>
                  <TableCell>Economia/ano</TableCell>
                  <TableCell>Confiança</TableCell>
                  <TableCell>Observações</TableCell>
                  <TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {acoes.data.acoes.map((a: FinOpsAcao) => (
                    <TableRow key={a.resource_id} hover onClick={() => setResourceDetail(a.resource_id)} sx={{ cursor: "pointer" }}>
                      <TableCell>
                        <InfoOutlinedIcon fontSize="small" color="action" />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>{a.recurso}</Typography>
                        <Typography variant="caption" color="text.secondary">{a.de.sku}</Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">{a.para.sku}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {a.de.vcpus} vCPU → {a.para.vcpus} vCPU · {a.de.memoria_gb} → {a.para.memoria_gb} GB
                        </Typography>
                        {a.family_change && <Chip size="small" color="info" variant="outlined" label={`${a.vendor_from} → ${a.vendor_to}`} sx={{ mt: 0.5 }} />}
                      </TableCell>
                      <TableCell align="right" sx={{ color: "success.main", fontWeight: 700 }}>{brl(a.economia_mensal)}</TableCell>
                      <TableCell align="right">{brl(a.economia_anual)}</TableCell>
                      <TableCell><Chip size="small" label={a.confianca} color={a.confianca === "alta" ? "success" : a.confianca === "media" ? "warning" : "default"} /></TableCell>
                      <TableCell><Typography variant="body2">{a.dias_observados} no perfil</Typography><Typography variant="caption" color="text.secondary">{a.dias_serie_atual ?? 0} dias atuais</Typography></TableCell>
                      <TableCell><Chip size="small" label={a.status_revisao} /></TableCell>
                    </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* ── Onde está o custo (CloudZero) ───────────────────────── */}
      {aba === 2 && (
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
              {caminho.map((p) => (
                <Chip key={p.dim} label={`${p.dim}: ${p.valor}`} onDelete={voltar} />
              ))}
              <Chip label={`nível: ${nivelAtual}`} color="primary" />
              {caminho.length > 0 && (
                <Typography variant="caption" color="text.secondary" sx={{ cursor: "pointer" }} onClick={voltar}>
                  voltar
                </Typography>
              )}
            </Stack>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{nivelAtual}</TableCell>
                  <TableCell align="right">Custo</TableCell>
                  <TableCell align="right">% do total</TableCell>
                  <TableCell align="right">Recursos</TableCell>
                  <TableCell align="right">Linhas de origem</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {(nav?.data.itens ?? []).map((x) => (
                  <TableRow key={x.chave} hover
                    onClick={() => {
                      const proximo = (nav?.data.proximo_nivel ?? "").trim();
                      if (proximo) descer(nivelAtual, x.chave);
                    }}
                    sx={{ cursor: nav?.data.proximo_nivel ? "pointer" : "default" }}>
                    <TableCell>{x.chave}</TableCell>
                    <TableCell align="right" sx={{ fontWeight: 600 }}>{brl(x.custo)}</TableCell>
                    <TableCell align="right">{x.pct != null ? `${x.pct.toFixed(2)}%` : "—"}</TableCell>
                    <TableCell align="right">{x.recursos.toLocaleString("pt-BR")}</TableCell>
                    <TableCell align="right">{x.linhas_origem.toLocaleString("pt-BR")}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {nav?.data.proximo_nivel && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                clique numa linha para descer para "{nav.data.proximo_nivel}"
              </Typography>
            )}
          </CardContent>
        </Card>
      )}
      {aba === 3 && <FinOpsVendorComparison />}
      {aba === 4 && <FinOpsDiskActions />}
      <FinOpsActionDialog
        open={resourceDetail !== null}
        resourceId={resourceDetail}
        onClose={() => setResourceDetail(null)}
      />
    </Box>
  );
}
