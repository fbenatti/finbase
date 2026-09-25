import { useEffect, useState } from "react";
import {
  Alert, Box, Card, CardContent, CircularProgress, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { FinOpsVendorComparison as Comparison, getFinopsVendorComparison } from "../../services/api";

const money = (value: number | null, currency: string | null) => value == null
  ? "Sem base comparável"
  : Number(value).toLocaleString("pt-BR", { style: "currency", currency: currency || "USD", maximumFractionDigits: 0 });

export default function FinOpsVendorComparison() {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getFinopsVendorComparison().then(setData).catch(() => setError("Não foi possível carregar a comparação de fabricantes."));
  }, []);
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 6 }} />;
  const currency = data.currency;
  return (
    <Box>
      <Typography variant="h6" sx={{ mb: 0.5 }}>Distribuição por fabricante</Typography>
      <Typography color="text.secondary" variant="body2" sx={{ mb: 2 }}>
        Inventário atual de VMs Azure em famílias D/E x86 reconhecidas. Outros inclui SKUs sem classificação Intel/AMD.
      </Typography>
      <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" sx={{ mb: 3 }}>
        {(["Intel", "AMD", "other"] as const).map((vendor) => (
          <Card key={vendor} sx={{ minWidth: 200, flex: 1 }}><CardContent>
            <Typography variant="caption" color="text.secondary">{vendor === "other" ? "Outros / não classificados" : vendor}</Typography>
            <Typography variant="h4" fontWeight={700}>{data.counts[vendor].toLocaleString("pt-BR")}</Typography>
            <Typography variant="body2" color="text.secondary">{data.shares_pct[vendor]}% de {data.total_servers.toLocaleString("pt-BR")} servidores</Typography>
          </CardContent></Card>
        ))}
        <Card sx={{ minWidth: 260, flex: 1 }}><CardContent>
          <Typography variant="caption" color="text.secondary">Economia mensal potencial por troca de vendor</Typography>
          <Typography variant="h4" fontWeight={700} color="success.main">
            {currency ? money(data.estimated_monthly_saving, currency)
              : Object.keys(data.savings_by_currency).length > 1 ? "Moedas diferentes"
                : data.servers_with_comparable_saving ? "Sem saving calculável" : "—"}
          </Typography>
          <Typography variant="body2" color="text.secondary">Cobertura comparável: {data.servers_with_comparable_saving.toLocaleString("pt-BR")} servidores</Typography>
        </CardContent></Card>
      </Stack>
      <Card><CardContent>
        <Typography variant="subtitle1" fontWeight={700}>Pares de capacidade equivalente</Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1.5 }}>
          Custo de referência usa a tarifa Consumption carregada no Silver × 730 horas × servidores, com origem e destino na mesma região e preço da mesma fonte/moeda. Não depende de consumo/p95.
        </Typography>
        <Table size="small"><TableHead><TableRow>
          <TableCell>Família atual</TableCell><TableCell>Alternativa AMD</TableCell><TableCell align="right">Servidores</TableCell><TableCell align="right">Custo atual de referência</TableCell><TableCell align="right">Custo alternativo</TableCell><TableCell align="right">Saving mensal potencial</TableCell>
        </TableRow></TableHead><TableBody>
          {data.pairs.map((pair) => <TableRow key={`${pair.sku_from}-${pair.sku_to}`}>
            <TableCell>{pair.vendor_from} · {pair.sku_from}</TableCell><TableCell>{pair.vendor_to} · {pair.sku_to}</TableCell>
            <TableCell align="right">{pair.servers.toLocaleString("pt-BR")}</TableCell>
            <TableCell align="right">{money(pair.cost_current_monthly ?? null, pair.currency)}</TableCell>
            <TableCell align="right">{money(pair.cost_target_monthly ?? null, pair.currency)}</TableCell>
            <TableCell align="right">
              {money(pair.saving_monthly, pair.currency)}
            </TableCell>
          </TableRow>)}
          {!data.pairs.length && <TableRow><TableCell colSpan={6}>Sem pares com tarifas comparáveis de Intel e AMD na mesma região.</TableCell></TableRow>}
        </TableBody></Table>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 2 }}>{data.method}</Typography>
      </CardContent></Card>
    </Box>
  );
}
