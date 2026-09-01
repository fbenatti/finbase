# Fase 1 — Modelos de identidade e autenticação local (JWT + refresh + login)

**Pré-requisito:** nenhum. **Duração estimada:** 1–2 sessões de trabalho da IA.

Nesta fase NÃO se protege nenhum router de negócio ainda — apenas criamos a base de identidade e os endpoints de auth. A proteção vem na Fase 2.

## Tarefa 1.1 — Dependências e configuração

**Contexto:** precisamos de bibliotecas de JWT e hash de senha, e de settings.

**Passos:**
1. Adicionar ao requirements da API: `pyjwt`, `bcrypt` (ou `passlib[bcrypt]`), `python-multipart` (form de login).
2. Em `app/config.py`, adicionar settings (lidos de env, com defaults de dev):
   - `JWT_SECRET_KEY` (obrigatório em produção; em dev gerar warning se default)
   - `JWT_ALGORITHM = "HS256"`
   - `ACCESS_TOKEN_EXPIRE_MINUTES = 60`
   - `REFRESH_TOKEN_EXPIRE_DAYS = 7`
   - `ADMIN_INITIAL_PASSWORD` (sem default seguro; seed falha com mensagem clara se ausente em prod)
   - `ENV = "development"` (`development`|`production`)
   - `AUTH_ENABLED = true` — só pode ser `false` quando `ENV != "production"`
   - `DEV_MASTER_TOKEN = ""` (vazio = desabilitado; quando preenchido e fora de produção, autentica como `dev-admin`)
3. Criar `app/core/__init__.py` e `app/core/security.py` com funções: `hash_password`, `verify_password`, `create_access_token(user, perms)`, `decode_access_token`, `generate_refresh_token` (opaco, `secrets.token_urlsafe(64)`) e `hash_refresh_token` (SHA-256).

**Critérios de aceite:** `python -c "from app.core.security import *"` importa sem erro; `verify_password` confere hash gerado.

## Tarefa 1.2 — Models de identidade

**Contexto:** RBAC precisa de usuários, grupos, roles e vínculos. Criar tudo agora evita retrabalho.

**Passos:** criar `app/models/identity.py` (ou um arquivo por model, seguindo o padrão existente) com SQLAlchemy sobre `Base`:

- `User`: `id` (PK), `username` (único, lowercase), `email`, `full_name`, `password_hash` (nullable — usuário SSO não tem senha), `auth_provider` (`local`|`oidc`|`saml`), `external_id` (nullable, id no IdP), `is_active` (bool), `must_change_password` (bool), `last_login_at`, `created_at`, `updated_at`.
- `Role`: `id`, `name` (único), `description`, `is_system` (bool — roles seed não podem ser deletadas), `created_at`.
- `RolePermission`: `id`, `role_id` (FK), `module` (string), `action` (`read`|`create`|`update`|`delete`). Unique `(role_id, module, action)`.
- `Group`: `id`, `name` (único), `description`, `external_id` (nullable — para mapear grupo do IdP na Fase 4), `created_at`.
- Tabelas de associação (many-to-many): `user_role (user_id, role_id)`, `user_group (user_id, group_id)`, `group_role (group_id, role_id)`.
- **Herança de roles**: `role_role (role_id, parent_role_id)` — a role `role_id` herda todas as permissões de `parent_role_id` (transitivo, profundidade máx. 10). Unique `(role_id, parent_role_id)`.
- `RefreshToken`: `id`, `user_id` (FK), `token_hash` (único), `expires_at`, `revoked_at` (nullable), `replaced_by_id` (nullable — rotação), `created_at`, `user_agent`, `ip`.
- Registrar todos em `app/models/__init__.py`.

**Critérios de aceite:** ao subir a API, as tabelas aparecem no PostgreSQL (`\dt`).

## Tarefa 1.3 — Seed idempotente

**Contexto:** bootstrap com admin e roles de sistema.

**Passos:** estender `app/seed.py` (ou criar `app/seed_identity.py` chamado no startup):
1. Role de sistema `Administrador`: todas as 4 ações (`read`, `create`, `update`, `delete`) em **todos** os módulos da lista do índice (incluir `auth`, `users`, `groups`, `roles`, `audit`).
2. Role de sistema `Leitor Global`: `read` em todos os módulos.
3. Usuário `admin` (se não existir) com senha de `ADMIN_INITIAL_PASSWORD` (hash bcrypt), `must_change_password=True`, role `Administrador`.
4. Usuário sintético `dev-admin` (se não existir): senha aleatória inutilizável, `must_change_password=False`, role `Administrador` — usado pelo bypass de DEV e pelo token master.
5. Seed deve ser **idempotente** (verificar existência antes de inserir).

**Critérios de aceite:** rodar a API duas vezes não duplica nada; `admin` consegue autenticar (após Tarefa 1.4).

## Tarefa 1.4 — Endpoints de autenticação (`app/routers/auth.py`)

**Passos:**
1. `POST /auth/login` (público): body `{username, password}`. Valida usuário local ativo; falha genérica `401 "Credenciais inválidas"` (não distinguir usuário inexistente). Em sucesso: access token JWT + cookie httpOnly `refresh_token`. Atualiza `last_login_at`. Resposta inclui `must_change_password`.
2. `POST /auth/refresh` (público, lê cookie): valida refresh (não revogado, não expirado); **rotação**: revoga o antigo, cria novo; devolve novo access token + novo cookie. Se refresh revogado for reutilizado → revogar **toda** a família do usuário (detecção de roubo).
3. `POST /auth/logout`: revoga o refresh do cookie e limpa o cookie.
4. `POST /auth/change-password` (autenticado): `{current_password, new_password}` com validação de política; limpa `must_change_password`.
5. `GET /auth/me` (autenticado): dados do usuário + lista de permissões efetivas `[{module, action}]`.
6. Criar `app/core/deps.py` com `get_current_user` (valida Bearer JWT, carrega usuário, rejeita inativo). Por ora usado só em `/auth/*`.
7. **Bypass de DEV**: em `get_current_user`, antes de validar o token, verificar: se `settings.AUTH_ENABLED is False` **e** `settings.ENV != "production"` → retornar/criar usuário sintético `dev-admin` (seed na Tarefa 1.3: usuário `dev-admin`, `is_active=True`, role `Administrador`, senha aleatória inutilizável) e seguir. Se `DEV_MASTER_TOKEN` estiver configurado e o header `Authorization: Bearer <token>` casar exatamente com ele (comparação `hmac.compare_digest`) → autenticar como `dev-admin`. Em produção (`ENV=production`), ambos os atalhos são **ignorados** mesmo que configurados, e o startup deve logar um warning se `AUTH_ENABLED=false` estiver presente.

**Critérios de aceite (curl):** login devolve token; `GET /auth/me` com token funciona; token expirado (forçar `exp` curto em teste) retorna 401; refresh rotaciona e o antigo reutilizado derruba a família; senha fraca rejeitada. Com `AUTH_ENABLED=false` e `ENV=development`: qualquer endpoint de `/auth/me` responde como `dev-admin` sem token. Com `DEV_MASTER_TOKEN=abc` configurado: `curl -H "Authorization: Bearer abc" /auth/me` responde como `dev-admin`. Com `ENV=production` e `AUTH_ENABLED=false`: request sem token → 401 (bypass ignorado) e warning no log.

## Tarefa 1.5 — Tela de login (frontend)

**Passos:**
1. Criar `apps/web/src/pages/Login.tsx` com formulário usuário/senha, mensagem de erro genérica.
2. Criar `src/auth/AuthContext.tsx`: guarda access token em **memória** (não localStorage), expõe `user`, `perms`, `login`, `logout`, `hasPerm(module, action)`.
3. Criar wrapper de fetch (`src/auth/api.ts`) que injeta `Authorization` e, em 401, tenta **um** `POST /auth/refresh` (cookie) e repete a chamada; se falhar, redireciona ao login.
4. Rota `/login` pública; demais rotas exigem usuário (redirecionar). Por ora basta guard simples de "logado ou não" — controle por permissão vem na Fase 5.
5. Se `must_change_password`, forçar tela de troca de senha antes de liberar.
6. **Modo DEV**: o `AuthContext` faz `GET /auth/me` no boot; se responder 200 sem login (bypass ativo), marca usuário como autenticado (`dev-admin`) e **pula a tela de login** automaticamente. Nenhum código de DEV deve ser necessário no frontend além disso.

**Critérios de aceite:** fluxo completo login → usar app → token expira → refresh silencioso → logout funciona no navegador.

## Entregáveis da fase
- `app/core/security.py`, `app/core/deps.py`, `app/routers/auth.py`, models de identidade, seed.
- Frontend: Login, AuthContext, api wrapper.
- **Não mexer em routers de negócio nesta fase.**
