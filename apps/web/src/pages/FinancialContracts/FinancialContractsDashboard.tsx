import { useEffect, useState } from "react";
import { Alert, Box, Card, CardContent, Grid, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { getFinancialContractsOverview } from "../../services/api";

type Overview = Record<string, unknown>;
const num = (value: unknown) => Number(value || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function FinancialContractsDashboard() {
  const [rows, setRows] = useState<Overview[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { getFinancialContractsOverview().then(setRows).catch((e) => setError(String(e))); }, []);
  const total = (key: string) => rows.reduce((sum, row) => sum + Number(row[key] || 0), 0);
  return <Box sx={{ p: { xs: 2, md: 3 }, bgcolor: "#f8fafc", minHeight: "calc(100vh - 64px)" }}>
    <Typography variant="h4">Controle financeiro contratual</Typography>
    <Typography color="text.secondary" sx={{ mb: 3 }}>Acompanhamento de realizado, créditos e overage por contrato, cloud e ID Orçamento.</Typography>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Grid container spacing={2} sx={{ mb: 3 }}>
      {[['Contratos', rows.length], ['Créditos disponíveis', num(total("credit_balance"))], ['Overage descoberto', num(total("uncovered_overage"))], ['Realizado', num(total("realized"))]].map(([label, value]) => <Grid item xs={12} sm={6} md={3} key={String(label)}><Card><CardContent><Typography color="text.secondary" variant="body2">{label}</Typography><Typography variant="h5" sx={{ mt: 1 }}>{value}</Typography></CardContent></Card></Grid>)}
    </Grid>
    <Card><CardContent><Typography variant="h6" sx={{ mb: 1 }}>Resumo por contrato</Typography><Table size="small"><TableHead><TableRow><TableCell>Contrato</TableCell><TableCell>Status</TableCell><TableCell>Competência</TableCell><TableCell>Pools</TableCell><TableCell>Crédito</TableCell><TableCell>Overage</TableCell><TableCell>Realizado</TableCell></TableRow></TableHead><TableBody>{rows.map((row) => <TableRow key={String(row.contract_id)}><TableCell><strong>{String(row.name)}</strong><br /><small>{String(row.code)}</small></TableCell><TableCell>{row.active ? "Ativo" : "Inativo"}</TableCell><TableCell>{String(row.last_period || "—")}</TableCell><TableCell>{String(row.pool_count || 0)}</TableCell><TableCell>{num(row.credit_balance)}</TableCell><TableCell>{num(row.uncovered_overage)}</TableCell><TableCell>{num(row.realized)}</TableCell></TableRow>)}</TableBody></Table>{!rows.length && <Typography color="text.secondary" sx={{ py: 3 }}>Nenhuma liquidação contratual publicada.</Typography>}</CardContent></Card>
  </Box>;
}
