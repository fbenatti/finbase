import { useEffect, useMemo, useState } from "react";
import { Alert, Autocomplete, Box, Button, Checkbox, Chip, Divider, FormControl, FormControlLabel, Grid, IconButton, InputLabel, List, ListItemButton, ListItemText, MenuItem, Paper, Select, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { Add, ContentCopy, Delete, Save } from "@mui/icons-material";
import { GoldCubeRule, GoldCubeRuleSource, GoldTable, GoldColumn, formatApiError, getGoldCubeRules, getGoldTables, getGoldSchema, saveGoldCubeRule, reprocessGoldCubes, getGoldCubeHistory, GoldCubeHistoryItem, deleteGoldCubeRule, getProcessingLogCubeStatus, rebuildProcessingLogCube, ProcessingLogCubeStatus } from "../../services/api";
import { useNotifications } from "../../context/NotificationContext";
import SqlEditor from "../../components/SqlEditor";

type Condition = { column: string; operator: string; values: string[]; logic?: "AND" | "OR" };
const operators = ["IN", "=", "CONTAINS", "NOT_IN", "NOT_CONTAINS", "EMPTY", "NOT_EMPTY"];
const emptySource = (): GoldCubeRuleSource => ({ gold_tables: [], filter_json: [], value_column: "realizado", percent: 100 });
const emptyRule = (): Partial<GoldCubeRule> => ({ code: "", name: "", description: "", active: true, dimensions: [], measure_columns: [], sql_template: "", snapshot_mode: false, sources: [emptySource()] });

function conditions(source: GoldCubeRuleSource): Condition[] {
  const raw: any = source.filter_json;
  if (Array.isArray(raw)) return raw as Condition[];
  if (raw?.conditions && Array.isArray(raw.conditions)) return raw.conditions as Condition[];
  return [];
}

/** Gold — pivot-style editor: pick Golds, choose dimensions (GROUP BY) and measures (SUM). */
export default function Gold({ embedded }: { embedded?: boolean } = {}) {
  const { enqueueSnackbar } = useNotifications();
  const [rules, setRules] = useState<GoldCubeRule[]>([]);
  const [tables, setTables] = useState<GoldTable[]>([]);
  const [selected, setSelected] = useState<Partial<GoldCubeRule>>(emptyRule());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<GoldCubeHistoryItem[]>([]);
  // Schema of the first selected Gold, used for the pivot autocompletes.
  const [schemaCols, setSchemaCols] = useState<GoldColumn[]>([]);
  const [editorMode, setEditorMode] = useState<"pivot" | "sql">("pivot");
  const [processingLogCube, setProcessingLogCube] = useState<ProcessingLogCubeStatus | null>(null);
  const [rebuildingProcessingLogCube, setRebuildingProcessingLogCube] = useState(false);

  const loadProcessingLogCube = async () => {
    try { setProcessingLogCube(await getProcessingLogCubeStatus()); }
    catch { setProcessingLogCube({ available: false, table: "dest_dm_processing_logs", row_count: 0 }); }
  };

  const rebuildProcessingLogs = async () => {
    setRebuildingProcessingLogCube(true);
    try {
      const result = await rebuildProcessingLogCube();
      enqueueSnackbar(result.message, "info");
      window.setTimeout(() => { void loadProcessingLogCube(); }, 3000);
    } catch (e) { enqueueSnackbar(formatApiError(e, "Não foi possível reconstruir o cubo de logs"), "error"); }
    finally { setRebuildingProcessingLogCube(false); }
  };

  useEffect(() => {
    void loadProcessingLogCube();
    const timer = window.setInterval(() => { void loadProcessingLogCube(); }, 15000);
    return () => window.clearInterval(timer);
  }, []);

  // Schema UNIFICADO de todas as Golds das fontes selecionadas (união de
  // colunas). Antes só o primaryGold (primeira Gold) era consultado, então
  // colunas exclusivas de outras Golds (ex.: rateio_medida na planning) nunca
  // apareciam nas dimensões/medidas do pivot.
  const selectedGolds = useMemo(() => {
    const set = new Set<string>();
    (selected.sources || []).forEach(s => {
      (s.gold_tables || []).forEach(g => set.add(g));
      if (s.gold_table) set.add(s.gold_table);
    });
    return [...set];
  }, [selected]);

  useEffect(() => {
    if (!selectedGolds.length) { setSchemaCols([]); return; }
    let cancelled = false;
    Promise.all(selectedGolds.map(gold => getGoldSchema(gold).catch(() => [] as GoldColumn[])))
      .then(results => {
        if (cancelled) return;
        const byName = new Map<string, GoldColumn>();
        results.flat().forEach(col => { if (!byName.has(col.name)) byName.set(col.name, col); });
        setSchemaCols([...byName.values()]);
      })
      .catch(() => { if (!cancelled) setSchemaCols([]); });
    return () => { cancelled = true; };
  }, [selectedGolds]);

  const load = async () => {
    setLoading(true); setError("");
    try {
      const [ruleList, tableList] = await Promise.all([getGoldCubeRules(), getGoldTables()]);
      setRules(ruleList); setTables(tableList);
      setSelected(current => current.id ? current : (ruleList[0] ? { ...ruleList[0] } : emptyRule()));
      getGoldCubeHistory().then(setHistory).catch(() => setHistory([]));
    } catch (e) { setError(formatApiError(e, "Não foi possível carregar os cubos analíticos")); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const setSource = (index: number, patch: Partial<GoldCubeRuleSource>) => {
    const sources = [...(selected.sources || [])] as GoldCubeRuleSource[];
    sources[index] = { ...sources[index], ...patch };
    setSelected({ ...selected, sources });
  };
  const setConditions = (index: number, next: Condition[]) => setSource(index, { filter_json: next as any });

  const save = async () => {
    setSaving(true); setError("");
    try {
      const saved = await saveGoldCubeRule({ ...selected, sources: (selected.sources || []).map(s => ({ ...s, filter_json: conditions(s) })) });
      setSelected({ ...saved });
      await load();
      enqueueSnackbar('regra de consolidação "' + (saved.name || saved.code || "?") + '" salva com sucesso', "success");
    } catch (e) { setError(formatApiError(e, "Não foi possível salvar a regra de consolidação")); }
    finally { setSaving(false); }
  };

  const startReprocess = async () => {
    setError("");
    try { await reprocessGoldCubes(selected.id ? [selected.id] : []); await load(); enqueueSnackbar("Consolidação de dados enfileirada", "info"); }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
  };

  const removeRule = async (ruleId: number) => {
    const rule = rules.find(r => r.id === ruleId);
    const label = rule?.code || '#' + ruleId;
    if (!window.confirm('Excluir a regra "' + label + '"? A tabela física ' + (rule?.materialization_table || 'dest_dm_cost_...') + ' também será removida do acervo de dados.')) return;
    setError("");
    try {
      await deleteGoldCubeRule(ruleId);
      if (selected.id === ruleId) setSelected(emptyRule());
      await load();
      enqueueSnackbar('Regra "' + label + '" e tabela excluídas', "success");
    } catch (e) { setError(formatApiError(e, "Não foi possível excluir a regra de consolidação")); }
  };

  const cloneRule = (ruleId: number) => {
    const rule = rules.find(r => r.id === ruleId);
    if (!rule) return;
    // Copia tudo, exceto o id (para o save criar uma nova regra via POST) e o
    // gold_code: um novo valor único é SUGERIDO agora (base-copia, -2, ...)
    // e confirmado no save — o backend rejeita code duplicado (409).
    const base = rule.code || "regra";
    let suggested = base + "-copia";
    let n = 2;
    const used = new Set(rules.map(r => r.code));
    while (used.has(suggested)) { suggested = base + "-copia-" + n; n += 1; }
    const copy: Partial<GoldCubeRule> = {
      code: suggested,
      name: (rule.name || base) + " (cópia)",
      description: rule.description ?? null,
      active: true,
      dimensions: [...(rule.dimensions || [])],
      measure_columns: [...(rule.measure_columns || [])],
      sources: (rule.sources || []).map(s => ({
        gold_table: s.gold_table ?? null,
        gold_tables: [...(s.gold_tables || (s.gold_table ? [s.gold_table] : []))],
        filter_json: Array.isArray(s.filter_json) ? JSON.parse(JSON.stringify(s.filter_json)) : s.filter_json,
        value_column: s.value_column,
        percent: s.percent,
      })),
    };
    setSelected(copy);
    setError("");
    enqueueSnackbar('Regra "' + (rule.name || base) + '" clonada — gold_code sugerido: ' + suggested + ' (edite se quiser e salve)', "info");
  };

  const toggleDimension = (col: string) => {
    const dims = selected.dimensions || [];
    const next = dims.includes(col) ? dims.filter(d => d !== col) : [...dims, col];
    setSelected({ ...selected, dimensions: next });
  };
  const toggleMeasure = (col: string) => {
    const meas = selected.measure_columns || [];
    const next = meas.includes(col) ? meas.filter(m => m !== col) : [...meas, col];
    setSelected({ ...selected, measure_columns: next });
  };

  const addDimension = (col: string) => {
    if (!col || (selected.dimensions || []).includes(col)) return;
    setSelected({ ...selected, dimensions: [...(selected.dimensions || []), col] });
  };
  const addMeasure = (col: string) => {
    if (!col || (selected.measure_columns || []).includes(col)) return;
    setSelected({ ...selected, measure_columns: [...(selected.measure_columns || []), col] });
  };

  return <Box sx={{ p: embedded ? 0 : 3 }}>
    <Paper variant="outlined" sx={{ p: 2, mb: 2, display: "flex", gap: 2, alignItems: { xs: "flex-start", md: "center" }, flexDirection: { xs: "column", md: "row" } }}>
      <Box sx={{ flex: 1 }}>
        <Typography variant="subtitle1" fontWeight={700}>Cubo operacional de logs</Typography>
        <Typography variant="body2" color="text.secondary">
          {processingLogCube?.available
            ? `${processingLogCube.table} · ${processingLogCube.row_count.toLocaleString("pt-BR")} execuções · atualização ${processingLogCube.updated_at ? new Date(processingLogCube.updated_at).toLocaleString("pt-BR") : "em andamento"}`
            : "Aguardando a primeira consolidação. A grade de logs usa este cubo analítico; os detalhes completos continuam no histórico de execuções."}
        </Typography>
      </Box>
      <Chip size="small" label={processingLogCube?.available ? "Disponível" : "Aguardando"} color={processingLogCube?.available ? "success" : "warning"} />
      <Button size="small" variant="outlined" disabled={rebuildingProcessingLogCube} onClick={() => void rebuildProcessingLogs()}>
        {rebuildingProcessingLogCube ? "Enfileirando…" : "Reconstruir cubo"}
      </Button>
    </Paper>
    <Typography variant="h4">Cubos de custo (base consolidada)</Typography>
    <Typography color="text.secondary" mb={2}>Escolha as bases consolidadas de origem, defina as dimensões de agrupamento (linhas do pivot) e as medidas a somar (valores). O reprocessamento gera uma tabela pré-agregada dest_dm_cost_&lt;code&gt; — os dashboards consultam apenas essas tabelas materializadas.</Typography>
    <Alert severity="info" sx={{ mb: 2 }}>Fluxo: Bases de origem + filtros → agrupamentos + medidas → <b>dest_dm_cost_&lt;code&gt;</b>. Salvar preserva a regra; o reprocessamento gera a base consolidada.</Alert>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Grid container spacing={2}>
      <Grid item xs={12} md={4}><Paper variant="outlined" sx={{ p: 2, minHeight: 460 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
          <Typography variant="h6">Regras salvas</Typography>
          <Button size="small" startIcon={<Add />} onClick={() => setSelected(emptyRule())}>Nova</Button>
        </Stack>
        {loading ? <Typography color="text.secondary">Carregando…</Typography> : <List dense>
          {rules.map(rule => <ListItemButton key={rule.id} selected={selected.id === rule.id} onClick={() => setSelected({ ...rule })}>
            <ListItemText primary={rule.name} secondary={`${rule.code} · ${rule.materialization_table || "Pendente"} · ${rule.materialization_rows ?? 0} linhas`} />
            <Chip size="small" label={rule.materialization_status === "UPDATED" ? "Atualizada" : rule.materialization_status === "EMPTY" ? "Sem dados" : rule.materialization_status === "ERROR" ? "Com erro" : "Pendente"}
              color={rule.materialization_status === "UPDATED" ? "success" : rule.materialization_status === "ERROR" ? "error" : "warning"} />
            <IconButton size="small" title="Clonar regra (novo gold_code)" disabled={!rule.id} onClick={(e) => { e.stopPropagation(); if (rule.id) cloneRule(rule.id); }}>
              <ContentCopy fontSize="small" />
            </IconButton>
            <IconButton size="small" color="error" title="Excluir regra e tabela" disabled={!rule.id} onClick={(e) => { e.stopPropagation(); if (rule.id) void removeRule(rule.id); }}>
              <Delete fontSize="small" />
            </IconButton>
          </ListItemButton>)}
          {!rules.length && <Typography color="text.secondary">Nenhuma regra criada.</Typography>}
        </List>}
      </Paper></Grid>
      <Grid item xs={12} md={8}><Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6" mb={2}>{selected.id ? "Editar regra" : "Nova regra"}</Typography>
        <ToggleButtonGroup exclusive size="small" value={editorMode} onChange={(_, value: "pivot" | "sql" | null) => { if (value) setEditorMode(value); }} sx={{ mb: 2 }}>
          <ToggleButton value="pivot">Construtor visual</ToggleButton>
          <ToggleButton value="sql">SQL personalizado</ToggleButton>
        </ToggleButtonGroup>
        {selected.materialization_error && <Alert severity="error" sx={{ mb: 2 }}>Falha na última materialização: {selected.materialization_error}</Alert>}
        <Stack spacing={2}>
          <TextField label="gold_code (nome da tabela)" value={selected.code || ""} onChange={e => setSelected({ ...selected, code: e.target.value })}
            helperText={`Tabela gerada: dest_dm_cost_${(selected.code || "code").toLowerCase().replace(/[^a-z0-9_\-]+/g, "_")}`} />
          <TextField label="Nome da regra" value={selected.name || ""} onChange={e => setSelected({ ...selected, name: e.target.value })} />
          <TextField label="Descrição" value={selected.description || ""} onChange={e => setSelected({ ...selected, description: e.target.value })} multiline minRows={2} />
          <FormControl><Select value={selected.active === false ? "inactive" : "active"} onChange={e => setSelected({ ...selected, active: e.target.value === "active" })}>
            <MenuItem value="active">Regra ativa</MenuItem><MenuItem value="inactive">Regra inativa</MenuItem>
          </Select></FormControl>
          <Divider />
          {editorMode === "sql" && (
            <>
              <Alert severity="info">No modo SQL, a query inteira é executada e o resultado vira a tabela <b>dest_dm_cost_{(selected.code || "code").toLowerCase().replace(/[^a-z0-9_\-]+/g, "_")}</b>. Campos recomendados: <code>_billing_period</code>, dimensões e medidas numéricas.</Alert>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={!!selected.snapshot_mode}
                    onChange={(e) => setSelected({ ...selected, snapshot_mode: e.target.checked })}
                  />
                }
                label={
                  <>
                    <b>Modo Snapshot</b> — cada reprocess <b>adiciona uma cópia datada</b> (coluna <code>_snapshot_date</code>) em vez de substituir a tabela. Use para comparar dados entre dias (ex.: delta de tags <code>shutdown=auto</code>).
                  </>
                }
              />
              <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5 }}>Editor SQL com syntax highlighting — clique e edite direto.</Typography>
              <SqlEditor
                value={selected.sql_template || ""}
                onChange={(v) => setSelected({ ...selected, sql_template: v })}
                minRows={10}
                maxRows={28}
              />
            </>
          )}
          {editorMode === "pivot" && (<>
          <Typography variant="subtitle1">Origens (bases consolidadas)</Typography>
          <Typography variant="caption" color="text.secondary">Cada origem pode reunir bases consolidadas com a mesma estrutura. Os registros são unificados, filtrados e agrupados.</Typography>
          {(selected.sources || []).map((source, sourceIndex) => {
            const sourceConditions = conditions(source);
            return <Paper key={sourceIndex} variant="outlined" sx={{ p: 1.5 }}><Stack spacing={1.25}>
              <Typography variant="subtitle2">Origem {sourceIndex + 1}{sourceIndex > 0 ? " (UNION)" : ""}</Typography>
              <FormControl size="small" required><InputLabel>bases consolidadas (mesma estrutura)</InputLabel>
                <Select
                  multiple
                  label="bases consolidadas (mesma estrutura)"
                  value={source.gold_tables && source.gold_tables.length ? source.gold_tables : (source.gold_table ? [source.gold_table] : [])}
                  onChange={e => {
                    const ids = (e.target.value as string[]);
                    setSource(sourceIndex, { gold_tables: ids, gold_table: ids[0] || null });
                  }}
                  renderValue={(ids) => (ids as string[]).map(id => tables.find(t => t.physical_name === id)?.name || id).join(", ")}
                >
                  {tables.map(t => <MenuItem key={t.physical_name} value={t.physical_name}>{t.name} ({t.physical_name})</MenuItem>)}
                </Select>
              </FormControl>
              {sourceConditions.map((condition, conditionIndex) => <Stack key={conditionIndex} direction={{ xs: "column", sm: "row" }} gap={1}>
                {conditionIndex > 0 && <Select size="small" value={condition.logic || "AND"} title="Conector entre filtros" onChange={e => { const next = [...sourceConditions]; next[conditionIndex] = { ...condition, logic: e.target.value as "AND" | "OR" }; setConditions(sourceIndex, next); }} sx={{ minWidth: 76 }}><MenuItem value="AND">E (AND)</MenuItem><MenuItem value="OR">OU (OR)</MenuItem></Select>}
                <TextField size="small" label="Campo" value={condition.column} onChange={e => { const next = [...sourceConditions]; next[conditionIndex] = { ...condition, column: e.target.value }; setConditions(sourceIndex, next); }} fullWidth />
                <Select size="small" value={condition.operator} onChange={e => { const next = [...sourceConditions]; next[conditionIndex] = { ...condition, operator: e.target.value }; setConditions(sourceIndex, next); }}>{operators.map(op => <MenuItem key={op} value={op}>{op}</MenuItem>)}</Select>
                <TextField size="small" label="Valores (vírgula)" value={(condition.values || []).join(", ")} disabled={["EMPTY", "NOT_EMPTY"].includes(condition.operator)} onChange={e => { const next = [...sourceConditions]; next[conditionIndex] = { ...condition, values: e.target.value.split(",").map(v => v.trim()).filter(Boolean) }; setConditions(sourceIndex, next); }} fullWidth />
                <Button color="error" onClick={() => setConditions(sourceIndex, sourceConditions.filter((_, i) => i !== conditionIndex))}><Delete /></Button>
              </Stack>)}
              <Stack direction="row" gap={1}>
                <Button size="small" startIcon={<Add />} onClick={() => setConditions(sourceIndex, [...sourceConditions, { column: "", operator: "IN", values: [], logic: "AND" }])}>Adicionar filtro</Button>
                <Button size="small" color="error" startIcon={<Delete />} onClick={() => setSelected({ ...selected, sources: (selected.sources || []).filter((_, i) => i !== sourceIndex) })}>Remover origem</Button>
              </Stack>
            </Stack></Paper>;
          })}
          <Button variant="outlined" startIcon={<Add />} onClick={() => setSelected({ ...selected, sources: [...(selected.sources || []), emptySource()] })}>Adicionar base à origem</Button>
          <Divider />

          {/* ═══════════ PIVOT: DIMENSÕES (GROUP BY) ═══════════ */}
          <Typography variant="subtitle1">Dimensões de agrupamento (linhas do pivot)</Typography>
          <Typography variant="caption" color="text.secondary">Colunas que agrupam os dados (ex.: _billing_period, organograma_nivel_1, servicename). Vazio → dimensões analíticas padrão.</Typography>
          {!selectedGolds.length && <Alert severity="info" sx={{ mb: 1 }}>Selecione uma base consolidada na origem para ver os campos disponíveis.</Alert>}
          {selectedGolds.length > 0 && <Autocomplete
            size="small"
            options={schemaCols.map(c => c.name)}
            onChange={(_, val) => addDimension(val || "")}
            renderInput={(params) => <TextField {...params} label="Adicionar dimensão (campo da base consolidada)" />}
          />}
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            {(selected.dimensions || []).map(dim =>
              <Chip key={dim} label={dim} color="primary" variant="outlined" onDelete={() => toggleDimension(dim)} />
            )}
            {!(selected.dimensions || []).length && <Typography variant="caption" color="text.secondary">Nenhuma dimensão definida — será usado o padrão analítico.</Typography>}
          </Box>
          <Divider />

          {/* ═══════════ PIVOT: MEDIDAS (SUM) ═══════════ */}
          <Typography variant="subtitle1">Medidas a somar (valores do pivot)</Typography>
          <Typography variant="caption" color="text.secondary">Colunas numéricas somadas na agregação (ex.: realizado, billedcost, effectivecost). Vazio → realizado.</Typography>
          {selectedGolds.length > 0 && <Autocomplete
            size="small"
            options={schemaCols.filter(c => /double|decimal|real|float|int|bigint/i.test(c.type) || /realizado|billedcost|effectivecost|costbrl|consumedquantity|creditamount|contractedcost|listcost/i.test(c.name)).map(c => c.name)}
            onChange={(_, val) => addMeasure(val || "")}
            renderInput={(params) => <TextField {...params} label="Adicionar medida (campo numérico)" />}
          />}
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            {(selected.measure_columns || []).map(m =>
              <Chip key={m} label={m} color="success" variant="outlined" onDelete={() => toggleMeasure(m)} />
            )}
            {!(selected.measure_columns || []).length && <Typography variant="caption" color="text.secondary">Nenhuma medida definida — será usado "realizado".</Typography>}
          </Box>
          <Divider />
          </>)}
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="caption" color="text.secondary">Tabela: {selected.id ? `dest_dm_cost_${selected.code}` : "criada ao salvar e reprocessar"}</Typography>
            <Stack direction="row" gap={1}>
              <Button variant="outlined" startIcon={<ContentCopy />} disabled={!selected.id} onClick={() => selected.id && cloneRule(selected.id)}>Clonar</Button>
              <Button variant="outlined" disabled={!selected.id} onClick={startReprocess}>Reprocessar</Button>
              <Button variant="contained" startIcon={<Save />} disabled={(() => {
                if (saving || !selected.name || !selected.code) return true;
                if (selected.sql_template && selected.sql_template.trim()) return false;
                return (selected.sources || []).some(s => !((s.gold_tables && s.gold_tables.length) || s.gold_table));
              })()} onClick={save}>Salvar regra</Button>
            </Stack>
          </Stack>
        </Stack>
      </Paper></Grid>
    </Grid>
    <Divider sx={{ my: 3 }} />
    <Typography variant="h6" mb={1}>Histórico recente</Typography>
    {history.length === 0 ? <Typography color="text.secondary">Nenhuma execução registrada.</Typography> : history.map(item =>
      <Alert key={item.id} severity={item.status === "FAILED" ? "error" : item.status === "COMPLETED" ? "success" : "info"} sx={{ mb: 0.5 }}>
        #{item.id} · {item.status} · {item.phase || "Sem detalhe"}{item.rows_destination != null ? ` · ${item.rows_destination} linhas` : ""}
      </Alert>)}
  </Box>;
}
