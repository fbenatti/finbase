import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Button, Card, CardContent, TextField, Typography, Alert, CircularProgress, Divider,
} from "@mui/material";
import { useAuth } from "../../auth/AuthContext";
import { api } from "../../services/api";

interface SsoPublicProvider { name: string; protocol: string; username_domain?: string }
interface LoginConfig { allow_local_login: boolean }

export default function Login() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<SsoPublicProvider[]>([]);
  const [ssoIdentifier, setSsoIdentifier] = useState("");
  const [config, setConfig] = useState<LoginConfig>({ allow_local_login: true });
  const params = new URLSearchParams(window.location.search);
  const breakGlass = params.get("local") === "1";
  const showLocal = config.allow_local_login || breakGlass;

  useEffect(() => {
    if (user) navigate("/", { replace: true });
    api.get("/auth/sso/providers").then((r) => setProviders(r.data)).catch(() => {});
    api.get("/auth/login-config").then((r) => setConfig(r.data)).catch(() => {});
  }, [user, navigate]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      navigate("/", { replace: true });
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 423) {
        setError("Conta temporariamente bloqueada por tentativas inválidas. Tente novamente em alguns minutos.");
      } else if (detail === "Conta temporariamente bloqueada por tentativas inválidas") {
        setError("Conta temporariamente bloqueada por tentativas inválidas. Tente novamente em alguns minutos.");
      } else if (status === 429) {
        setError("Muitas tentativas. Aguarde um minuto antes de tentar de novo.");
      } else {
        setError("Usuário ou senha inválidos");
      }
    } finally {
      setBusy(false);
    }
  };

  const ssoLogin = (provider: SsoPublicProvider) => {
    setError(null);
    if (provider.protocol === "oidc" && !ssoIdentifier.trim()) {
      setError("Informe seu usuário corporativo para continuar.");
      return;
    }
    const query = provider.protocol === "oidc"
      ? `?login_hint=${encodeURIComponent(ssoIdentifier.trim())}`
      : "";
    window.location.href = `/api/auth/sso/${encodeURIComponent(provider.name)}/login${query}`;
  };

  const oidcProviders = providers.filter((p) => p.protocol === "oidc");

  return (
    <Box
      sx={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        bgcolor: "background.default",
      }}
    >
      <Card sx={{ width: 400 }}>
        <CardContent sx={{ p: 4 }}>
          <Typography variant="h5" gutterBottom>OpenFinops</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Entre com suas credenciais
          </Typography>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          {oidcProviders.length > 0 && (
            <Box sx={{ mb: 1.5 }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Conta corporativa</Typography>
              <TextField
                label="Usuário ou e-mail" fullWidth autoFocus
                value={ssoIdentifier} onChange={(e) => setSsoIdentifier(e.target.value)}
                autoComplete="username"
                helperText="O domínio informado será substituído automaticamente pelo domínio corporativo."
              />
            </Box>
          )}
          {providers.map((p) => (
            <Button
              key={p.name} fullWidth variant="outlined" sx={{ mb: 1.5, py: 1.2 }}
              onClick={() => ssoLogin(p)}
            >
              {p.protocol === "oidc" ? "Continuar com conta corporativa" : `Entrar com ${p.name}`}
            </Button>
          ))}
          {providers.length > 0 && showLocal && <Divider sx={{ my: 2 }}>ou</Divider>}
          {showLocal ? (
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
          ) : (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", textAlign: "center" }}>
              Login local desabilitado. {breakGlass ? "(modo break-glass)" : "Acesse com SSO corporativo."}
            </Typography>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
