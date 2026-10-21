import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Box, Typography, Button, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Paper, Chip, Alert,
  CircularProgress, LinearProgress,
} from "@mui/material";
import { Refresh, PlayArrow } from "@mui/icons-material";
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
      setError(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", mb: 3 }}>
        <Box>
          <Typography variant="h4">Blob Explorer</Typography>
          {conn && (
            <Typography variant="body1" color="text.secondary">
              {conn.name} ({conn.code})
            </Typography>
          )}
        </Box>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button variant="outlined" startIcon={<Refresh />} onClick={doScan} disabled={loading}>
            Escanear
          </Button>
          <Button variant="contained" startIcon={<PlayArrow />} onClick={() => navigate("/operacoes/execucoes?tab=extraction")}>
            Ir para Extração
          </Button>
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      {loading && <LinearProgress sx={{ mb: 2 }} />}

      {scanResult && (
        <Box sx={{ mb: 2, display: "flex", gap: 2, flexWrap: "wrap" }}>
          <Chip label={`Total: ${scanResult.total}`} color="primary" />
          <Chip label={`Novos: ${scanResult.new}`} color="success" />
          <Chip label={`Alterados: ${scanResult.changed}`} color="warning" />
          <Chip label={`Inalterados: ${scanResult.unchanged}`} />
        </Box>
      )}

      {!scanResult && !loading && (
        <Alert severity="info">Clique em "Escanear" para listar os blobs disponiveis.</Alert>
      )}

      {scanResult && (
        <TableContainer component={Paper}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Blob Path</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Período</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Ultima Modificacao</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Tamanho</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {scanResult.items.map((item: BlobScanItem, i: number) => (
                <TableRow key={i} hover>
                  <TableCell>
                    <Chip
                      label={item.status}
                      size="small"
                      color={STATUS_COLORS[item.status] || "default"}
                    />
                  </TableCell>
                  <TableCell sx={{ maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {item.blob_path}
                  </TableCell>
                  <TableCell>{item.billing_period || "-"}</TableCell>
                  <TableCell>{new Date(item.last_modified).toLocaleString()}</TableCell>
                  <TableCell>{item.content_length ? `${(item.content_length / 1024).toFixed(1)} KB` : "-"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
