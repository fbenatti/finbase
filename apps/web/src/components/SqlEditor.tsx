import { useMemo, useRef } from "react";
import { Box } from "@mui/material";

/**
 * SqlEditor — editor de SQL com syntax highlighting (sem dependências).
 *
 * Técnica: um <pre> colorido (tokens) fica atrás de um <textarea> transparente.
 * Ambos compartilham fonte, tamanho, line-height e padding, e o scroll do
 * textarea é espelhado no <pre> para manter o alinhamento.
 */

const KEYWORDS = new Set([
  "select", "from", "where", "group", "by", "order", "having", "limit",
  "join", "left", "right", "inner", "outer", "full", "on", "and", "or",
  "not", "as", "case", "when", "then", "else", "end", "with", "union",
  "all", "distinct", "is", "null", "in", "like", "between", "exists",
  "interval", "true", "false", "asc", "desc", "using", "cross",
  "create", "table", "if", "exists", "drop", "insert", "into", "values",
]);

const FUNCTIONS = new Set([
  "sum", "avg", "min", "max", "count", "coalesce", "greatest", "least",
  "cast", "try_cast", "try", "lower", "upper", "trim", "substr", "replace",
  "date_format", "date_parse", "date_diff", "date_trunc", "last_day_of_month",
  "current_date", "array_agg", "bool_or", "concat", "length", "round",
  "abs", "strpos", "split_part", "contains", "cardinality", "element_at",
  "approx_distinct", "row_number", "rank", "dense_rank", "lag", "lead",
]);

const COLORS = {
  keyword: "#7c3aed",
  function: "#2563eb",
  string: "#059669",
  number: "#d97706",
  comment: "#94a3b8",
  identifier: "#0e7490",
  operator: "#475569",
  default: "#0f172a",
};

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

interface Token {
  text: string;
  cls: keyof typeof COLORS;
}

function tokenizeSql(sql: string): Token[] {
  const tokens: Token[] = [];
  const re = /('(?:[^']|'')*'|"(?:[^"]|"")*"|--[^\n]*|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b|[()\[\],.;=<>+\-*/|]+|\s+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(sql)) !== null) {
    const text = m[0];
    if (/^\s+$/.test(text)) {
      tokens.push({ text, cls: "default" });
    } else if (text.startsWith("--")) {
      tokens.push({ text, cls: "comment" });
    } else if (text.startsWith("'") || text.startsWith('"')) {
      const isString = text.startsWith("'");
      tokens.push({ text, cls: isString ? "string" : "identifier" });
    } else if (/^\d/.test(text)) {
      tokens.push({ text, cls: "number" });
    } else if (/^[A-Za-z_]/.test(text)) {
      const lower = text.toLowerCase();
      if (KEYWORDS.has(lower)) tokens.push({ text, cls: "keyword" });
      else if (FUNCTIONS.has(lower)) tokens.push({ text, cls: "function" });
      else tokens.push({ text, cls: "default" });
    } else {
      tokens.push({ text, cls: "operator" });
    }
  }
  return tokens;
}

function renderTokens(tokens: Token[]): string {
  let out = "";
  for (const t of tokens) {
    if (t.cls === "default") {
      out += escapeHtml(t.text);
    } else {
      out += `<span style="color:${COLORS[t.cls]}">${escapeHtml(t.text)}</span>`;
    }
  }
  return out;
}

export default function SqlEditor({
  value,
  onChange,
  minRows = 10,
  maxRows = 30,
}: {
  value: string;
  onChange: (v: string) => void;
  minRows?: number;
  maxRows?: number;
}) {
  const preRef = useRef<HTMLPreElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const tokens = useMemo(() => tokenizeSql(value), [value]);
  const html = useMemo(() => renderTokens(tokens), [tokens]);

  const syncScroll = () => {
    if (preRef.current && taRef.current) {
      preRef.current.scrollTop = taRef.current.scrollTop;
      preRef.current.scrollLeft = taRef.current.scrollLeft;
    }
  };

  const rows = Math.min(Math.max(value.split("\n").length + 1, minRows), maxRows);

  return (
    <Box
      sx={{
        position: "relative",
        width: "100%",
        borderRadius: 2,
        border: "1px solid #cbd5e1",
        bgcolor: "#0f172a",
        "&:focus-within": { borderColor: "#7c3aed", boxShadow: "0 0 0 3px rgba(124,58,237,0.15)" },
        overflow: "hidden",
      }}
    >
      {/* Camada de fundo: código colorido */}
      <pre
        ref={preRef}
        aria-hidden
        dangerouslySetInnerHTML={{ __html: html + "\n" }}
        style={{
          margin: 0,
          padding: "12px 14px",
          fontFamily: '"JetBrains Mono", "Fira Code", Consolas, Menlo, monospace',
          fontSize: 13,
          lineHeight: 1.65,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          color: "#e2e8f0",
          minHeight: rows * 21.45,
          overflow: "hidden",
          pointerEvents: "none",
        }}
      />
      {/* Camada de frente: textarea transparente */}
      <textarea
        ref={taRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncScroll}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        rows={rows}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          resize: "none",
          border: "none",
          outline: "none",
          background: "transparent",
          color: "transparent",
          caretColor: "#f8fafc",
          fontFamily: '"JetBrains Mono", "Fira Code", Consolas, Menlo, monospace',
          fontSize: 13,
          lineHeight: 1.65,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          overflowWrap: "break-word",
          padding: "12px 14px",
          boxSizing: "border-box",
        }}
      />
    </Box>
  );
}
