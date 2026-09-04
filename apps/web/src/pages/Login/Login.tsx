import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Button, Card, CardContent, TextField, Typography, Alert, CircularProgress,
} from "@mui/material";
import { useAuth } from "../../auth/AuthContext";

export default function Login() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  React.useEffect(() => {
    if (user) navigate("/", { replace: true });
  }, [user, navigate]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      navigate("/", { replace: true });
    } catch {
      setError("Usuário ou senha inválidos");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box
      sx={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        bgcolor: "background.default",
      }}
    >
      <Card sx={{ width: 380 }}>
        <CardContent sx={{ p: 4 }}>
          <Typography variant="h5" gutterBottom>OpenFinops</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Entre com suas credenciais
          </Typography>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <form onSubmit={submit}>
            <TextField
              label="Usuário" fullWidth margin="normal" autoFocus
              value={username} onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
            <TextField
              label="Senha" type="password" fullWidth margin="normal"
              value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <Button
              type="submit" variant="contained" fullWidth sx={{ mt: 3 }}
              disabled={busy || !username || !password}
            >
              {busy ? <CircularProgress size={22} /> : "Entrar"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </Box>
  );
}
