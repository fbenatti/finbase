import { ReactNode, useMemo } from "react";
import { Autocomplete, Box, Button, Chip, FormControl, InputLabel, MenuItem, Paper, Select, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { Add, BarChart, Numbers, PieChart, ShowChart, TableChart } from "./FinbaseIcons";
import { RowAction } from "./RowAction";
import type { PivotAggregation, PivotFilter, PivotFilterOperator, PivotMeasure, PivotMetadata, PivotSort } from "../services/api";
import type { PivotVisualization } from "./PivotChart";
import { AGGREGATION_LABELS, OPERATOR_LABELS, fieldLabel } from "../utils/pivotLabels";

type Field = { name: string; type: string; numeric: boolean };
type MeasureSort = "none" | PivotSort["direction"] | "only_desc" | "only_asc";
export interface PivotTemplate { id: string; label: string; rows: string[]; cols: string[]; visualization: PivotVisualization; sort?: MeasureSort }

interface Props {
  meta: PivotMetadata | null;
  fields: Field[];
  visualization: PivotVisualization;
  rows: string[];
  cols: string[];
  measures: PivotMeasure[];
  filters: PivotFilter[];
  filterInputs: Record<string, string>;
  measureSort: MeasureSort;
  onVisualization: (value: PivotVisualization) => void;
  onRows: (value: string[]) => void;
  onCols: (value: string[]) => void;
  onMeasure: (value: PivotMeasure) => void;
  onMeasureSort: (value: MeasureSort) => void;
  onFilters: (value: PivotFilter[]) => void;
  onFilterInput: (column: string, input: string) => void;
  onTemplate: (template: PivotTemplate) => void;
}

const VISUALIZATIONS: { value: PivotVisualization; label: string; icon: ReactNode }[] = [
  { value: "table", label: "Tabela", icon: <TableChart fontSize="small" /> },
  { value: "bar", label: "Barras", icon: <BarChart fontSize="small" /> },
  { value: "line", label: "Linha", icon: <ShowChart fontSize="small" /> },
  { value: "pie", label: "Pizza", icon: <PieChart fontSize="small" /> },
  { value: "indicator", label: "Indicador", icon: <Numbers fontSize="small" /> },
];
const PERIOD_NAMES = ["_billing_period", "period", "forecast_month"];
const NO_VALUE_OPERATORS: PivotFilterOperator[] = ["IS_NULL", "IS_NOT_NULL"];

function Step({ number, title, hint, children }: { number: number; title: string; hint?: string; children: ReactNode }) {
  return <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "200px minmax(0,1fr)" }, gap: { xs: 0.75, md: 2 }, alignItems: "start", py: 1.25 }}>
    <Stack direction="row" gap={1} alignItems="flex-start">
      <Box sx={{ width: 22, height: 22, borderRadius: "50%", bgcolor: "primary.main", color: "primary.contrastText", fontSize: 12, fontWeight: 750, display: "grid", placeItems: "center", flexShrink: 0, mt: 0.25 }}>{number}</Box>
      <Box><Typography variant="subtitle2" fontWeight={750}>{title}</Typography>{hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}</Box>
    </Stack>
    <Box sx={{ minWidth: 0 }}>{children}</Box>
  </Box>;
}

export function pivotTemplates(meta: PivotMetadata | null): PivotTemplate[] {
  if (!meta) return [];
  const period = meta.columns.find(column => PERIOD_NAMES.includes(column.name.toLowerCase()))?.name;
  const dims = meta.recommended_dimensions.filter(name => !PERIOD_NAMES.includes(name.toLowerCase()) && name.toLowerCase() !== "origem").slice(0, 3);
  const templates: PivotTemplate[] = [{ id: "total", label: "Total geral", rows: [], cols: [], visualization: "indicator" }];
  if (period) templates.push({ id: "monthly", label: "Evolução mensal", rows: [period], cols: [], visualization: "line" });
  dims.forEach(dim => templates.push({ id: `top-${dim}`, label: `Maiores por ${fieldLabel(dim).toLowerCase()}`, rows: [dim], cols: [], visualization: "bar", sort: "only_desc" }));
  if (period && dims[0]) templates.push({ id: `matrix-${dims[0]}`, label: `${fieldLabel(dims[0])} mês a mês`, rows: [dims[0]], cols: [period], visualization: "table" });
  return templates;
}

export default function PivotGuidedPanel(props: Props) {
  const { meta, fields, visualization, rows, cols, measures, filters, filterInputs, measureSort } = props;
  const measure = measures[0];
  const templates = useMemo(() => pivotTemplates(meta), [meta]);
  const byName = useMemo(() => new Map(fields.map(field => [field.name, field])), [fields]);
  const sortedFields = useMemo(() => [...fields].sort((a, b) => fieldLabel(a.name).localeCompare(fieldLabel(b.name), "pt-BR")), [fields]);
  const usesGroups = visualization !== "indicator";
  const usesSeries = visualization === "table" || visualization === "bar" || visualization === "line";
  const seriesTitle = visualization === "table" ? "Abrir em colunas por" : "Separar séries por";
  const options = (exclude: string[]) => sortedFields.filter(field => !exclude.includes(field.name));

  return <Box>
    {templates.length > 0 && <Stack direction="row" gap={0.75} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
      <Typography variant="caption" color="text.secondary" fontWeight={700}>Comece por um modelo:</Typography>
      {templates.map(template => <Chip key={template.id} size="small" variant="outlined" color="primary" label={template.label} onClick={() => props.onTemplate(template)} />)}
    </Stack>}

    <Step number={1} title="Visualizar como">
      <ToggleButtonGroup exclusive size="small" value={visualization} onChange={(_, value) => value && props.onVisualization(value)} sx={{ flexWrap: "wrap" }}>
        {VISUALIZATIONS.map(item => <ToggleButton key={item.value} value={item.value} sx={{ gap: 0.75, px: 1.5, textTransform: "none" }}>{item.icon}{item.label}</ToggleButton>)}
      </ToggleButtonGroup>
    </Step>

    <Step number={2} title="Calcular" hint="O que será somado ou contado">
      <Stack direction="row" gap={1} flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>Cálculo</InputLabel>
          <Select label="Cálculo" value={measure?.aggregation || "SUM"} onChange={e => measure && props.onMeasure({ ...measure, aggregation: e.target.value as PivotAggregation })}>
            {(Object.keys(AGGREGATION_LABELS) as PivotAggregation[]).map(op => <MenuItem key={op} value={op}>{AGGREGATION_LABELS[op]}</MenuItem>)}
          </Select>
        </FormControl>
        {measure?.aggregation !== "COUNT" && <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>de</InputLabel>
          <Select label="de" value={measure?.column || ""} onChange={e => props.onMeasure({ aggregation: measure?.aggregation || "SUM", column: e.target.value })}>
            {(meta?.measures || []).map(name => <MenuItem key={name} value={name}>{fieldLabel(name)}</MenuItem>)}
          </Select>
        </FormControl>}
        {usesGroups && <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Ordenar</InputLabel>
          <Select label="Ordenar" value={measureSort === "desc" ? "only_desc" : measureSort === "asc" ? "only_asc" : measureSort} onChange={e => props.onMeasureSort(e.target.value as MeasureSort)}>
            <MenuItem value="none">Pela ordem do agrupamento</MenuItem>
            <MenuItem value="only_desc">Maiores valores primeiro</MenuItem>
            <MenuItem value="only_asc">Menores valores primeiro</MenuItem>
          </Select>
        </FormControl>}
      </Stack>
      {measures.length > 1 && <Typography variant="caption" color="text.secondary">Há {measures.length} medidas; o gráfico usa a primeira. Use o modo avançado para editar as demais.</Typography>}
    </Step>

    {usesGroups && <Step number={3} title="Agrupar por" hint="Cada grupo vira uma linha ou barra; a ordem define os níveis">
      <Autocomplete
        multiple size="small" options={options(cols)} value={rows.map(name => byName.get(name) || { name, type: "", numeric: false })}
        getOptionLabel={option => fieldLabel(option.name)} isOptionEqualToValue={(option, value) => option.name === value.name}
        onChange={(_, value) => props.onRows(value.map(item => item.name))}
        renderInput={params => <TextField {...params} placeholder={rows.length ? "Incluir subagrupamento" : "Ex.: Centro de custo"} />}
      />
    </Step>}

    {usesSeries && <Step number={4} title={seriesTitle} hint="Opcional">
      <Autocomplete
        size="small" options={options(rows)} value={cols[0] ? byName.get(cols[0]) || { name: cols[0], type: "", numeric: false } : null}
        getOptionLabel={option => fieldLabel(option.name)} isOptionEqualToValue={(option, value) => option.name === value.name}
        onChange={(_, value) => props.onCols(value ? [value.name] : [])}
        renderInput={params => <TextField {...params} placeholder="Nenhum" helperText={cols.length > 1 ? `Configuração avançada com ${cols.length} campos; escolher aqui substitui todos.` : undefined} />}
      />
    </Step>}

    <Step number={usesGroups ? (usesSeries ? 5 : 4) : 3} title="Filtrar" hint="Todas as condições precisam ser atendidas">
      <Stack spacing={1}>
        {filters.map(filter => {
          const operator = filter.operator || "IN";
          return <Stack key={filter.column} direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Paper variant="outlined" sx={{ px: 1.25, py: 0.85, minWidth: 180, fontSize: 14, fontWeight: 650 }}>{fieldLabel(filter.column)}</Paper>
            <Select size="small" value={operator} sx={{ minWidth: 160 }} onChange={e => {
              const next = e.target.value as PivotFilterOperator;
              props.onFilters(filters.map(item => item.column === filter.column ? { ...item, operator: next, values: NO_VALUE_OPERATORS.includes(next) ? [] : item.values } : item));
            }}>
              {(Object.keys(OPERATOR_LABELS) as PivotFilterOperator[]).map(op => <MenuItem key={op} value={op}>{OPERATOR_LABELS[op]}</MenuItem>)}
            </Select>
            {!NO_VALUE_OPERATORS.includes(operator) && <TextField size="small" sx={{ flex: "1 1 220px" }} placeholder="Valor (separe vários por vírgula)"
              value={filterInputs[filter.column] ?? filter.values.join(", ")} onChange={e => props.onFilterInput(filter.column, e.target.value)} />}
            <RowAction kind="delete" label={`Remover filtro ${fieldLabel(filter.column)}`} onClick={() => props.onFilters(filters.filter(item => item.column !== filter.column))} />
          </Stack>;
        })}
        <Autocomplete
          size="small" sx={{ maxWidth: 360 }} options={options(filters.map(item => item.column))} value={null} blurOnSelect clearOnBlur
          getOptionLabel={option => fieldLabel(option.name)}
          onChange={(_, value) => value && props.onFilters([...filters, { column: value.name, values: [], operator: "IN" }])}
          renderInput={params => <TextField {...params} placeholder="Adicionar condição: escolha um campo" InputProps={{ ...params.InputProps, startAdornment: <Add fontSize="small" sx={{ color: "text.secondary", ml: 0.5 }} /> }} />}
        />
      </Stack>
    </Step>
    {!measure && meta && <Button size="small" startIcon={<Add />} disabled={!meta.default_measure} onClick={() => meta.default_measure && props.onMeasure({ column: meta.default_measure, aggregation: "SUM" })}>Incluir cálculo</Button>}
  </Box>;
}
