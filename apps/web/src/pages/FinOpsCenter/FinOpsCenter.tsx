/**
 * FinOps Simples — visão no estilo CloudZero/Turbonomic.
 *
 * Três perguntas, uma tela cada, navegação por clique — sem KPI montado no
 * navegador e sem sete abas. Os dados vêm pré-digeridos dos cubos Gold:
 * `/finops/overview`, `/finops/actions` e `/finops/navigate`.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, Collapse,
  Stack, Tab, Tabs, Table, TableBody, TableCell, TableHead, TableRow,
  Typography,
} from "@mui/material";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowUpIcon from "@mui/icons-material/KeyboardArrowUp";
import {
  FinOpsAcao, FinOpsActions, FinOpsNavigate, FinOpsOverview,
  getFinopsActions, getFinopsOverview, getFinopsNavigate,
} from "../../services/api";

const brl = (v: unknown) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });

const pctNum = (v: unknown) =>
  v == null ? "—" : `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(1)}%`;

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
  const [aberta, setAberta] = useState<number | null>(null);
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

      <Tabs value={aba} onChange={(_e, v: number) => setAba(v)} sx={{ mb: 2 }}>
        <Tab label="Visão geral" />
        <Tab label={`Ações (${acoes.data.resumo.quantidade})`} />
        <Tab label="Onde está o custo" />
      </Tabs>

      {/* ── Visão geral ─────────────────────────────────────────── */}
      {aba === 0 && (
        <>
          <Stack direction="row" spacing={2} sx={{ mb: 3, flexWrap: "wrap", gap: 2 }}>
            <CardKpi
              titulo={`Custo — último mês completo (${c.custo_mes_completo.periodo ?? "—"})`}
              valor={brl(c.custo_mes_completo.valor)}
            />
            <CardKpi
              titulo={`Variação vs ${c.variacao_mom.periodo_anterior ?? "—"}`}
              valor={c.variacao_mom.comparavel ? `${brl(c.variacao_mom.delta)} (${pctNum(c.variacao_mom.delta_pct)})` : "—"}
              cor={c.variacao_mom.comparavel && (c.variacao_mom.delta ?? 0) > 0 ? "error.main" : "success.main"}
              nota={c.variacao_mom.comparavel ? undefined : c.variacao_mom.reason}
            />
            <CardKpi
              titulo="Run rate anual"
              valor={brl(c.run_rate.anual)}
              nota={`${c.run_rate.meses} meses comparáveis`}
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
                label={`${p.periodo} ${brl(p.custo)}`}
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
              {acoes.data.resumo.quantidade} ações propostas — {brl(acoes.data.resumo.economia_identificada_mensal)}/mês
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
              estimativa mensal; aprovada não é realizada · janela de 15 dias
            </Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell width={36} />
                  <TableCell>Recurso</TableCell>
                  <TableCell>Reduzir para</TableCell>
                  <TableCell align="right">Economia/mês</TableCell>
                  <TableCell>Economia/ano</TableCell>
                  <TableCell>Confiança</TableCell>
                  <TableCell>Dias</TableCell>
                  <TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {acoes.data.acoes.map((a: FinOpsAcao, i: number) => (
                  <>
                    <TableRow key={a.resource_id} hover onClick={() => setAberta(aberta === i ? null : i)} sx={{ cursor: "pointer" }}>
                      <TableCell>
                        {aberta === i ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
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
                      </TableCell>
                      <TableCell align="right" sx={{ color: "success.main", fontWeight: 700 }}>{brl(a.economia_mensal)}</TableCell>
                      <TableCell align="right">{brl(a.economia_anual)}</TableCell>
                      <TableCell><Chip size="small" label={a.confianca} color={a.confianca === "alta" ? "success" : a.confianca === "media" ? "warning" : "default"} /></TableCell>
                      <TableCell>{a.dias_observados}</TableCell>
                      <TableCell><Chip size="small" label={a.status_revisao} /></TableCell>
                    </TableRow>
                    <TableRow key={`${a.resource_id}-det`}>
                      <TableCell colSpan={8} sx={{ py: 0 }}>
                        <Collapse in={aberta === i} unmountOnExit>
                          <Box sx={{ m: 2 }}>
                            <Typography variant="caption" color="text.secondary">Detalhe da ação</Typography>
                            <Stack direction="row" spacing={3} sx={{ mt: 1, flexWrap: "wrap", gap: 2 }}>
                              <Box>
                                <Typography variant="caption" color="text.secondary">Hoje</Typography>
                                <Typography variant="body2">{a.de.sku} — {a.de.vcpus} vCPU, {a.de.memoria_gb} GB</Typography>
                                <Typography variant="body2">{brl(a.de.custo_mensal)}/mês</Typography>
                              </Box>
                              <Box>
                                <Typography variant="caption" color="text.secondary">Proposto</Typography>
                                <Typography variant="body2" color="success.main">{a.para.sku} — {a.para.vcpus} vCPU, {a.para.memoria_gb} GB</Typography>
                                <Typography variant="body2">{brl(a.para.custo_mensal)}/mês</Typography>
                              </Box>
                              <Box>
                                <Typography variant="caption" color="text.secondary">Evidência</Typography>
                                <Typography variant="body2">CPU p95 {a.cpu_p95 != null ? `${Number(a.cpu_p95).toFixed(1)}%` : "—"}</Typography>
                                <Typography variant="body2">{a.dias_observados} dias observados</Typography>
                                <Typography variant="body2">veredito {a.verdict}; revisão {a.status_revisao}</Typography>
                              </Box>
                            </Stack>
                            {a.vetor && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>{a.vetor}</Typography>}
                          </Box>
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </>
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
    </Box>
  );
}