import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { useEffect, useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, CircularProgress, FormControlLabel, MenuItem, Stack, Step, StepLabel, Stepper, TextField, Typography } from "@mui/material";
import { CheckCircleOutline, OpenInNew } from "@mui/icons-material";
import {
  SourceConnection, DestinationTable, formatApiError, testConnection,
  getDestinationTables, createDestinationTable, getSourceDestMappings,
  createSchedule, runScheduleNow,
} from "../../services/api";

// Assistente pós-cadastro: leva o usuário da fonte recém-criada até a coleta
// agendada. Todo passo pode ser pulado e refeito depois nas telas próprias.

const STEPS = ["Testar acesso", "Tabela de destino", "Agendar coleta", "Pronto"];

const SCHEDULE_OPTIONS = [
  { label: "Todo dia de madrugada", value: "0 3 * * *" },
  { label: "A cada 6 horas", value: "0 */6 * * *" },
  { label: "Uma vez por semana", value: "0 3 * * 1" },
];

type StepResult = "done" | "skipped";

export default function SourceSetupWizard({ connection, onClose }: { connection: SourceConnection | null; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [results, setResults] = useState<StepResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Passo 1
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Passo 2
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [linkedTables, setLinkedTables] = useState<DestinationTable[]>([]);
  const [tableChoice, setTableChoice] = useState<number | "new" | "">("");
  const [newTableName, setNewTableName] = useState("");
  const [chosenTable, setChosenTable] = useState<DestinationTable | null>(null);

  // Passo 3
  const [cron, setCron] = useState(SCHEDULE_OPTIONS[0].value);
  const [runNow, setRunNow] = useState(true);
  const [scheduleSummary, setScheduleSummary] = useState("");

  useEffect(() => {
    if (!connection) return;
    setStep(0); setResults([]); setError(""); setTestResult(null);
    setTableChoice(""); setNewTableName(""); setChosenTable(null);
    setCron(SCHEDULE_OPTIONS[0].value); setRunNow(true); setScheduleSummary("");
    Promise.all([getDestinationTables(), getSourceDestMappings(connection.id)])
      .then(([all, mappings]) => {
        setTables(all);
        const linkedIds = new Set(mappings.map((mapping) => mapping.destination_table_id));
        setLinkedTables(all.filter((table) => linkedIds.has(table.id)));
      })
      .catch(() => setTables([]));
  }, [connection]);

  if (!connection) return null;

  const next = (result: StepResult) => {
    setResults((current) => { const copy = [...current]; copy[step] = result; return copy; });
    setError("");
    setStep((current) => current + 1);
  };

  const runTest = async () => {
    setBusy(true); setError("");
    try {
      const result = await testConnection(connection.id);
      const ok = ["OK", "SUCCESS"].includes(String(result.status).toUpperCase());
      setTestResult({ ok, message: result.message });
    } catch (e: unknown) {
      setTestResult({ ok: false, message: formatApiError(e, "Não foi possível testar o acesso.") });
    } finally {
      setBusy(false);
    }
  };

  const confirmTable = async () => {
    setBusy(true); setError("");
    try {
      let table = tables.find((item) => item.id === tableChoice) ?? null;
      if (tableChoice === "new") {
        table = await createDestinationTable({ name: newTableName.trim() });
        setTables((current) => [...current, table as DestinationTable]);
      }
      setChosenTable(table);
      next("done");
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível usar essa tabela."));
    } finally {
      setBusy(false);
    }
  };

  const confirmSchedule = async () => {
    setBusy(true); setError("");
    try {
      const schedule = await createSchedule({
        name: `Coleta — ${connection.name}`,
        connection_id: connection.id,
        connection_ids: [connection.id],
        cron_expression: cron,
        timezone: "America/Sao_Paulo",
        schedule_type: "collection",
        destination_reprocess: Boolean(chosenTable || linkedTables.length),
        active: true,
      });
      const label = SCHEDULE_OPTIONS.find((option) => option.value === cron)?.label ?? cron;
      if (runNow) await runScheduleNow(schedule.id);
      setScheduleSummary(runNow ? `${label}. A primeira coleta já começou.` : `${label}.`);
      next("done");
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível criar o agendamento."));
    } finally {
      setBusy(false);
    }
  };

  const skipButton = <Button disabled={busy} onClick={() => next("skipped")}>Pular este passo</Button>;
  const hasLinkedTable = linkedTables.length > 0;

  return (
    <Dialog open onClose={() => { if (!busy) onClose(); }} maxWidth="sm" fullWidth>
      <DialogTitle>Configurar “{connection.name}”</DialogTitle>
      <DialogContent>
        <Stepper activeStep={step} alternativeLabel sx={{ mb: 3, mt: 1 }}>
          {STEPS.map((label, index) => (
            <Step key={label} completed={results[index] === "done"}>
              <StepLabel optional={results[index] === "skipped" ? <Typography variant="caption" color="text.secondary">Pulado</Typography> : undefined}>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {step === 0 && (
          <Stack gap={2}>
            <Typography>Vamos conferir se o Finbase consegue acessar essa fonte com os dados que você informou.</Typography>
            {testResult && <Alert severity={testResult.ok ? "success" : "warning"}>{testResult.ok ? "Acesso funcionando." : "O acesso falhou. Você pode corrigir os dados da fonte depois e testar de novo."} {testResult.message}</Alert>}
          </Stack>
        )}

        {step === 1 && (
          <Stack gap={2}>
            {hasLinkedTable ? (
              <Alert severity="success">Esta fonte já grava em: {linkedTables.map((table) => table.name).join(", ")}.</Alert>
            ) : (
              <>
                <Typography>Escolha onde os dados dessa fonte vão ficar. Se ainda não sabe, pode pular e decidir depois.</Typography>
                <TextField select label="Tabela de destino" value={tableChoice} onChange={(e) => setTableChoice(e.target.value === "new" ? "new" : Number(e.target.value))}>
                  {tables.map((table) => <MenuItem key={table.id} value={table.id}>{table.name}</MenuItem>)}
                  <MenuItem value="new">+ Criar uma tabela nova</MenuItem>
                </TextField>
                {tableChoice === "new" && <TextField label="Nome da nova tabela" value={newTableName} onChange={(e) => setNewTableName(e.target.value)} autoFocus />}
                <Typography variant="body2" color="text.secondary">Depois, em Catálogo de dados › Relações entre tabelas, você liga os campos da fonte aos campos da tabela.</Typography>
              </>
            )}
          </Stack>
        )}

        {step === 2 && (
          <Stack gap={2}>
            <Typography>De quanto em quanto tempo o Finbase deve buscar dados novos?</Typography>
            <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
              {SCHEDULE_OPTIONS.map((option) => (
                <Chip key={option.value} label={option.label} color={cron === option.value ? "primary" : "default"} variant={cron === option.value ? "filled" : "outlined"} onClick={() => setCron(option.value)} />
              ))}
            </Box>
            <FormControlLabel control={<Checkbox checked={runNow} onChange={(e) => setRunNow(e.target.checked)} />} label="Fazer a primeira coleta agora" />
          </Stack>
        )}

        {step === 3 && (
          <Stack gap={1.5}>
            <Stack direction="row" gap={1} alignItems="center"><CheckCircleOutline color="success" /><Typography variant="h6">Fonte cadastrada</Typography></Stack>
            <Summary label="Acesso" value={results[0] === "done" ? "testado" : "não testado"} />
            <Summary label="Tabela de destino" value={chosenTable?.name ?? (hasLinkedTable ? linkedTables.map((table) => table.name).join(", ") : "não definida")} />
            <Summary label="Coleta" value={results[2] === "done" ? scheduleSummary : "não agendada"} />
            {results.includes("skipped") && <Typography variant="body2" color="text.secondary">Os passos pulados podem ser feitos depois:</Typography>}
            <Stack direction="row" gap={1} flexWrap="wrap">
              {results[1] !== "done" || chosenTable ? <Button size="small" endIcon={<OpenInNew />} href="/dados/catalogo/relacionamentos" target="_blank">Ligar campos à tabela</Button> : null}
              {results[2] !== "done" && <Button size="small" endIcon={<OpenInNew />} href="/operacoes/agendamentos" target="_blank">Agendamentos</Button>}
            </Stack>
          </Stack>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        {step === 0 && <>
          {skipButton}
          {testResult?.ok
            ? <Button variant="contained" onClick={() => next("done")}>Continuar</Button>
            : <Button variant="contained" disabled={busy} startIcon={busy ? <CircularProgress size={16} /> : undefined} onClick={() => void runTest()}>{testResult ? "Testar de novo" : "Testar acesso"}</Button>}
        </>}
        {step === 1 && <>
          {!hasLinkedTable && skipButton}
          {hasLinkedTable
            ? <Button variant="contained" onClick={() => next("done")}>Continuar</Button>
            : <Button variant="contained" disabled={busy || tableChoice === "" || (tableChoice === "new" && !newTableName.trim())} onClick={() => void confirmTable()}>Usar esta tabela</Button>}
        </>}
        {step === 2 && <>
          {skipButton}
          <Button variant="contained" disabled={busy} startIcon={busy ? <CircularProgress size={16} /> : undefined} onClick={() => void confirmSchedule()}>Agendar</Button>
        </>}
        {step === 3 && <Button variant="contained" onClick={onClose}>Concluir</Button>}
      </DialogActions>
    </Dialog>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" gap={1}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 140 }}>{label}</Typography>
      <Typography variant="body2">{value}</Typography>
    </Stack>
  );
}
