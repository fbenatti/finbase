/**
 * Visões de leitura das regras de classificação:
 *  - Estrutura: árvore organizacional à esquerda, regras da unidade em frases à direita.
 *  - Cobertura: valores reais de um campo e a unidade para onde cada um vai.
 *
 * O casamento de valores replica services/classification_engine.py: texto
 * canônico (sem acento, minúsculo, só letras e números), "=" exato, "~" contém,
 * "!=" diferente, campos diferentes com E e a menor ordem de aplicação vence.
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Box, Button, ButtonBase, Chip, CircularProgress, IconButton, LinearProgress, MenuItem, Select, Stack, ToggleButton,
  ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  AccountTreeOutlined, AddOutlined, ChevronRightOutlined, ContentCopyOutlined, DeleteOutlined, EditOutlined,
  ExpandMoreOutlined, TaskAltOutlined,
} from "@mui/icons-material";
import { ClassificationFieldValue, ClassificationRule, Department, getClassificationFieldValues } from "../../services/api";
import { flattenDepartments } from "../../components/DepartmentTreePicker";
import EmptyState from "../../components/EmptyState";
import { colors, fontFamily, radius } from "../../theme/tokens";

export interface RuleCondition { field: string; operator: string; values: string[] }

const OPERATOR_TEXT: Record<string, string> = { "=": "for", "~": "contiver", "!=": "não for" };

export function ruleConditions(rule: ClassificationRule): RuleCondition[] {
  const raw = Array.isArray(rule.conditions) ? rule.conditions : [];
  const conditions = raw
    .map((c) => ({
      field: String(c.field || ""),
      operator: String(c.operator || c.op || "="),
      values: (Array.isArray(c.values) ? c.values : String(c.values ?? c.value ?? "").split("|")).map((v) => String(v).trim()).filter(Boolean),
    }))
    .filter((c) => c.field && c.values.length);
  if (conditions.length || !rule.source_field || !rule.pattern) return conditions;
  // Regras antigas: source_field + pattern.
  let values = [rule.pattern];
  try {
    const parsed = JSON.parse(rule.pattern);
    if (Array.isArray(parsed)) values = parsed.map(String);
  } catch { /* valor único */ }
  return [{ field: rule.source_field, operator: rule.pattern_type === "exact" ? "=" : "~", values }];
}

const canon = (text: string) => text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");

function conditionMatches(condition: RuleCondition, value: string): boolean {
  const target = canon(value);
  return condition.values.some((raw) => {
    if (raw.trim() === ".*") return true;
    const expected = canon(raw);
    if (!expected) return false;
    if (condition.operator === "=") return target === expected;
    if (condition.operator === "!=") return target !== expected;
    return target.includes(expected);
  });
}

const byPriority = (a: ClassificationRule, b: ClassificationRule) => a.priority - b.priority || a.id - b.id;

/** Valor de um único campo: "rule" quando uma regra só desse campo decide; "partial" quando depende de outros campos. */
export function classifyValue(rules: ClassificationRule[], field: string, value: string, cloud?: string):
  { kind: "rule"; rule: ClassificationRule } | { kind: "partial"; rule: ClassificationRule } | { kind: "none" } {
  // Regra geral (só ".*") manda o resto para "Não classificado": não conta como cobertura.
  const isFallback = (r: ClassificationRule) => ruleConditions(r).every((c) => c.values.every((v) => v.trim() === ".*"));
  for (const rule of [...rules].filter((r) => r.active && !isFallback(r)).sort(byPriority)) {
    if (rule.cloud_scope && cloud && rule.cloud_scope !== cloud) continue;
    const conditions = ruleConditions(rule);
    const own = conditions.filter((c) => c.field === field);
    if (!own.length || !own.every((c) => conditionMatches(c, value))) continue;
    if (own.length === conditions.length) return { kind: "rule", rule };
    return { kind: "partial", rule };
  }
  return { kind: "none" };
}

function RuleSentence({ rule }: { rule: ClassificationRule }) {
  const conditions = ruleConditions(rule);
  if (!conditions.length) return <Typography variant="body2" color="text.secondary">Sem critérios definidos.</Typography>;
  return (
    <Typography component="div" variant="body2" sx={{ color: "text.secondary", lineHeight: 1.9 }}>
      {conditions.map((condition, index) => (
        <Fragment key={index}>
          {index === 0 ? "Quando " : " e "}
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>{condition.field}</Box>
          {" "}{OPERATOR_TEXT[condition.operator] ?? condition.operator}{" "}
          {condition.values.map((value, i, all) => (
            <Fragment key={value + i}>
              {i > 0 && (i === all.length - 1 ? " ou " : ", ")}
              <Box component="span" sx={{
                fontFamily: fontFamily.mono, fontSize: "0.75rem", px: 0.75, py: 0.125, borderRadius: 1,
                bgcolor: colors.brandTint, color: colors.brandDark, whiteSpace: "nowrap",
              }}>{value}</Box>
            </Fragment>
          ))}
        </Fragment>
      ))}
    </Typography>
  );
}

function RuleCard({ rule, unitPath, onEdit, onClone, onDelete }: {
  rule: ClassificationRule;
  unitPath?: string;
  onEdit: (rule: ClassificationRule) => void;
  onClone: (rule: ClassificationRule) => void;
  onDelete: (rule: ClassificationRule) => void;
}) {
  return (
    <Box sx={{
      p: 2, borderRadius: `${radius.surface}px`, border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
      opacity: rule.active ? 1 : 0.6, transition: "border-color 120ms",
      "&:hover": { borderColor: colors.borderStrong }, "&:hover .rule-actions, &:focus-within .rule-actions": { opacity: 1 },
    }}>
      <Stack direction="row" alignItems="flex-start" gap={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap" sx={{ mb: 0.5 }}>
            <Typography variant="body2" fontWeight={700}>{rule.name}</Typography>
            {!rule.active && <Chip size="small" label="Desativada" />}
            {rule.cloud_scope && <Chip size="small" variant="outlined" label={rule.cloud_scope.toUpperCase()} sx={{ height: 20 }} />}
          </Stack>
          <RuleSentence rule={rule} />
          <Stack direction="row" gap={1.5} sx={{ mt: 0.75 }} flexWrap="wrap">
            {unitPath && (
              <Typography variant="caption" color="text.secondary">
                → <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>{unitPath}</Box>
              </Typography>
            )}
            <Typography variant="caption" sx={{ color: colors.textMuted }}>Ordem de aplicação {rule.priority}</Typography>
          </Stack>
        </Box>
        <Stack direction="row" className="rule-actions" sx={{ opacity: { xs: 1, md: 0 }, transition: "opacity 120ms", mt: -0.5, mr: -0.5 }}>
          <Tooltip title="Editar"><IconButton size="small" aria-label="Editar regra" onClick={() => onEdit(rule)}><EditOutlined /></IconButton></Tooltip>
          <Tooltip title="Duplicar"><IconButton size="small" aria-label="Duplicar regra" onClick={() => onClone(rule)}><ContentCopyOutlined /></IconButton></Tooltip>
          <Tooltip title="Excluir"><IconButton size="small" aria-label="Excluir regra" onClick={() => onDelete(rule)} sx={{ "&:hover": { color: colors.error } }}><DeleteOutlined /></IconButton></Tooltip>
        </Stack>
      </Stack>
    </Box>
  );
}

function ruleText(rule: ClassificationRule) {
  return [rule.name, rule.department_name || "", ...ruleConditions(rule).flatMap((c) => [c.field, ...c.values])].join(" ").toLowerCase();
}

export function ClassificationStructureView({ rules, departments, search, onNew, onEdit, onClone, onDelete }: {
  rules: ClassificationRule[];
  departments: Department[];
  search: string;
  onNew: (departmentId: number | null) => void;
  onEdit: (rule: ClassificationRule) => void;
  onClone: (rule: ClassificationRule) => void;
  onDelete: (rule: ClassificationRule) => void;
}) {
  const flat = useMemo(() => flattenDepartments(departments), [departments]);
  const pathById = useMemo(() => new Map(flat.map((d) => [d.id, d.path])), [flat]);
  const term = search.trim().toLowerCase();
  const visibleRules = useMemo(() => (term ? rules.filter((r) => ruleText(r).includes(term)) : rules), [rules, term]);

  const rulesByUnit = useMemo(() => {
    const map = new Map<number | null, ClassificationRule[]>();
    for (const rule of visibleRules) map.set(rule.department_id, [...(map.get(rule.department_id) || []), rule]);
    for (const list of map.values()) list.sort(byPriority);
    return map;
  }, [visibleRules]);

  // Total da subárvore, para mostrar no nó quantas regras há abaixo dele.
  const subtreeCount = useMemo(() => {
    const counts = new Map<number, number>();
    const walk = (node: Department): number => {
      const total = (rulesByUnit.get(node.id)?.length || 0) + (node.children || []).reduce((sum, child) => sum + walk(child), 0);
      counts.set(node.id, total);
      return total;
    };
    departments.forEach(walk);
    return counts;
  }, [departments, rulesByUnit]);

  const [selected, setSelected] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());

  const subtreeIds = useMemo(() => {
    if (selected == null) return null;
    const ids: number[] = [];
    const find = (nodes: Department[]): Department | undefined => {
      for (const node of nodes) {
        if (node.id === selected) return node;
        const hit = find(node.children || []);
        if (hit) return hit;
      }
      return undefined;
    };
    const collect = (node: Department) => { ids.push(node.id); (node.children || []).forEach(collect); };
    const root = find(departments);
    if (root) collect(root);
    return ids;
  }, [selected, departments]);

  const groups = useMemo(() => {
    const ids = subtreeIds ?? flat.map((d) => d.id);
    const result = ids
      .map((id) => ({ id, path: pathById.get(id) || "", rules: rulesByUnit.get(id) || [] }))
      .filter((group) => group.rules.length);
    const orphan = subtreeIds ? [] : rulesByUnit.get(null) || [];
    return orphan.length ? [...result, { id: -1, path: "Sem unidade", rules: orphan }] : result;
  }, [subtreeIds, flat, pathById, rulesByUnit]);

  const toggle = (id: number) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const renderNode = (node: Department, depth: number) => {
    const count = subtreeCount.get(node.id) || 0;
    const hasChildren = (node.children || []).length > 0;
    const isOpen = !collapsed.has(node.id);
    const isSelected = selected === node.id;
    if (term && count === 0) return null;
    return (
      <Box key={node.id}>
        <Stack direction="row" alignItems="center" sx={{
          pl: depth * 1.75, pr: 1, borderRadius: 1.5, minHeight: 34,
          bgcolor: isSelected ? colors.brandSelected : "transparent",
          "&:hover": { bgcolor: isSelected ? colors.brandSelected : colors.rowHover },
        }}>
          <IconButton size="small" aria-label={isOpen ? "Recolher" : "Expandir"} onClick={() => toggle(node.id)}
            sx={{ width: 26, height: 26, visibility: hasChildren ? "visible" : "hidden", color: "text.secondary" }}>
            {isOpen ? <ExpandMoreOutlined sx={{ fontSize: "18px !important" }} /> : <ChevronRightOutlined sx={{ fontSize: "18px !important" }} />}
          </IconButton>
          <ButtonBase onClick={() => setSelected(node.id)} sx={{ flex: 1, minWidth: 0, justifyContent: "space-between", py: 0.75, pr: 0.5, borderRadius: 1, textAlign: "left" }}>
            <Typography variant="body2" noWrap sx={{ fontWeight: isSelected ? 700 : count ? 500 : 400, color: count ? "text.primary" : "text.secondary" }}>
              {node.name}
            </Typography>
            {count > 0 && (
              <Box component="span" sx={{
                ml: 1, minWidth: 22, height: 20, px: 0.75, borderRadius: radius.pill, display: "grid", placeItems: "center",
                fontSize: "0.6875rem", fontWeight: 700, bgcolor: isSelected ? colors.paper : colors.tableHead, color: "text.secondary",
              }}>{count}</Box>
            )}
          </ButtonBase>
        </Stack>
        {hasChildren && isOpen && (node.children || []).map((child) => renderNode(child, depth + 1))}
      </Box>
    );
  };

  const selectedPath = selected != null ? pathById.get(selected) : null;
  const totalShown = groups.reduce((sum, group) => sum + group.rules.length, 0);

  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "300px 1fr" }, gap: 2, alignItems: "start" }}>
      <Box sx={{
        border: "1px solid", borderColor: "divider", borderRadius: `${radius.surface}px`, p: 1, bgcolor: "background.paper",
        position: { md: "sticky" }, top: { md: 16 }, maxHeight: { md: "calc(100vh - 220px)" }, overflowY: "auto",
      }}>
        <ButtonBase onClick={() => setSelected(null)} sx={{
          width: "100%", justifyContent: "space-between", px: 1.5, py: 1, borderRadius: 1.5, mb: 0.5,
          bgcolor: selected == null ? colors.brandSelected : "transparent", "&:hover": { bgcolor: selected == null ? colors.brandSelected : colors.rowHover },
        }}>
          <Stack direction="row" alignItems="center" gap={1}>
            <AccountTreeOutlined sx={{ fontSize: 18, color: colors.brand }} />
            <Typography variant="body2" fontWeight={selected == null ? 700 : 500}>Toda a estrutura</Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary">{visibleRules.length}</Typography>
        </ButtonBase>
        {departments.map((node) => renderNode(node, 0))}
      </Box>

      <Box sx={{ minWidth: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1} flexWrap="wrap" sx={{ mb: 1.5 }}>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="subtitle1" fontWeight={700} noWrap>{selectedPath || "Toda a estrutura"}</Typography>
            <Typography variant="caption" color="text.secondary">
              {totalShown === 1 ? "1 regra" : `${totalShown} regras`}{selected != null ? " nesta unidade e abaixo dela" : ""}
            </Typography>
          </Box>
          <Button size="small" startIcon={<AddOutlined />} onClick={() => onNew(selected)}>
            {selected != null ? "Nova regra nesta unidade" : "Nova regra"}
          </Button>
        </Stack>

        {groups.length === 0 ? (
          <Box sx={{ border: "1px dashed", borderColor: "divider", borderRadius: `${radius.surface}px`, py: 6 }}>
            <EmptyState message={term ? "Nenhuma regra encontrada para a busca." : "Nenhuma regra direciona custo para esta unidade."} />
          </Box>
        ) : (
          <Stack gap={2.5}>
            {groups.map((group) => (
              <Box key={group.id}>
                {(groups.length > 1 || group.id !== selected) && (
                  <Typography variant="caption" sx={{ display: "block", mb: 1, fontWeight: 700, color: "text.secondary", letterSpacing: "0.02em" }}>
                    {group.path}
                  </Typography>
                )}
                <Stack gap={1}>
                  {group.rules.map((rule) => (
                    <RuleCard key={rule.id} rule={rule} onEdit={onEdit} onClone={onClone} onDelete={onDelete} />
                  ))}
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  );
}

type CoverageFilter = "all" | "none" | "classified";

export function ClassificationCoverageView({ rules, departments, destinationTableId, onClassify, onEdit }: {
  rules: ClassificationRule[];
  departments: Department[];
  destinationTableId: number;
  onClassify: (field: string, value: string, cloud?: string) => void;
  onEdit: (rule: ClassificationRule) => void;
}) {
  const pathById = useMemo(() => new Map(flattenDepartments(departments).map((d) => [d.id, d.path])), [departments]);
  const fields = useMemo(() => {
    const counts = new Map<string, number>();
    rules.forEach((rule) => ruleConditions(rule).forEach((c) => counts.set(c.field, (counts.get(c.field) || 0) + 1)));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([field]) => field);
  }, [rules]);
  const [field, setField] = useState("");
  const activeField = field || fields[0] || "";
  const [values, setValues] = useState<ClassificationFieldValue[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<CoverageFilter>("none");

  useEffect(() => {
    if (!activeField) return;
    let cancelled = false;
    setLoading(true);
    getClassificationFieldValues({ destination_table_id: destinationTableId, field: activeField, limit: 200 })
      .then((data) => { if (!cancelled) setValues(data); })
      .catch(() => { if (!cancelled) setValues([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [activeField, destinationTableId]);

  const rows = useMemo(() => values.map((item) => ({ ...item, result: classifyValue(rules, activeField, item.value, item.cloud) })), [values, rules, activeField]);
  const totalLines = rows.reduce((sum, row) => sum + row.count, 0);
  const classifiedLines = rows.filter((row) => row.result.kind === "rule").reduce((sum, row) => sum + row.count, 0);
  const missing = rows.filter((row) => row.result.kind === "none");
  const missingLines = missing.reduce((sum, row) => sum + row.count, 0);
  const coverage = totalLines ? Math.round((classifiedLines / totalLines) * 1000) / 10 : 0;
  const maxCount = Math.max(1, ...rows.map((row) => row.count));
  const shown = rows.filter((row) => filter === "all" || (filter === "none" ? row.result.kind === "none" : row.result.kind !== "none"));

  if (!fields.length) {
    return <EmptyState message="Crie uma regra para ver a cobertura dos valores do campo que ela usa." />;
  }

  const tile = (label: string, value: string, tone?: string) => (
    <Box sx={{ flex: 1, minWidth: 160, p: 2, borderRadius: `${radius.surface}px`, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography sx={{ fontFamily: fontFamily.heading, fontSize: "1.5rem", fontWeight: 700, color: tone || "text.primary", fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
    </Box>
  );

  return (
    <Box>
      <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap" sx={{ mb: 2 }}>
        <Typography variant="body2" color="text.secondary">Campo</Typography>
        <Select size="small" value={activeField} onChange={(e) => setField(String(e.target.value))} sx={{ minWidth: 220, fontFamily: fontFamily.mono, fontSize: "0.8125rem" }}>
          {fields.map((name) => <MenuItem key={name} value={name} sx={{ fontFamily: fontFamily.mono, fontSize: "0.8125rem" }}>{name}</MenuItem>)}
        </Select>
        <Box sx={{ flex: 1 }} />
        <ToggleButtonGroup size="small" exclusive value={filter} sx={{ "& .MuiToggleButton-root": { textTransform: "none" } }} onChange={(_, value: CoverageFilter | null) => value && setFilter(value)}>
          <ToggleButton value="none">Sem classificação ({missing.length})</ToggleButton>
          <ToggleButton value="classified">Classificados ({rows.length - missing.length})</ToggleButton>
          <ToggleButton value="all">Todos ({rows.length})</ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      {loading ? (
        <Stack alignItems="center" sx={{ py: 6 }}><CircularProgress size={28} /></Stack>
      ) : rows.length === 0 ? (
        <Box sx={{ border: "1px dashed", borderColor: "divider", borderRadius: `${radius.surface}px`, py: 6 }}>
          <EmptyState message="Ainda não há valores carregados para este campo. Use Atualizar valores e volte em alguns segundos." />
        </Box>
      ) : (
        <>
          <Stack direction="row" gap={1.5} flexWrap="wrap" sx={{ mb: 2 }}>
            {tile("Linhas classificadas", `${coverage.toLocaleString("pt-BR")}%`, coverage >= 95 ? colors.success : coverage >= 80 ? colors.warning : colors.error)}
            {tile("Valores sem classificação", missing.length.toLocaleString("pt-BR"), missing.length ? colors.error : colors.success)}
            {tile("Linhas sem classificação", missingLines.toLocaleString("pt-BR"))}
          </Stack>
          <LinearProgress variant="determinate" value={coverage} sx={{
            height: 6, borderRadius: radius.pill, mb: 2.5, bgcolor: alpha(colors.error, 0.15),
            "& .MuiLinearProgress-bar": { bgcolor: colors.success, borderRadius: radius.pill },
          }} />

          {shown.length === 0 ? (
            <Box sx={{ border: "1px dashed", borderColor: "divider", borderRadius: `${radius.surface}px`, py: 5, textAlign: "center" }}>
              <TaskAltOutlined sx={{ fontSize: 32, color: colors.success, mb: 1 }} />
              <Typography variant="body2" color="text.secondary">Todos os valores deste campo já têm uma unidade.</Typography>
            </Box>
          ) : (
            <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: `${radius.surface}px`, overflow: "hidden", bgcolor: "background.paper" }}>
              {shown.map((row, index) => {
                const result = row.result;
                return (
                  <Stack key={`${row.cloud}|${row.value}`} direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} gap={{ xs: 1, sm: 2 }} sx={{
                    px: 2, py: 1.25, borderTop: index ? "1px solid" : 0, borderColor: "divider",
                    "&:hover": { bgcolor: colors.rowHover },
                  }}>
                    <Box sx={{ flex: "0 0 32%", minWidth: 0 }}>
                      <Typography noWrap title={row.value} sx={{ fontFamily: fontFamily.mono, fontSize: "0.8125rem" }}>{row.value}</Typography>
                      <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 0.5 }}>
                        <Box sx={{ flex: 1, maxWidth: 160, height: 4, borderRadius: radius.pill, bgcolor: colors.tableHead, overflow: "hidden" }}>
                          <Box sx={{ width: `${(row.count / maxCount) * 100}%`, height: "100%", bgcolor: result.kind === "none" ? colors.error : colors.brandLight }} />
                        </Box>
                        <Typography variant="caption" sx={{ color: colors.textMuted, fontVariantNumeric: "tabular-nums" }}>
                          {row.count.toLocaleString("pt-BR")} linhas{row.cloud ? ` · ${row.cloud.toUpperCase()}` : ""}
                        </Typography>
                      </Stack>
                    </Box>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      {result.kind === "none" ? (
                        <Typography variant="body2" sx={{ color: colors.error, fontWeight: 600 }}>Sem classificação</Typography>
                      ) : (
                        <ButtonBase onClick={() => onEdit(result.rule)} sx={{ display: "block", textAlign: "left", borderRadius: 1, maxWidth: "100%" }}>
                          <Typography variant="body2" noWrap sx={{ fontWeight: 600 }}>
                            {(result.rule.department_id && pathById.get(result.rule.department_id)) || result.rule.department_name || "Sem unidade"}
                          </Typography>
                          <Typography variant="caption" color="text.secondary" noWrap component="div">
                            {result.kind === "partial" ? "Depende de outros campos · " : ""}regra {result.rule.name}
                          </Typography>
                        </ButtonBase>
                      )}
                    </Box>
                    {result.kind === "none" && (
                      <Button size="small" variant="outlined" startIcon={<AddOutlined />} onClick={() => onClassify(activeField, row.value, row.cloud)}>
                        Classificar
                      </Button>
                    )}
                  </Stack>
                );
              })}
            </Box>
          )}
          {values.length >= 200 && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
              Mostrando os 200 valores com mais linhas.
            </Typography>
          )}
        </>
      )}
    </Box>
  );
}
