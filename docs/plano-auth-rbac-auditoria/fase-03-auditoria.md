# Fase 3 — Auditoria (trilha global) + Histórico de alterações por registro (estilo ServiceNow)

**Pré-requisito:** Fase 2 concluída (auth e RBAC ativos — os registros carregam o usuário autenticado).

## Arquitetura (fixo, não alterar)

Dois **produtos** sobre uma única fonte de dados (`AuditLog`):

1. **Auditoria (global)** — visão de compliance para administradores: "tudo que aconteceu no sistema, por qualquer usuário, em qualquer módulo". Tela própria em Administração, com filtros e pesquisa.
2. **Histórico do registro (embutido)** — visão operacional estilo **ServiceNow "Activity/History"**: uma aba **"Histórico de alterações"** dentro da tela de detalhe/edição de cada registro, mostrando uma **linha do tempo** das mudanças daquele objeto: **quem** fez, **quando**, **qual ação** (criou/editou/excluiu) e **o que mudou campo a campo** (valor anterior → valor novo). Disponível a qualquer usuário com `read` no módulo (não exige acesso ao módulo `audit` — ver regra de autorização na Tarefa 3.4).

Princípios:
- Uma mutação = um evento de histórico. `create` e `delete` também aparecem na timeline (create: "Registro criado", com snapshot inicial colapsável; delete: "Registro excluído", com o último conteúdo).
- O diff campo a campo é **pré-calculado na gravação** (não na consulta) e armazenado junto ao evento — leitura da timeline fica barata e o formato é estável para o frontend.
- Labels amigáveis: o diff usa **nomes de exibição dos campos** (mapa de labels por módulo no frontend; fallback = nome técnico).
- Nada de segredo no histórico: sanitização obrigatória antes de gravar (Tarefa 3.2).

## Tarefa 3.1 — Model `AuditLog`

**Passos:** criar `app/models/audit_log.py`:
- `id` (PK, bigint), `created_at` (indexado),
- `user_id` (FK, nullable para falhas de login), `username` (denormalizado — o histórico deve sobreviver à exclusão/renomeação do usuário), `auth_provider`,
- `module` (string, indexado), `method` (`POST|PUT|PATCH|DELETE|LOGIN|LOGIN_FAILED|LOGOUT`), `path`, `status_code`,
- `entity_type` (string nullable — ex.: `business_rule`), `entity_id` (string nullable),
- `entity_label` (string nullable — identificador legível do registro no momento do evento, ex.: nome da regra ou código da conexão; usado na timeline e em buscas),
- `action_label` (`create`|`update`|`delete`|`execute`|`login`...),
- `before_json` (JSONB nullable — **snapshot completo** antes, sanitizado),
- `after_json` (JSONB nullable — snapshot depois / payload sanitizado),
- `changes_json` (JSONB nullable — **diff pré-calculado**: lista de `{field, old, new}`, apenas campos alterados/adicionados/removidos; `null` quando não aplicável, ex.: login),
- `ip`, `user_agent`, `request_id` (uuid por request).
- Índices: `(module, entity_type, entity_id, created_at)` (alimenta a timeline), `(user_id, created_at)`, `(created_at)`.

**Critérios de aceite:** tabela criada no startup.

## Tarefa 3.2 — Sanitização, serialização e cálculo de diff

**Passos:**
1. Criar `app/core/audit_sanitize.py` com `sanitize_dict(d)`: remove chaves sensíveis — reutilizar/estender o conjunto `_SECRET_CONFIG_KEYS` de `connections.py` e adicionar `password`, `password_hash`, `token`, `refresh_token`, `authorization`, `client_secret`. Recursivo (dicts e listas). Truncar strings > 10.000 chars (marcar `"...[truncated]"`).
2. `to_snapshot(model_instance)`: model SQLAlchemy → dict (colunas apenas), datas em ISO 8601, depois `sanitize_dict`. Nunca serializar objeto vivo com `__dict__` sem remover `_sa_instance_state`.
3. `compute_diff(before, after) -> list[{field, old, new}]`: comparação rasa dos campos de primeiro nível (para dicts aninhados, comparar o sub-objeto inteiro e registrar o campo pai — não descer recursivamente no diff); ignorar campos técnicos (`updated_at`, `last_login_at`) via lista de exclusão configurável; omitir entradas com `old == new`. Limite de segurança: máx. 200 campos por diff.

**Critérios de aceite:** teste unitário: config com `client_secret` some do snapshot; datas em ISO; diff de update que muda 2 campos retorna exatamente 2 entradas; `updated_at` não aparece.

## Tarefa 3.3 — Captura das mutações

**Contexto:** queremos o "antes" real do banco, não só o payload.

**Passos:**
1. **Eventos de auth**: registrar `LOGIN`, `LOGIN_FAILED` (sem senha!), `LOGOUT` no router `auth.py` (sem `changes_json`).
2. **Helper `app/core/audit.py`** com:

```python
async def audit(db, request, user, *, module, entity_type=None, entity_id=None,
                entity_label=None, action_label, before=None, after=None) -> None
```

   que sanitiza, calcula `changes_json = compute_diff(before, after)` e persiste o `AuditLog` na **mesma transação** do endpoint. Falha de auditoria **não** derruba a operação de negócio — capturar exceção, logar erro, seguir.
3. **Aplicar nos endpoints mutadores**, router por router, seguindo o padrão:
   - `POST` (create): `before=None`, `after=snapshot(criado)`, `action_label="create"`.
   - `PUT/PATCH` (update): `before=snapshot(objeto carregado ANTES de aplicar mudanças)`, `after=snapshot(depois)`, `action_label="update"`.
   - `DELETE`: `before=snapshot(objeto)`, `after=None`, `action_label="delete"`.
   - Endpoints "ação" (ex.: disparar ETL, clonar regra): `action_label="execute"`, `after` = payload/parâmetros sanitizados, `entity_type` = alvo.
   - Sempre preencher `entity_label` com o campo mais legível do registro (ex.: `rule.name`, `connection.code`).
   - Ordem de cobertura: `rules`, `connections`, `users`, `groups`, `roles`, `etl`, `planning`, `forecast` primeiro; depois os demais. Listar cobertura por router no PR.
4. `ip` (respeitar `X-Forwarded-For` se configurado) e `user_agent` do request; `request_id` por middleware (uuid em `request.state`).

**Critérios de aceite:** atualizar uma regra gera evento com before/after completos, `changes_json` correto e sem segredos; delete preserva o último conteúdo; login errado gera `LOGIN_FAILED` sem senha; `GET` nunca gera auditoria.

## Tarefa 3.4 — Endpoints de consulta

**Passos:** `app/routers/audit.py`:

1. **Histórico do registro** (alimenta a aba nas telas de negócio):
   `GET /audit/history/{module}/{entity_type}/{entity_id}` — autorização: `read` no **módulo do registro** (`require(module, "read")`), **não** no módulo `audit`. Retorna a timeline ordenada `created_at desc`, cada item já no formato pronto para a UI:

```json
{
  "id": 123,
  "created_at": "2026-05-01T14:32:10Z",
  "user": {"id": 7, "username": "maria.silva", "full_name": "Maria Silva"},
  "action": "update",
  "entity_label": "Regra de rateio GCP",
  "changes": [{"field": "percentage", "old": 30, "new": 40}],
  "has_full_snapshot": true
}
```

   Incluir `GET /audit/history/.../{event_id}` para o detalhe com snapshots completos (lazy — a timeline não carrega `before_json`/`after_json` pesados).
2. **Auditoria global** (aba Administração → Auditoria): exige `read` no módulo `audit`:
   - `GET /audit/logs` — filtros: período (`from`/`to`), `user`, `module`, `entity_type`, `entity_id`, `method`, `action_label`; paginação (limit/offset, default 50, máx 200); ordenação `created_at desc`.
   - `GET /audit/logs/{id}` — detalhe completo com snapshots e diff.

**Critérios de aceite:** usuário com `read` em `rules` (sem acesso a `audit`) consulta o histórico de uma regra; usuário sem `read` em `rules` toma 403; timeline não inclui `before_json`/`after_json` completos (só no detalhe).

## Tarefa 3.5 — Frontend: componente genérico de histórico + telas

**Passos:**
1. **Componente reutilizável** `apps/web/src/components/RecordHistory.tsx` — props `{module, entityType, entityId}`; consome `/audit/history/...`. Layout estilo ServiceNow:
   - timeline vertical (mais recente no topo), cada entrada com: avatar/iniciais, **nome do usuário**, data/hora formatada ("01/05/2026 14:32"), badge da ação (`Criado` verde / `Alterado` azul / `Excluído` vermelho / `Executado` cinza), e a lista de mudanças renderizada campo a campo: **`Label do campo**: `valor antigo` → `valor novo`** (valores longos truncados com expand).
   - mapa de labels por módulo: `src/audit/fieldLabels.ts` (`{ rules: { percentage: "Percentual", ... } }`, fallback = nome técnico);
   - entrada expansível → chama o detalhe do evento e mostra snapshots antes/depois em JSON colapsável (duas colunas, estilo diff viewer simples);
   - estados: loading, vazio ("Nenhuma alteração registrada"), erro 403 (não renderiza a aba).
2. **Aba "Histórico de alterações"** nas telas de detalhe/edição — começar por `rules` e `connections`, depois `users`, `groups`, `roles`, `planning`, `forecast`. A aba só aparece se o usuário tem `read` no módulo (o componente já trata o 403).
3. **Página Auditoria** (menu Administração, `read` em `audit`): tabela paginada com filtros (período, usuário, módulo, entidade, ação) e drawer de detalhe com o mesmo visual do diff.

**Critérios de aceite (cenário ponta a ponta pela UI):** usuário A edita o campo "Percentual" de uma regra de 30 para 40 → usuário B abre a regra → aba "Histórico de alterações" mostra: "Maria Silva — 01/05/2026 14:32 — Alterado — Percentual: 30 → 40". Expandir mostra JSON antes/depois. Criação e exclusão de regra também aparecem como entradas.

## Tarefa 3.6 — Retenção

**Passos:** config `AUDIT_RETENTION_DAYS` (default 365) + job no startup (ou endpoint admin `POST /audit/purge`) que apaga registros mais antigos. Documentar.

## Entregáveis da fase
- `AuditLog` (com `changes_json` e `entity_label`), `app/core/audit.py`, `app/core/audit_sanitize.py`, instrumentação dos mutadores, endpoints `/audit/history/...` e `/audit/logs`, componente `RecordHistory`, abas de histórico, página de auditoria global.
