import { dataDisplayText } from "../utils/dataPresentation";
import React, { useState } from "react";
import {
  Box, Button, Card, CardContent, TextField, Typography, Alert, CircularProgress,
} from "@mui/material";
import { apiChangePassword } from "../services/api";
import { useAuth } from "./AuthContext";

/** Tela forçada de troca de senha quando must_change_password=true. */
export default function ChangePassword({ onChanged }: { onChanged: () => void }) {
  const { logout, user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next !== confirm) {
      setError("A confirmação não coincide com a nova senha");
      return;
    }
    setBusy(true);
    try {
      await apiChangePassword(current, next);
      onChanged();
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 401) {
        // Sessão perdida (ex.: senha resetada em outra aba) → volta ao login
        await logout();
        return;
      }
      setError(typeof detail === "string" ? detail : "Não foi possível alterar a senha");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Card sx={{ width: 400 }}>
        <CardContent sx={{ p: 4 }}>
          <Typography variant="h6" gutterBottom>Você precisa trocar a senha</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Olá {user?.username ?? ""}, defina uma nova senha para continuar.
            Mínimo de 10 caracteres, com maiúscula, minúscula e dígito.
          </Typography>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
          <form onSubmit={submit}>
            <TextField label="Senha vigente" type="password" fullWidth margin="normal" value={current}
              onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
            <TextField label="Nova senha de acesso" type="password" fullWidth margin="normal" value={next}
              onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
            <TextField label="Repita a nova senha" type="password" fullWidth margin="normal" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
            <Button type="submit" variant="contained" fullWidth sx={{ mt: 3 }}
              disabled={busy || !current || !next || !confirm}>
              {busy ? <CircularProgress size={22} /> : "Alterar senha"}
            </Button>
            <Button fullWidth sx={{ mt: 1 }} onClick={() => logout()}>
              Sair
            </Button>
          </form>
        </CardContent>
      </Card>
    </Box>
  );
}
