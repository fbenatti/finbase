import React, { useEffect, useMemo, useState } from "react";
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
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<SsoPublicProvider[]>([]);
  const [config, setConfig] = useState<LoginConfig>({ allow_local_login: true });
  const params = new URLSearchParams(window.location.search);
  const breakGlass = params.get("local") === "1";
  const showLocal = config.allow_local_login || breakGlass;

  useEffect(() => {
    if (user) navigate("/", { replace: true });
    api.get("/auth/sso/providers").then((r) => setProviders(r.data)).catch(() => {});
    api.get("/auth/login-config").then((r) => setConfig(r.data)).catch(() => {});
  }, [user, navigate]);

  // Usuários locais nunca têm "@" no username; contas corporativas (OIDC) são
  // sempre um e-mail. Isso basta para decidir o fluxo sem uma segunda tela.
  const isCorporate = identifier.includes("@");
  const oidcProvider = useMemo(() => providers.find((p) => p.protocol === "oidc"), [providers]);
  const samlProviders = useMemo(() => providers.filter((p) => p.protocol === "saml"), [providers]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (isCorporate) {
      if (!oidcProvider) {
        setError("Login com conta corporativa não está disponível no momento.");
        return;
      }
      const query = `?login_hint=${encodeURIComponent(identifier.trim())}`;
      window.location.href = `/api/auth/sso/${encodeURIComponent(oidcProvider.name)}/login${query}`;
      return;
    }

    if (!showLocal) {
      setError("Login local desabilitado. Use seu e-mail corporativo.");
      return;
    }

    setBusy(true);
    try {
      await login(identifier, password);
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

  const samlLogin = (provider: SsoPublicProvider) => {
    setError(null);
    window.location.href = `/api/auth/sso/${encodeURIComponent(provider.name)}/login`;
  };

  const canSubmit = isCorporate ? Boolean(identifier.trim()) : Boolean(identifier && password);

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
            Entre com seu usuário local ou e-mail corporativo
          </Typography>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <form onSubmit={submit}>
            <TextField
              label="Usuário ou e-mail corporativo" fullWidth margin="normal" autoFocus
              value={identifier} onChange={(e) => setIdentifier(e.target.value)}
              autoComplete="username"
              helperText={isCorporate ? "Você será redirecionado para o login corporativo." : undefined}
            />
            {!isCorporate && (
              <TextField
                label="Senha" type="password" fullWidth margin="normal"
                value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            )}
            <Button
              type="submit" variant="contained" fullWidth sx={{ mt: 3 }}
              disabled={busy || !canSubmit || (!isCorporate && !showLocal)}
            >
              {busy ? <CircularProgress size={22} /> : isCorporate ? "Continuar com conta corporativa" : "Entrar"}
            </Button>
          </form>
          {!isCorporate && !showLocal && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", textAlign: "center", mt: 1.5 }}>
              Login local desabilitado. {breakGlass ? "(modo break-glass)" : "Acesse com seu e-mail corporativo."}
            </Typography>
          )}
          {samlProviders.length > 0 && (
            <>
              <Divider sx={{ my: 2 }}>ou</Divider>
              {samlProviders.map((p) => (
                <Button
                  key={p.name} fullWidth variant="outlined" sx={{ mb: 1.5, py: 1.2 }}
                  onClick={() => samlLogin(p)}
                >
                  {`Entrar com ${p.name}`}
                </Button>
              ))}
            </>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
