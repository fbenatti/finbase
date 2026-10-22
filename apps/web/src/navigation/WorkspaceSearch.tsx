import { useEffect, useRef } from "react";
import { Box, IconButton, InputAdornment, TextField, Tooltip, Typography } from "@mui/material";
import { CloseOutlined, SearchOutlined } from "@mui/icons-material";

type Props = {
  value: string; onChange: (value: string) => void; expanded: boolean;
  count: number; focusToken: number; onOpen: () => void; onEnter: () => void;
};

export default function WorkspaceSearch({ value, onChange, expanded, count, focusToken, onOpen, onEnter }: Props) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (expanded && focusToken > 0) input.current?.focus(); }, [focusToken, expanded]);
  if (!expanded) return <Box sx={{ display: "flex", justifyContent: "center", py: 1 }}><Tooltip title="Buscar telas (Ctrl/Cmd + K)"><IconButton aria-label="Abrir busca de telas" onClick={onOpen}><SearchOutlined fontSize="small" /></IconButton></Tooltip></Box>;
  return <Box component="form" role="search" aria-label="Busca de telas" onSubmit={event => { event.preventDefault(); onEnter(); }} sx={{ px: 1.5, py: 1.5 }}>
    <TextField
      inputRef={input} fullWidth size="small" label="Buscar telas" placeholder="Ex.: orçamento, fontes, precisão"
      value={value} onChange={event => onChange(event.target.value)}
      onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onChange(""); } }}
      slotProps={{ input: {
        startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment>,
        endAdornment: value ? <InputAdornment position="end"><IconButton size="small" aria-label="Limpar busca" onClick={() => onChange("")} sx={{ width: 28, height: 28 }}><CloseOutlined sx={{ fontSize: 16 }} /></IconButton></InputAdornment> : undefined,
      }, htmlInput: { autoComplete: "off", "aria-keyshortcuts": "Control+k Meta+k" } }}
    />
    <Typography component="p" variant="caption" color="text.secondary" aria-live="polite" sx={{ mt: 0.75, lineHeight: 1.4 }}>
      {value.trim() ? count ? `${count} ${count === 1 ? "resultado" : "resultados"}` : "Nenhuma tela encontrada para essa busca." : "Encontre qualquer tela, em qualquer nível do menu."}
    </Typography>
  </Box>;
}
