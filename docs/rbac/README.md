# RBAC e ACL: visão geral

Modelo de acesso do OpenFinops, inspirado no ServiceNow: perfis, ACLs por tela, tabela, linha e campo, escopos (hierarquia e orçamento) e regras de negócio. O plano por fase está no artifact de aprovação; o inventário do estado inicial está em `00-inventario-fase0.md`.

## Como uma decisão é tomada

1. A rota é mapeada a um módulo (`apps/api/app/core/authz_middleware.py`: `PATH_MODULE` e `AUTHZ_EXTRA_MODULES`). Rota sem módulo é negada (fail closed).
2. O motor de tela escolhe o resultado segundo `ACCESS_ENGINE` (`apps/api/app/core/access/engine.py`):
   - `legacy`: só o RBAC antigo decide (rollback).
   - `shadow` (padrão): o RBAC antigo decide; divergências são logadas em `access.shadow`.
   - `acl`: o motor novo decide. Sem ACLs no banco, cai no legado.
3. Para dados, `decide()` (`apps/api/app/core/access/policy.py`) avalia ACLs: negação explícita vence; sem ACL de permissão é negado; ACLs somadas por OR; condições por AND. O filtro de linha é aplicado com `apply_row_filter` (`apps/api/app/services/access_service.py`).

## Tabelas

| Tabela | Conteúdo |
|---|---|
| `access_acl` | ACL de um perfil: tipo (tela, tabela, campo, regra), recurso, operação (read, create, write, delete), efeito (allow, deny) |
| `access_acl_condition` | filtro de linha: coluna, tipo de escopo, alcance (exact, subtree) |
| `access_scope` | valor de escopo: hierarquia, orçamento, centro de custo |
| `user_scope`, `group_scope` | escopo concedido a pessoa ou grupo, com prazo e motivo |
| `access_business_rule` | regra de negócio (antes da consulta, antes ou depois da gravação) |
| `access_policy_version` | versão global; cada mudança de ACL ou escopo incrementa |

Migração: `apps/api/migrations/add_access_control.sql`. O backfill gera uma ACL por permissão legada: sem escopo, recurso = módulo; com escopo, recurso = módulo.escopo; `update` vira `write`.

## Cache

`access_cache.py` guarda decisões por versão da política. Mudanças de ACL ou escopo invalidam em todos os workers. Mudanças de perfil ou grupo de uma pessoa levam até 60 s (TTL do cache de RBAC).

## Rotas de administração

`/access` (mapeado ao módulo `users`): simulador `GET /access/users/{id}/decision`, escopos, ACLs por perfil, concessão de escopo. Mutações passam pela auditoria automática.

## Explorador SQL

`POST /analysis/query` só aceita SELECT, uma instrução, em tabelas de destino ativas (`apps/api/app/core/sql_guard.py`). Tabelas bronze ficam bloqueadas.

## Views no Trino

`apps/api/app/services/access_views.py` gera `CREATE OR REPLACE VIEW` por escopo. Não foi executado contra um Trino: a validação ainda está pendente.

## Revisão do modo sombra (antes de trocar para `acl`)

Com dados reais, somente leitura:

    cd apps/api
    DATABASE_URL=postgresql+asyncpg://usuario:senha@host:5432/banco ../../.venv/bin/python scripts/access_shadow_report.py > divergencias.csv

O CSV lista cada divergência entre legado e ACL, com o tipo. "negação inesperada" (legado permite, ACL nega) precisa ser zerada antes da troca para `acl`.

## Testes

Sem banco: `pytest tests/test_access_policy.py tests/test_access_parity.py tests/test_access_model.py tests/test_access_views_shadow.py tests/test_sql_guard.py tests/test_route_aliases.py tests/test_authz_route_coverage.py`.

Com Postgres descartável (`TEST_ACCESS_DATABASE_URL`):

    docker run -d --name finbase-rbac-test -e POSTGRES_USER=rbac -e POSTGRES_PASSWORD=rbac -e POSTGRES_DB=rbac_test -p 55432:5432 postgres:16
    TEST_ACCESS_DATABASE_URL=postgresql+asyncpg://rbac:rbac@localhost:55432/rbac_test pytest tests/test_access_integration.py

## Pendências

- Revisão do modo sombra com dados reais e troca do padrão para `acl` (decisão do time).
- Validação das views no Trino.
- Tela de regras de acesso (`Admin.tsx`, aba 4) e alias/`/access` em `main.py`: aguardam o commit consolidado.
- Parametrizar `_where_clause` do pivot (hoje escapa literais; melhoria de endurecimento).
