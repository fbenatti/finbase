/**
 * Serviços auxiliares — Jev e itens futuros.
 *
 * Não são fontes de dado de nuvem, então não entram na lista de conexões. Mas
 * seguem o mesmo princípio: **endpoint e segredo são configuração, não código**.
 *
 * O token é write-only. A API devolve `secret_fields` (quais campos existem) e
 * nunca o valor — o formulário mostra "configurado" e permite substituir, sem
 * exibir. Se o valor viesse para a tela, um print de suporte o vazaria.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions,
  DialogContent, DialogTitle, FormControlLabel, IconButton, Link, Stack, Switch,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import DeleteOutline from "@mui/icons-material/DeleteOutline";
import {
  deleteIntegration, IntegrationItem, getIntegrations, saveIntegration, testIntegration,
} from "../../services/api";

/** Rótulo e ajuda por tipo, para o operador saber o que está configurando. */
const TYPE_HELP: Record<string, string> = {
  ai_provider: "Serviço de decisão assistida. Não altera fato: não promove um candidato vetado.",
  itsm: "Abertura de chamado a partir de uma ação aprovada.",
  auxiliary: "Serviço auxiliar.",
};

const STATUS_COLOR: Record<string, "success" | "warning" | "error" | "default"> = {
  OK: "success",
  NOT_CONFIGURED: "warning",
  UNAUTHORIZED: "error",
  UNREACHABLE: "error",
  ERROR: "error",
};

export default function IntegrationsPanel() {
  const [items, setItems] = useState<IntegrationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [editando, setEditando] = useState<IntegrationItem | null>(null);
  const [excluindo, setExcluindo] = useState<IntegrationItem | null>(null);
  const [exclusaoErro, setExclusaoErro] = useState<string | null>(null);
  const [excluindoEmAndamento, setExcluindoEmAndamento] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [testando, setTestando] = useState(false);
  const [resultado, setResultado] = useState<string | null>(null);
  // Campos de configuração e segredo em edição. O segredo começa vazio de
  // propósito: nunca é preenchido com o valor gravado.
  const [config, setConfig] = useState<Record<string, string>>({});
  const [segredo, setSegredo] = useState<Record<string, string>>({});

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const dados = await getIntegrations();
      setItems(dados.items || []);
      setErro(null);
    } catch (e) {
      setErro("Não foi possível carregar os serviços auxiliares.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void carregar(); }, [carregar]);

  const abrir = (item: IntegrationItem) => {
    setEditando(item);
    setResultado(null);
    const inicial: Record<string, string> = {};
    Object.entries(item.config || {}).forEach(([k, v]) => {
      inicial[k] = v == null ? "" : String(v);
    });
    // Sugere os campos usuais quando ainda não há nada gravado, para o operador
    // não ter de adivinhar os nomes.
    if (item.code === "jev") {
      if (!("base_url" in inicial)) inicial.base_url = "";
      if (!("path_systemone" in inicial)) inicial.path_systemone = "/v1/systemone";
      if (!("model" in inicial)) inicial.model = "";
      if (!("timeout_s" in inicial)) inicial.timeout_s = "90";
    }
    setConfig(inicial);
    setSegredo({});
  };

  const salvar = async () => {
    if (!editando) return;
    setSalvando(true);
    setResultado(null);
    try {
      const configNumerica: Record<string, unknown> = {};
      Object.entries(config).forEach(([k, v]) => {
        // `timeout_s` é número no backend; enviar string quebraria a validação.
        configNumerica[k] = k.endsWith("_s") && v.trim() !== "" ? Number(v) : v;
      });
      const corpo: Parameters<typeof saveIntegration>[1] = { config: configNumerica };
      // Só envia o segredo se o operador digitou algo: campo vazio significa
      // "manter o que está gravado", não "apagar".
      const segredoPreenchido = Object.fromEntries(
        Object.entries(segredo).filter(([, v]) => v.trim() !== ""),
      );
      if (Object.keys(segredoPreenchido).length) corpo.secret = segredoPreenchido;
      await saveIntegration(editando.code, corpo);
      await carregar();
      setEditando(null);
    } catch (e) {
      setResultado("Falha ao salvar. Verifique os campos.");
    } finally {
      setSalvando(false);
    }
  };

  const alternar = async (item: IntegrationItem, habilitado: boolean) => {
    try {
      await saveIntegration(item.code, { enabled: habilitado });
      await carregar();
    } catch {
      setErro(`Não foi possível ${habilitado ? "habilitar" : "desabilitar"} ${item.name}.`);
    }
  };

  const testar = async () => {
    if (!editando) return;
    setTestando(true);
    setResultado(null);
    try {
      const r = await testIntegration(editando.code);
      setResultado(`${r.status}: ${r.message}`);
      await carregar();
    } catch {
      setResultado("Falha ao executar o teste.");
    } finally {
      setTestando(false);
    }
  };

  const excluir = async () => {
    if (!excluindo) return;
    setExcluindoEmAndamento(true);
    setExclusaoErro(null);
    try {
      await deleteIntegration(excluindo.code);
      setExcluindo(null);
      await carregar();
    } catch {
      setExclusaoErro(`Não foi possível excluir ${excluindo.name}. Verifique sua permissão e tente novamente.`);
    } finally {
      setExcluindoEmAndamento(false);
    }
  };

  if (loading) {
    return <Box sx={{ p: 3 }}><CircularProgress size={22} /></Box>;
  }

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Serviços que apoiam a análise sem serem fonte de dado de nuvem. O segredo é
        gravado e nunca exibido de volta.
      </Typography>

      {erro && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErro(null)}>{erro}</Alert>}

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Serviço</TableCell>
            <TableCell>Tipo</TableCell>
            <TableCell>Estado</TableCell>
            <TableCell>Último teste</TableCell>
            <TableCell align="right">Ações</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.code} hover>
              <TableCell>
                <Stack spacing={0.3}>
                  <Typography variant="body2" fontWeight={600}>{item.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {item.description}
                  </Typography>
                </Stack>
              </TableCell>
              <TableCell>
                <Chip
                  size="small"
                  variant="outlined"
                  label={TYPE_HELP[item.integration_type] ? item.integration_type : item.integration_type}
                  title={TYPE_HELP[item.integration_type] || ""}
                />
              </TableCell>
              <TableCell>
                <Stack direction="row" spacing={1} alignItems="center">
                  <FormControlLabel
                    control={
                      <Switch
                        size="small"
                        checked={item.enabled}
                        onChange={(e) => void alternar(item, e.target.checked)}
                      />
                    }
                    label=""
                  />
                  {item.has_secret
                    ? <Chip size="small" color="success" variant="outlined" label="token gravado" />
                    : <Chip size="small" color="warning" variant="outlined" label="sem token" />}
                </Stack>
              </TableCell>
              <TableCell>
                {item.last_test_status ? (
                  <Stack spacing={0.3}>
                    <Chip
                      size="small"
                      color={STATUS_COLOR[item.last_test_status] || "default"}
                      label={item.last_test_status}
                    />
                    <Typography variant="caption" color="text.secondary" sx={{ maxWidth: 260 }}>
                      {String(item.last_test_message || "").slice(0, 90)}
                    </Typography>
                  </Stack>
                ) : (
                  <Typography variant="caption" color="text.secondary">nunca testado</Typography>
                )}
              </TableCell>
              <TableCell align="right">
                <Stack direction="row" spacing={0.5} justifyContent="flex-end" alignItems="center">
                  <Button size="small" onClick={() => abrir(item)}>Configurar</Button>
                  {item.id !== null && (
                    <Tooltip title="Excluir serviço auxiliar">
                      <IconButton
                        size="small"
                        color="error"
                        aria-label={`Excluir ${item.name}`}
                        onClick={() => { setExclusaoErro(null); setExcluindo(item); }}
                      >
                        <DeleteOutline fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </Stack>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Dialog
        open={Boolean(excluindo)}
        onClose={() => { if (!excluindoEmAndamento) { setExcluindo(null); setExclusaoErro(null); } }}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Excluir serviço auxiliar?</DialogTitle>
        <DialogContent>
          {exclusaoErro ? (
            <Alert severity="error">{exclusaoErro}</Alert>
          ) : (
            <Typography>
              A configuração e as credenciais salvas de <strong>{excluindo?.name}</strong> serão removidas.
              Serviços previstos no catálogo podem continuar aparecendo como não configurados.
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button disabled={excluindoEmAndamento} onClick={() => { setExcluindo(null); setExclusaoErro(null); }}>
            Cancelar
          </Button>
          <Button color="error" variant="contained" disabled={excluindoEmAndamento || Boolean(exclusaoErro)} onClick={() => void excluir()}>
            {excluindoEmAndamento ? "Excluindo…" : "Excluir serviço"}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!editando} onClose={() => setEditando(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Configurar {editando?.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              {TYPE_HELP[editando?.integration_type || ""] || ""}
            </Typography>

            {Object.keys(config).map((chave) => (
              <TextField
                key={chave}
                label={chave}
                value={config[chave]}
                onChange={(e) => setConfig({ ...config, [chave]: e.target.value })}
                size="small"
                fullWidth
                helperText={
                  chave === "base_url" ? "Endpoint do serviço. Variável, não chumbado no código."
                    : chave === "model" ? "Obrigatório para o Jev. Ex.: jev-latest (lista em GET /v1/models)."
                    : chave === "path_systemone" ? "Caminho do primitivo de decisão."
                    : chave === "timeout_s" ? "Segundos."
                    : undefined
                }
              />
            ))}

            <TextField
              label={editando?.has_secret ? "Token (já gravado — preencha para substituir)" : "Token"}
              value={segredo.api_key || ""}
              onChange={(e) => setSegredo({ ...segredo, api_key: e.target.value })}
              size="small"
              fullWidth
              type="password"
              autoComplete="new-password"
              helperText="Write-only: não é exibido de volta depois de gravado."
            />

            {resultado && (
              <Alert severity={resultado.startsWith("OK") ? "success" : "warning"}>
                {resultado}
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditando(null)}>Cancelar</Button>
          <Button onClick={() => void testar()} disabled={testando}>
            {testando ? "Testando…" : "Testar"}
          </Button>
          <Button variant="contained" onClick={() => void salvar()} disabled={salvando}>
            {salvando ? "Salvando…" : "Salvar"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
