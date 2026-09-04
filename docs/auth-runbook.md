# Auth, RBAC e Auditoria — Runbook

## 0. Desenvolvimento sem fricção (LEIA PRIMEIRO)

O caso mais comum em DEV é não querer lidar com login enquanto se trabalha em telas:

- **Recomendado:** `ENV=development` + `AUTH_ENABLED=false` → a API responde como o
  usuário sintético **`dev-admin`** (role Administrador) em toda request. O frontend
  entra direto, **sem tela de login**. A auditoria continua gravando (`username=dev-admin`).
- **Testar telas de login/RBAC sem fluxo de refresh:** mantenha `AUTH_ENABLED=true` e
  defina `DEV_MASTER_TOKEN=<valor>`; use `Authorization: Bearer <valor>` no curl:
  ```bash
  curl -H "Authorization: Bearer <valor>" localhost:8000/auth/me
  ```
- ⚠️ **Os dois atalhos são IGNORADOS em `ENV=production`.** Staging deve rodar com auth ligada.

### Credenciais atuais (banco local de desenvolvimento)

| Usuário | Senha | Observação |
|---|---|---|
| `admin` | `SenhaNova123` | `must_change_password` limpo durante os testes; em banco novo a senha vem de `ADMIN_INITIAL_PASSWORD` (fallback DEV: `admin`, com troca obrigatória no 1º login) |
| `dev-admin` | (inutilizável) | usado só pelo bypass DEV / `DEV_MASTER_TOKEN` |

Para trocar a senha do admin: `POST /auth/change-password` autenticado, ou
`POST /users/{id}/reset-password` (admin).

## 1. Variáveis de ambiente (novas)

| Nome | Obrigatório | Default | Descrição |
|---|---|---|---|
| `ENV` | não | `development` | `development` ou `production`. Ativa guards de segurança |
| `AUTH_ENABLED` | não | `true` | `false` só funciona fora de produção (bypass `dev-admin`) |
| `DEV_MASTER_TOKEN` | não | `""` | Bearer estático que vira `dev-admin`, só fora de produção |
| `ADMIN_INITIAL_PASSWORD` | sim (produção) | `""` | Senha inicial do `admin` no seed; startup falha em produção sem ela |
| `JWT_SECRET_KEY` | sim (produção) | dev-insecure | Startup falha em produção com o valor default |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | não | `60` | Expiração do access token |
| `REFRESH_TOKEN_EXPIRE_DAYS` | não | `7` | Expiração do refresh token (rotação a cada uso) |
| `AUDIT_RETENTION_DAYS` | não | `365` | Purge automático de `audit_log` no startup |
| `ALLOW_LOCAL_LOGIN` | não | `true` | `false` esconde login local na UI (break-glass: `?local=1`) |
| `CORS_ORIGINS` | sim (produção) | `http://localhost:5173,http://localhost:3000` | Origem do frontend |
| `SSO_REDIRECT_URI` | SSO | `""` | URI de callback OIDC quando não configurada no provider |

## 2. Primeiro admin e break-glass

- Banco novo: o seed cria `admin` (senha de `ADMIN_INITIAL_PASSWORD`, `must_change_password=true`)
  com a role de sistema **Administrador** (todas as permissões em todos os módulos).
- Esqueceu a senha? Use o shell com auth desligada (fora de produção) para rodar:
  `POST /users/{id}/reset-password` ou `PUT` direto no `password_hash`.
- Lockout: 5 falhas seguidas bloqueiam a conta local por 15 min (`failed_attempts`, `locked_until`).
  Desbloqueie pelo banco: `UPDATE user_account SET locked_until=NULL, failed_attempts=0 WHERE username='admin';`
  (ou via script com `AUTH_ENABLED=false`).

## 3. Modelo de permissões

Permissão = `(module, action)`, `action ∈ read|create|update|delete`. HTTP: `GET/HEAD→read`,
`POST→create`, `PUT/PATCH→update`, `DELETE→delete`. Origens de um usuário (união):
roles diretas + roles de grupos + herança `role_role` (máx. 10 níveis, anti-ciclo) + grants diretos
(`UserGrant`, com motivo e expiração opcional). Cache de 60 s com invalidação explícita nas mutações.

- **Roles de sistema** (não deletáveis): `Administrador`, `Leitor Global`.
- UI: menu **Administração** (Usuários, Grupos, Roles) e **Auditoria** (só com `read` em `audit`).

Cenário comum "dar leitura de análises para a diretoria X": crie role `Leitor Análise`
(`read` em `analysis`), grupo `Diretoria X`, associe a role ao grupo e adicione os usuários ao grupo.

## 4. SSO

### OIDC (Entra ID)

1. App registration → permissões OpenID `email profile openid`, redirect URI do callback
   (`.../auth/sso/<nome>/callback`).
2. Expor claim `groups` (app manifest `groupMembershipClaims: "SecurityGroup"`).
3. Cadastre o provider: `POST /sso/providers` `{name, protocol:"oidc", config_json:{client_id,
   client_secret, issuer:"https://login.microsoftonline.com/<tenant>/v2.0", groups_claim:"groups",
   auto_create_groups:true}}`. O segredo é cifrado (AES-GCM, chave derivada de `JWT_SECRET_KEY`)
   e nunca retorna na API.
4. `POST /sso/providers/{id}/test` valida o discovery.

### SAML2

Requer **`xmlsec1`** e **`python3-saml`** no servidor (`apt-get install xmlsec1`). Sem eles os
endpoints respondem 501 com instruções. Fluxo: `GET /auth/sso/{p}/login` (redirect binding),
`POST /auth/sso/{p}/acs`, metadata do SP em `GET /auth/sso/metadata`.

### JIT e grupos

Usuário é localizado por `external_id`/`email` e criado automaticamente (sem senha). Grupos com
`external_id` igual ao claim são sincronizados a cada login: grupos ausentes são removidos, grupos
novos podem ser auto-criados (`auto_create_groups`) e ficam **sem roles** até um admin associar.
Mesmo e-mail em local+SSO **não** é fundido automaticamente; vincule `external_id` na tela do usuário.

## 5. Auditoria e histórico

- Toda `POST/PUT/PATCH/DELETE` autenticada grava `audit_log` (sanitizado, sem segredos) com
  snapshot antes/depois e `changes_json` (diff campo a campo) pré-calculado.
- **Timeline por registro**: `GET /audit/history/{module}/{entity_type}/{entity_id}` — exige só
  `read` no módulo do registro. UI: botão de histórico em Conexões; componente
  `RecordHistory` reutilizável (ServiceNow).
- **Visão global**: `GET /audit/logs` (filtros + paginação) — exige `read` em `audit`. UI: menu
  Administração → Auditoria.
- Retenção: `AUDIT_RETENTION_DAYS` (default 365), purge no startup.

## 6. Emergência: desligar auth

Feature flag `AUTH_ENABLED=false` **somente fora de produção**: toda request vira `dev-admin`
(todas as permissões) e a auditoria continua gravando. Não use em produção — lá a flag é ignorada
e o startup ainda exige `JWT_SECRET_KEY` e `ADMIN_INITIAL_PASSWORD`.

## 7. Testes

```bash
apps/api/scripts/test-auth.sh          # unidade: auth + auditoria (sem banco)
cd apps/api && python -m pytest -q     # suíte completa
```
