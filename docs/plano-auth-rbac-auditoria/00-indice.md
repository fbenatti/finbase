# Plano de Implementação — Autenticação, RBAC e Auditoria (OpenFinops)

> **Para a IA executora:** este plano é dividido em fases. Execute **uma fase por vez**, na ordem numérica. Cada fase tem tarefas numeradas com: *Contexto* (por que), *Passos* (o que fazer, arquivo a arquivo) e *Critérios de aceite* (como validar). **Não pule fases** e não implemente itens de fases futuras antes da hora. Ao final de cada fase, rode os critérios de aceite e reporte o resultado antes de avançar.

## Objetivo

Adicionar ao OpenFinops (FastAPI + PostgreSQL + React/Vite):

1. **Autenticação**: tela de login, token JWT de acesso com duração de **1 hora** + **refresh token**. Suporte a **conta local** (bcrypt) e **SSO corporativo (SAML2 e OIDC)** integrado ao AD cloud.
2. **RBAC**: permissões por **módulo** (cada router da API é um módulo) e por **ação granular**: `read`, `create`, `update`, `delete`. Definição de acesso por **grupos de acesso** ou **nominalmente (por usuário)**. **Roles** agrupam permissões (ex.: "Somente leitura do módulo Rules") e podem ser associadas a usuários e a grupos. **Roles podem herdar de outras roles** (composição em cadeia, com prevenção de ciclo): ex. role "Analista FinOps" inclui role "Leitor Global" + permissões próprias.
3. **Auditoria**: toda operação `POST`, `PUT`, `PATCH` e `DELETE` autenticada gera registro com **snapshot completo antes/depois**, usuário, data/hora, módulo e resultado.
4. **Histórico de alterações (estilo ServiceNow)**: aba "Histórico de alterações" embutida na tela de cada registro, com timeline de quem fez, quando, qual ação e o diff campo a campo (valor anterior → novo) — alimentada pela mesma trilha de auditoria, acessível a quem tem `read` no módulo do registro.

## Convenções do projeto (respeitar!)

- Backend: `apps/api/app/` — FastAPI, SQLAlchemy **async** (`app/database.py` expõe `get_db` e `Base`). **Não existe Alembic**: tabelas são criadas via `Base.metadata.create_all` no startup (`app/main.py`). Novos models devem ser importados em `app/models/__init__.py` para entrarem no `create_all`.
- Routers existentes: `app/routers/*.py`. Cada router = **um módulo** do RBAC. Lista inicial de módulos (derivar do prefixo do router): `connections`, `rules`, `etl`, `analysis`, `forecast`, `planning`, `reservations`, `savings`, `periods`, `destinations`, `schema`, `fact`, `csv_upload`, `clone_rules`, `dimon`, `ai_copilot`, `ai_dashboards`, `ai_provider_dashboards`, `azure_ptu`, `azure_resource_graph`, `azure_tag_policy`, `oci_operational`, `scheduled_shutdown`. Novos módulos de administração: `auth`, `users`, `groups`, `roles`, `audit`.
- Schemas Pydantic: `app/schemas/`.
- Frontend: `apps/web/src/` — React + Vite; páginas em `pages/`, chamadas à API via proxy `:5173 → :8000`.
- Senhas/segredos: nunca logar nem serializar. O router `connections.py` já tem padrão `_SECRET_CONFIG_KEYS` / `_sanitize_config` — auditoria deve aplicar sanitização equivalente.
- Banco de metadados: PostgreSQL (mesma base dos metadados atuais).

## Decisões de arquitetura (já tomadas — não reinventar)

| Tema | Decisão |
|---|---|
| Token de acesso | JWT (HS256), expiração **1h**, claims: `sub` (user id), `username`, `perms` (resumo), `exp`, `iat`, `jti` |
| Refresh token | Opaco (aleatório), persistido em tabela `refresh_token` com hash, expiração 7 dias, **rotação** a cada uso (refresh antigo é revogado) |
| Transporte | Access token no header `Authorization: Bearer`. Refresh token em **cookie httpOnly, SameSite=Lax** (`/auth/refresh`, `/auth/logout`) |
| Senha local | **bcrypt** (passlib ou bcrypt direto). Política: mín. 10 caracteres, 1 maiúscula, 1 minúscula, 1 dígito |
| Bootstrap | Usuário `admin` criado por **seed** (idempotente) com role `Administrador` (todas as permissões). Senha inicial via env `ADMIN_INITIAL_PASSWORD`, troca obrigatória no 1º login |
| RBAC | Permissão = `(module, action)` com `action ∈ {read, create, update, delete}`. Mapeamento HTTP: `GET/HEAD → read`, `POST → create`, `PUT/PATCH → update`, `DELETE → delete` (endpoints "ação", ex. disparar ETL, classificam-se como `create` salvo exceção documentada). Resolução: permissões efetivas = união de (1) roles diretas do usuário, (2) roles herdadas transitivamente (role → role), (3) roles dos seus grupos (também com herança), (4) grants diretos. Nenhuma ação implica outra: `update` **não** implica `read` — mas a UI de edição da role pode marcar `read` automaticamente ao marcar qualquer outra ação (UX, não regra de enforcement) |
| Herança de roles | Tabela `role_role (role_id, parent_role_id)`. A role herda todas as permissões da(s) role(s) pai, recursivamente. **Ciclo proibido**: validar na API ao criar vínculo (rejeitar com 422 se fechar ciclo). Profundidade máxima: 10 |
| Auth em DEV | `AUTH_ENABLED=false` desliga autenticação **somente quando `ENV != production`** (em produção a flag é ignorada e auth é sempre ligada). Com auth desligada, toda request roda como usuário sintético `dev-admin` com todas as permissões — e a **auditoria continua gravando** (username `dev-admin`), mantendo os caminhos de RBAC/auditoria exercitados. Alternativa intermediária: `DEV_MASTER_TOKEN` (Bearer estático, só fora de produção) que autentica como `dev-admin` sem login — usar quando se quer testar a tela de login mas não RBAC. Recomendação: **DEV local usa `AUTH_ENABLED=false`**; token master só para validar fluxos de auth/RBAC |
| SSO | OIDC e SAML2 configuráveis (ambos opcionais). Usuário SSO é provisionado automaticamente (JIT). Grupos do IdP podem mapear para grupos locais |
| Auditoria | Snapshot **completo** antes/depois (JSON), sanitizado (sem segredos), **diff pré-calculado na gravação** (`changes_json`). Dois produtos sobre a mesma trilha: auditoria global (admin) e histórico por registro embutido nas telas (estilo ServiceNow) |
| Rotas públicas | Apenas `/auth/login`, `/auth/refresh`, `/auth/sso/*` e `/health` ficam sem autenticação |

## Ordem das fases

| Fase | Arquivo | Entrega |
|---|---|---|
| 0 | `fase-01-modelos-auth.md` | Models de usuário/grupo/role + autenticação local + JWT + tela de login |
| 1 | `fase-02-rbac.md` | RBAC: permissões por módulo, enforcement na API, telas de admin |
| 2 | `fase-03-auditoria.md` | Auditoria POST/PUT/PATCH/DELETE com snapshot antes/depois + histórico |
| 3 | `fase-04-sso.md` | OIDC + SAML2 com AD cloud, provisionamento JIT, mapeamento de grupos |
| 4 | `fase-05-frontend-e-hardening.md` | Guardas no frontend, UX por permissão, hardening e testes finais |

## Regras para a IA executora

1. Rode a API e valide manualmente (curl) cada endpoint criado antes de marcar a tarefa como concluída.
2. Não altere routers de negócio além do estritamente necessário para adicionar `Depends(...)` de auth/RBAC e o hook de auditoria.
3. Não remova nem enfraqueça o `_sanitize_config` existente; replique o padrão na auditoria.
4. Toda migration de schema é via novo model + `create_all`; para colunas novas em tabelas existentes, use o padrão de migração leve já presente em `app/main.py` (verificar comentário "create_all does not evolve PostgreSQL tables").
5. Commits pequenos por tarefa, mensagem no formato `feat(auth): ...`, `feat(rbac): ...`, `feat(audit): ...`.
6. Se um critério de aceite falhar, corrija antes de avançar — nunca pule.
