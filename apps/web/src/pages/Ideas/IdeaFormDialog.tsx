import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { useState } from "react";
import { Button, FormControl, InputLabel, MenuItem, Select, Stack, TextField } from "@mui/material";
import { createIdea, type IdeaCreateInput, type IdeaImpact, type IdeaType } from "../../services/ideasApi";
import { IMPACT_LABELS, TYPE_LABELS } from "./constants";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const TYPES: IdeaType[] = ["FEATURE", "PAINEL", "BUG", "BUG_CRITICO", "MELHORIA"];
const IMPACTS: IdeaImpact[] = ["BAIXO", "MEDIO", "ALTO", "CRITICO"];
const DIFFS = ["XS", "S", "M", "L", "XL"];

export default function IdeaFormDialog({ open, onClose, onCreated }: Props) {
  const [form, setForm] = useState<IdeaCreateInput>({
    title: "",
    description: "",
    idea_type: "FEATURE",
    impact: "MEDIO",
    difficulty: "M",
  });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await createIdea(form);
      onCreated();
      onClose();
      setForm({ title: "", description: "", idea_type: "FEATURE", impact: "MEDIO", difficulty: "M" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Registrar ideia</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField label="Título da ideia" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
          <TextField label="Contexto e proposta" multiline minRows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <FormControl fullWidth>
            <InputLabel>Natureza</InputLabel>
            <Select label="Natureza" value={form.idea_type} onChange={(e) => setForm({ ...form, idea_type: e.target.value as IdeaType })}>
              {TYPES.map((t) => <MenuItem key={t} value={t}>{TYPE_LABELS[t]}</MenuItem>)}
            </Select>
          </FormControl>
          <Stack direction="row" spacing={2}>
            <FormControl fullWidth>
              <InputLabel>Impacto esperado</InputLabel>
              <Select label="Impacto esperado" value={form.impact} onChange={(e) => setForm({ ...form, impact: e.target.value as IdeaImpact })}>
                {IMPACTS.map((i) => <MenuItem key={i} value={i}>{IMPACT_LABELS[i]}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl fullWidth>
              <InputLabel>Esforço</InputLabel>
              <Select label="Esforço" value={form.difficulty} onChange={(e) => setForm({ ...form, difficulty: e.target.value as IdeaCreateInput["difficulty"] })}>
                {DIFFS.map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
              </Select>
            </FormControl>
          </Stack>
          <TextField label="Prazo desejado" type="date" InputLabelProps={{ shrink: true }} value={form.due_date || ""} onChange={(e) => setForm({ ...form, due_date: e.target.value || undefined })} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={!form.title.trim() || saving} onClick={() => void save()}>Registrar</Button>
      </DialogActions>
    </Dialog>
  );
}
