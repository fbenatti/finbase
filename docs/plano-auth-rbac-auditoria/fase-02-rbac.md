# Fase 2 — RBAC: permissões por módulo e enforcement na API

**Pré-requisito:** Fase 1 concluída e validada.

## Conceito (fixo, não alterar)

- Permissão = par `(module, action)`, `action ∈ {read, create, update, delete}` — nenhuma implica outra.
- Mapeamento HTTP → ação: `GET/HEAD → read`, `POST → create`, `PUT/PATCH → update`, `DELETE → delete`. Endpoints "ação" (ex.: `POST /etl/run`) classificam-se como `create`, salvo exceção documentada no endpoint.
- Origens de permissão de um usuário (união de todas):
  1. **Roles diretas** do usuário (`user_role`) — concessão *nominal*;
  2. **Roles dos grupos** do usuário (`user_group` → `group_role`) — concessão *por grupo*;
  3. **Herança de roles** (`role_role`): cada role alcançada em 1 ou 2 traz recursivamente as permissões das roles pai (máx. 10 níveis, ciclos proibidos);
  4. **Grants diretos** (Tarefa 2.3) — concessão nominal fina sem criar role.
- Módulo = prefixo do router (lista no `00-indice.md`).

## Tarefa 2.1 — Resolução de permissões efetivas

**Passos:**
1. Em `app/core/rbac.py`, função async `get_effective_permissions(db, user_id) -> dict[str, set[str]]` (module → actions). Implementar:
   - buscar roles diretas + roles via grupos;
   - **expandir herança**: resolver `role_role` recursivamente (BFS com conjunto de visitados para tolerar dados ruins, limite de profundidade 10);
   - união de `RolePermission` de todas as roles alcançadas + `UserGrant` válidos (Tarefa 2.3).
2. Também expor `get_effective_roles(db, user_id)` (lista de roles alcançadas com origem: `direta|grupo:<nome>|herdada de:<role>`) — usada na tela "Permissões efetivas" para explicar **de onde vem** cada acesso.
3. Cache em memória simples (dict + TTL 60s) com invalidação explícita quando roles/vínculos/herança mudarem. Documentar o TTL.
4. Em `app/core/deps.py`, dependency factory (a ação é passada explicitamente pelo router conforme o mapeamento HTTP):

```python
def require(module: str, action: str):
    async def checker(user = Depends(get_current_user), db = Depends(get_db)):
        perms = await get_effective_permissions(db, user.id)
        if action not in perms.get(module, set()):
            raise HTTPException(403, detail="Sem permissão para esta ação.")
        return user
    return checker
```

**Critérios de aceite:** (a) usuário com role que dá só `read` acessa GET e toma 403 em POST/PUT/DELETE do módulo; (b) cenário de herança: role "Analista" inclui role "Leitor Global"; usuário só com "Analista" lê todos os módulos; remover a herança → perde o acesso (após TTL do cache).

## Tarefa 2.2 — Proteger os routers existentes

**Passos (repetir o padrão em cada router de `app/routers/`):**
1. Em cada endpoint, aplicar o mapeamento HTTP → ação: `GET/HEAD` → `Depends(require("<modulo>", "read"))`; `POST` → `require("<modulo>", "create")`; `PUT/PATCH` → `require("<modulo>", "update")`; `DELETE` → `require("<modulo>", "delete")`.
2. Em routers grandes, preferir dependência no nível do router quando homogêneo: `APIRouter(prefix=..., dependencies=[Depends(require("rules", "read"))])` e adicionar a ação correta (`create`/`update`/`delete`) só nos mutadores.
3. **Não proteger** `/auth/login`, `/auth/refresh`, `/health` (futuro). Manter `/auth/me` e `/auth/change-password` com apenas `get_current_user`.
4. Listar em tabela no próprio PR qual endpoint recebeu qual requisito (módulo/ação) — serve de auditoria do trabalho.

**Critérios de aceite:** sem token → 401 em qualquer endpoint de negócio; com token de usuário sem permissão → 403; admin → tudo funciona como antes (regressão manual das telas principais).

## Tarefa 2.3 — Grant direto nominal (sem role)

**Contexto:** permitir dar exceção pontual a uma pessoa ("João pode escrever em forecast") sem criar role.

**Passos:**
1. Model `UserGrant`: `id`, `user_id` (FK), `module`, `action`, `granted_by` (FK user), `reason` (texto, obrigatório), `expires_at` (nullable), `created_at`. Unique `(user_id, module, action)`.
2. Incluir `UserGrant` na resolução de permissões efetivas (ignorar expirados).
3. Grants entram na auditoria (Fase 3) como qualquer outra mutação.

**Critérios de aceite:** grant direto concede acesso imediato (após TTL do cache); grant expirado deixa de valer.

## Tarefa 2.4 — Routers de administração

Criar routers (módulos `users`, `groups`, `roles`), todos exigindo as ações granulares do respectivo módulo (`read`/`create`/`update`/`delete` conforme o método HTTP):

1. **`app/routers/users.py`**: CRUD de usuários locais (criar com senha temporária + `must_change_password`; ativar/desativar — nunca deletar usuário com histórico, só desativar); reset de senha por admin; `GET /users/{id}/effective-permissions` listando permissões **e a origem de cada uma** (role direta, grupo, herança — usar `get_effective_roles` da Tarefa 2.1); gerenciar grants diretos (`UserGrant`).
2. **`app/routers/groups.py`**: CRUD de grupos; adicionar/remover membros; associar/desassociar roles ao grupo.
3. **`app/routers/roles.py`**: CRUD de roles (roles `is_system` não podem ser deletadas nem ter permissões reduzidas por não-admin); definir permissões da role como lista de `{module, action}`; associar role a usuários; **herança de roles**: `POST /roles/{id}/inherits/{parent_id}` e `DELETE` correspondente, com **validação de ciclo** (rejeitar 422 se fechar ciclo no grafo `role_role`) e `GET /roles/{id}/tree` devolvendo a árvore de herança resolvida.
4. Toda mutação que altera permissões chama a **invalidação de cache** da Tarefa 2.1.
5. Validar `module` contra a lista de módulos conhecida (constante central `app/core/modules.py` derivada da lista do índice) e `action` contra `{read, create, update, delete}`.

**Critérios de aceite (cenário ponta a ponta):** criar role "Leitor Rules" (`read` em `rules`), criar grupo, associar role ao grupo, adicionar usuário de teste ao grupo → usuário lê `rules` e toma 403 ao criar regra. Grant direto de `create` em `rules` para o mesmo usuário → cria regra com sucesso (mas ainda toma 403 em DELETE). Remover grant → volta a 403. Herança: role "Editor Rules" herda "Leitor Rules" + tem `update` → usuário com "Editor Rules" lê e edita; tentativa de fazer "Leitor Rules" herdar "Editor Rules" (que fecharia ciclo) → 422.

## Tarefa 2.5 — Telas de administração (frontend)

**Passos:** seção "Administração" no menu (visível só a quem tem `read` em `users`/`groups`/`roles` — por ora, esconder apenas se 403):
1. **Usuários**: lista, criar/editar/desativar, reset de senha, aba "Grants diretos" e aba "Permissões efetivas".
2. **Grupos**: lista, membros, roles do grupo.
3. **Roles**: lista; editor de permissões em **matriz módulo × ação** (colunas `read`, `create`, `update`, `delete`, linhas = módulos, com checkbox; ao marcar qualquer ação de um módulo sem `read`, marcar `read` automaticamente — UX apenas); painel de **herança** ("esta role herda de:") com seleção de roles pai e visualização da árvore resolvida (`GET /roles/{id}/tree`).
4. **Permissões efetivas do usuário**: tela mostrando matriz final + coluna "origem" (role direta / grupo X / herdada de role Y / grant direto).

**Critérios de aceite:** cenário da Tarefa 2.4 executável inteiramente pela UI.

## Entregáveis da fase
- `app/core/rbac.py`, `app/core/modules.py`, `UserGrant`, routers `users`/`groups`/`roles`, proteção aplicada em todos os routers de negócio, telas de admin.
