import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Box, Typography, Button, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Paper, Chip, Alert,
  CircularProgress, LinearProgress, Stack,
} from "@mui/material";
import { RefreshOutlined, PlayArrowOutlined } from "@mui/icons-material";
import {
  scanBlobs, BlobScanResult, BlobScanItem, getConnection, SourceConnection,
} from "../../services/api";

const STATUS_COLORS: Record<string, "success" | "warning" | "default"> = {
  NEW: "success",
  CHANGED: "warning",
  UNCHANGED: "default",
};

export default function Explorer() {
  const { connectionId } = useParams<{ connectionId: string }>();
  const navigate = useNavigate();
  const [conn, setConn] = useState<SourceConnection | null>(null);
  const [scanResult, setScanResult] = useState<BlobScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const id = Number(connectionId);

  useEffect(() => {
    getConnection(id).then(setConn).catch((e) => setError(e.message));
  }, [id]);

  const doScan = async () => {
    setLoading(true);
    setError("");
    try {
      setScanResult(await scanBlobs(id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível varrer a fonte");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h6">Fonte de dados</Typography>
        <Typography variant="body2" color="text.secondary">
          {conn ? `${conn.name} (${conn.code})` : "Arquivos encontrados na fonte desta conexão, com o status de cada um."}
        </Typography>
      </Box>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} justifyContent="space-between" alignItems={{ sm: "center" }}>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {scanResult ? (
              <>
                <Box sx={{ pr: 3 }}><Typography variant="caption" color="text.secondary">Total</Typography><Typography variant="h6">{scanResult.total}</Typography></Box>
                <Box sx={{ pr: 3 }}><Typography variant="caption" color="text.secondary">Inéditos</Typography><Typography variant="h6" color="success.main">{scanResult.new}</Typography></Box>
                <Box sx={{ pr: 3 }}><Typography variant="caption" color="text.secondary">Modificados</Typography><Typography variant="h6" color="warning.main">{scanResult.changed}</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Sem mudança</Typography><Typography variant="h6">{scanResult.unchanged}</Typography></Box>
              </>
            ) : (
              <Typography variant="body2" color="text.secondary">Execute a varredura para listar os arquivos disponíveis.</Typography>
            )}
          </Stack>
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" startIcon={loading ? <CircularProgress size={16} /> : <RefreshOutlined />} onClick={doScan} disabled={loading}>
              Varrer fonte
            </Button>
            <Button variant="contained" startIcon={<PlayArrowOutlined />} onClick={() => navigate("/operacoes/extracoes")}>
              Ir para extração
            </Button>
          </Stack>
        </Stack>
      </Paper>

      {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      {loading && <LinearProgress />}

      {!scanResult && !loading && (
        <Alert severity="info">Clique em "Varrer fonte" para listar os arquivos disponíveis na fonte.</Alert>
      )}

      {scanResult && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 600 }}>Situação</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Caminho do arquivo</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Competência</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Modificado em</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Tamanho</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {scanResult.items.map((item: BlobScanItem, i: number) => (
                <TableRow key={i} hover>
                  <TableCell>
                    <Chip label={item.status} size="small" variant="outlined" color={STATUS_COLORS[item.status] || "default"} />
                  </TableCell>
                  <TableCell sx={{ maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis" }}>{item.blob_path}</TableCell>
                  <TableCell>{item.billing_period || "-"}</TableCell>
                  <TableCell>{new Date(item.last_modified).toLocaleString()}</TableCell>
                  <TableCell>{item.content_length ? `${(item.content_length / 1024).toFixed(1)} KB` : "-"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Stack>
  );
}
