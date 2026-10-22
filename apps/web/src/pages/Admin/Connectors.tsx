import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, Paper, Stack, Switch, Typography } from "@mui/material";
import { CONNECTOR_TYPES } from "../Connections/Connections";
import { formatApiError, getHiddenConnectorTypes, updateHiddenConnectorTypes } from "../../services/api";

const SCOPE_LABELS: Record<string, string> = {
  azure: "Azure",
  gcp: "Google Cloud",
  oci: "Oracle Cloud (OCI)",
  atlas: "MongoDB Atlas",
  github: "GitHub",
  notebooklm: "NotebookLM",
  gemini: "Google Gemini",
  claude: "Claude (Anthropic)",
  databricks: "Databricks",
};

export default function Connectors() {
  const [savedHidden, setSavedHidden] = useState<string[] | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    getHiddenConnectorTypes()
      .then(list => { setSavedHidden(list); setHidden(new Set(list)); })
      .catch(e => setError(formatApiError(e, "Não foi possível carregar a configuração de conectores")))
      .finally(() => setLoading(false));
  }, []);

  const dirty = savedHidden !== null && (
    hidden.size !== savedHidden.length || savedHidden.some(type => !hidden.has(type))
  );

  const groups = useMemo(() => {
    const byScope = new Map<string, typeof CONNECTOR_TYPES>();
    for (const type of CONNECTOR_TYPES) {
      byScope.set(type.scope, [...(byScope.get(type.scope) ?? []), type]);
    }
    return [...byScope.entries()];
  }, []);

  const setVisible = (type: string, visible: boolean) => {
    setMessage("");
    setHidden(current => {
      const next = new Set(current);
      if (visible) next.delete(type); else next.add(type);
      return next;
    });
  };

  const setAllVisible = (visible: boolean) => {
    setMessage("");
    setHidden(visible ? new Set() : new Set(CONNECTOR_TYPES.map(type => type.value)));
  };

  const discard = () => {
    setMessage("");
    if (savedHidden) setHidden(new Set(savedHidden));
  };

  const save = async () => {
    setSaving(true); setError(""); setMessage("");
    try {
      const saved = await updateHiddenConnectorTypes([...hidden]);
      setSavedHidden(saved);
      setHidden(new Set(saved));
      setMessage("Configuração salva. Fontes de dados já usa a nova lista.");
    } catch (e) {
      setError(formatApiError(e, "Não foi possível salvar a configuração"));
    } finally {
      setSaving(false);
    }
  };

  const visibleCount = CONNECTOR_TYPES.filter(type => !hidden.has(type.value)).length;

  return <Stack spacing={2}>
    <Box>
      <Typography variant="h6">Conectores</Typography>
      <Typography variant="body2" color="text.secondary">
        Escolha quais conectores aparecem em Fontes de dados. Um conector desligado continua configurado
        e volta a aparecer quando você o ligar de novo.
      </Typography>
    </Box>

    {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    {message && <Alert severity="success" onClose={() => setMessage("")}>{message}</Alert>}

    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }} justifyContent="space-between">
        <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap" useFlexGap>
          <Box>
            <Typography variant="caption" color="text.secondary">Visíveis em Fontes</Typography>
            <Typography variant="h6">{visibleCount} de {CONNECTOR_TYPES.length}</Typography>
          </Box>
          {dirty && <Chip size="small" color="warning" variant="outlined" label="Alterações não salvas" />}
        </Stack>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="text" disabled={loading || saving} onClick={() => setAllVisible(true)}>Mostrar todos</Button>
          <Button variant="text" disabled={loading || saving} onClick={() => setAllVisible(false)}>Ocultar todos</Button>
          <Button variant="text" disabled={!dirty || saving} onClick={discard}>Descartar</Button>
          <Button variant="contained" disabled={!dirty || saving || loading} onClick={save}>Salvar</Button>
        </Stack>
      </Stack>
    </Paper>

    {groups.map(([scope, types]) => <Paper key={scope} variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>{SCOPE_LABELS[scope] ?? scope}</Typography>
      <Stack>
        {types.map(type => <Stack
          key={type.value}
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ py: 1, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600}>{type.label}</Typography>
            <Typography variant="caption" color="text.secondary">{type.value}</Typography>
          </Box>
          <Switch
            checked={!hidden.has(type.value)}
            disabled={loading || saving}
            onChange={e => setVisible(type.value, e.target.checked)}
            inputProps={{ "aria-label": `Mostrar ${type.label} em Fontes de dados` }}
          />
        </Stack>)}
      </Stack>
    </Paper>)}
  </Stack>;
}
