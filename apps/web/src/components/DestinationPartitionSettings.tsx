import { dataDisplayText } from "../utils/dataPresentation";
import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, FormControl, FormHelperText, InputLabel,
  MenuItem, Paper, Select, TextField, Typography,
} from "@mui/material";
import { SaveOutlined } from "@mui/icons-material";
import {
  DestinationPartitionSpec,
  DestinationPartitionExtra,
  getDestinationTable,
  updateDestinationTable,
} from "../services/api";

const HIGH_CARDINALITY = new Set([
  "subaccountname",
  "useremail",
  "resource_id",
  "resourceid",
]);

const MAX_EXTRAS = 2;

type PartitionSpecState = {
  grain: "billing_period" | "snapshot_date";
  snapshot_column?: string;
  extra_partitions: DestinationPartitionExtra[];
};

function defaultSpec(): PartitionSpecState {
  return { grain: "billing_period", extra_partitions: [] };
}

function normalizeSpec(raw: DestinationPartitionSpec | null | undefined): PartitionSpecState {
  if (!raw) return defaultSpec();
  return {
    grain: raw.grain === "snapshot_date" ? "snapshot_date" : "billing_period",
    snapshot_column: raw.snapshot_column || undefined,
    extra_partitions: (raw.extra_partitions || []).slice(0, MAX_EXTRAS).map((e) => ({
      column: e.column,
      transform: e.transform === "bucket" ? "bucket" : "identity",
      bucket_count: e.bucket_count ?? 16,
    })),
  };
}

function findSubAccountColumn(columnNames: string[]): string | null {
  return columnNames.find((name) => name.toLowerCase() === "subaccountname") ?? null;
}

type Props = {
  destinationTableId: number;
  columnNames: string[];
  /** Conexão selecionada no relacionamento — usada na fatia replace. */
  sourceConnectionId?: number | "";
  sourceConnectionLabel?: string;
  onSaved?: () => void;
};

export default function DestinationPartitionSettings({
  destinationTableId,
  columnNames,
  sourceConnectionId,
  sourceConnectionLabel,
  onSaved,
}: Props) {
  const [spec, setSpec] = useState<PartitionSpecState>(defaultSpec());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getDestinationTable(destinationTableId)
      .then((detail) => {
        if (!cancelled) setSpec(normalizeSpec(detail.partition_spec_json));
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Não foi possível carregar a partição");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [destinationTableId]);

  const availableForExtra = columnNames.filter(
    (name) => !spec.extra_partitions.some((e) => e.column === name),
  );

  const addExtra = (column: string) => {
    if (spec.extra_partitions.length >= MAX_EXTRAS) return;
    const lower = column.toLowerCase();
    const useBucket = HIGH_CARDINALITY.has(lower);
    setSpec({
      ...spec,
      extra_partitions: [
        ...spec.extra_partitions,
        {
          column,
          transform: useBucket ? "bucket" : "identity",
          bucket_count: 16,
        },
      ],
    });
  };

  const updateExtra = (index: number, patch: Partial<DestinationPartitionExtra>) => {
    const next = spec.extra_partitions.map((e, i) => (i === index ? { ...e, ...patch } : e));
    setSpec({ ...spec, extra_partitions: next });
  };

  const removeExtra = (index: number) => {
    setSpec({
      ...spec,
      extra_partitions: spec.extra_partitions.filter((_, i) => i !== index),
    });
  };

  const save = async () => {
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      await updateDestinationTable(destinationTableId, {
        partition_spec_json: {
          grain: spec.grain,
          snapshot_column: spec.grain === "snapshot_date" ? (spec.snapshot_column || "snapshot_date") : undefined,
          extra_partitions: spec.extra_partitions,
        },
      });
      setSuccess("Partição salva.");
      onSaved?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível salvar");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="body2" color="text.secondary">Buscando partição do destino…</Typography>
      </Paper>
    );
  }

  const slicePeriodLabel = spec.grain === "snapshot_date"
    ? (spec.snapshot_column || "snapshot_date")
    : "_billing_period";
  const subAccountCol = findSubAccountColumn(columnNames);
  const hasSubAccountExtra = spec.extra_partitions.some(
    (e) => e.column.toLowerCase() === "subaccountname",
  );

  const applyFocusSubAccountPreset = () => {
    if (!subAccountCol || hasSubAccountExtra) return;
    addExtra(subAccountCol);
  };

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2, bgcolor: "#fafbff" }}>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>

        Organização dos dados de destino
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        A substituição de registros para evitar duplicatas considera sempre período + conexão de origem.
        Campo de identidade extra em <strong>SubAccountId</strong> entra na partição física (Azure: SubscriptionId;
        GCP: id do projeto). Valor vazio vira <code>__UNSET__</code>. SubAccountName não entra no caminho.
      </Typography>

      <Box sx={{ mb: 1.5, display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center" }}>
        <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
          Chaves da fatia (replace):
        </Typography>
        <Chip size="small" variant="outlined" label={slicePeriodLabel} />
        <Chip size="small" variant="outlined" color="secondary" label="source_connection_id" />
        {sourceConnectionId ? (
          <Chip
            size="small"
            color="primary"
            label={sourceConnectionLabel
              ? `Conexão origem: ${sourceConnectionLabel} (id ${sourceConnectionId})`
              : `Conexão origem id ${sourceConnectionId}`}
          />
        ) : (
          <Typography variant="caption" color="text.secondary">
            Selecione a fonte acima para ver a conexão usada na publicação.
          </Typography>
        )}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 1 }} onClose={() => setSuccess("")}>{dataDisplayText(success)}</Alert>}

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2, alignItems: "flex-start" }}>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Granularidade da fatia</InputLabel>
          <Select
            label="Granularidade da fatia"
            value={spec.grain}
            onChange={(e) => setSpec({
              ...spec,
              grain: e.target.value as PartitionSpecState["grain"],
            })}
          >
            <MenuItem value="billing_period">Mês de faturamento (_billing_period)</MenuItem>
            <MenuItem value="snapshot_date">Dia da captura (snapshot)</MenuItem>
          </Select>
          <FormHelperText>Obrigatório na publicação da base preparada</FormHelperText>
        </FormControl>

        {spec.grain === "snapshot_date" && (
          <TextField
            size="small"
            label="Coluna da captura"
            value={spec.snapshot_column || "snapshot_date"}
            onChange={(e) => setSpec({ ...spec, snapshot_column: e.target.value })}
            sx={{ minWidth: 200 }}
          />
        )}

        <FormControl size="small" sx={{ minWidth: 220 }} disabled={!availableForExtra.length}>
          <InputLabel>Campo extra de partição</InputLabel>
          <Select
            label="Campo extra de partição"
            value=""
            displayEmpty
            onChange={(e) => {
              const col = e.target.value as string;
              if (col) addExtra(col);
            }}
          >
            <MenuItem value="" disabled>
              {spec.extra_partitions.length >= MAX_EXTRAS
                ? `Máximo ${MAX_EXTRAS} extras`
                : "Incluir coluna…"}
            </MenuItem>
            {availableForExtra.map((name) => (
              <MenuItem key={name} value={name}>{name}</MenuItem>
            ))}
          </Select>
          <FormHelperText>Limite de {MAX_EXTRAS} colunas do destino</FormHelperText>
        </FormControl>

        {subAccountCol && !hasSubAccountExtra && (
          <Button size="small" variant="outlined" onClick={applyFocusSubAccountPreset} sx={{ mt: 0.5 }}>
            Adicionar {subAccountCol} (distribuição por grupos)
          </Button>
        )}

        <Button
          variant="contained"
          size="small"
          startIcon={<SaveOutlined />}
          onClick={save}
          disabled={saving}
          sx={{ mt: 0.5 }}
        >
          {saving ? "Salvando…" : "Gravar partição"}
        </Button>
      </Box>

      {spec.extra_partitions.length === 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          Nenhuma partição extra configurada
          {subAccountCol ? ` — use o menu ou o botão para ${subAccountCol}.` : " — defina colunas no destino primeiro."}
        </Typography>
      )}

      {spec.extra_partitions.length > 0 && (
        <Box sx={{ mt: 2, display: "flex", flexDirection: "column", gap: 1 }}>
          {spec.extra_partitions.map((extra, index) => (
            <Box
              key={`${extra.column}-${index}`}
              sx={{
                display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center",
                p: 1, borderRadius: 1, bgcolor: "#fff", border: "1px solid #E1E8F0",
              }}
            >
              <Chip label={extra.column} size="small" color="primary" variant="outlined" />
              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>Transformação</InputLabel>
                <Select
                  label="Transformação"
                  value={extra.transform}
                  onChange={(e) => updateExtra(index, {
                    transform: e.target.value as "identity" | "bucket",
                  })}
                >
                  <MenuItem value="identity">Identidade (baixa cardinalidade)</MenuItem>
                  <MenuItem value="bucket">Agrupamento em buckets (muitos valores)</MenuItem>
                </Select>
              </FormControl>
              {extra.transform === "bucket" && (
                <TextField
                  size="small"
                  type="number"
                  label="Quantidade de buckets"
                  value={extra.bucket_count ?? 16}
                  inputProps={{ min: 2, max: 128 }}
                  onChange={(e) => updateExtra(index, {
                    bucket_count: Math.max(2, Math.min(128, Number(e.target.value) || 16)),
                  })}
                  sx={{ width: 100 }}
                />
              )}
              <Button size="small" color="error" onClick={() => removeExtra(index)}>
                Remover
              </Button>
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
}
