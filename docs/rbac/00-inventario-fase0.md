# RBAC: inventário da Fase 0

Estado do código no branch `layot_carbon`, antes de qualquer alteração de comportamento.

## 1. Como o acesso é aplicado hoje

| Camada | Arquivo | O que faz | Limite |
|---|---|---|---|
| Dependência | `apps/api/app/core/deps.py` (`require`) | Exige `(módulo, ação, escopo?)` por rota | Só nas rotas que a declaram |
| Middleware | `apps/api/app/core/authz_middleware.py` | Mapeia prefixo de URL para módulo; GET→read, POST→create, PUT/PATCH→update, DELETE→delete | Sem filtro de linha; POST de ação não distingue leitura de comando |
| RBAC | `apps/api/app/core/rbac.py` | Resolve perfis (direto, grupo, herança) e grants; cache de 60 s por processo | Cache não é compartilhado entre workers |
| Front | `apps/web/src/auth/moduleMap.ts` | Mapa rota → módulo para menu e guard | Manual; rotas fora do mapa não são verificadas |
| Catálogo de telas | `apps/web/src/pages/Admin/accessCatalog.ts` | Telas da matriz de perfis | Mantido à mão, separado do mapa acima |

Módulos RBAC: `apps/api/app/core/modules.py` (`ALL_MODULES`, `ALL_ACTIONS`). A lista inclui `finops`, que não aparece no `PATH_MODULE` do middleware.

Modelos: `role_permission(module, action, scope)`, `user_grant` (com motivo e `expires_at`), `role_role` (herança), `group_role`, `user_group`.

## 2. Cobertura por router

Rotas contadas por `@router.` e uso explícito de `require(`:

| Router | Rotas | `require()` | Coberto pelo middleware |
|---|---:|---:|:---:|
| forecast | 45 | 0 | sim (`/forecast`) |
| etl | 40 | 0 | sim (`/etl`) |
| analysis | 31 | 0 | sim (`/analysis`) |
| azure_ptu | 31 | 0 | sim (`/visoes-consumo-ia`) |
| planning | 29 | 12 | sim (`/planning`) |
| rules | 26 | 0 | sim (`/mappings`, `/business-rules`, `/classifications`, `/departments`) |
| scheduled_shutdown | 22 | 0 | sim |
| anomalies | 24 | 24 | sim (mas também tem `require` próprio) |
| ideas | 14 | 0 | sim |
| destinations | 12 | 0 | sim |
| gold | 15 | 0 | sim (`/gold`) |
| reservations | 8 | 0 | sim |
| savings | 9 | 0 | sim |
| ai_copilot | 9 | 0 | sim |
| clone_rules | 8 | 0 | sim |
| financial_contracts | 8 | 0 | **não** |
| azure_tag_policy | 10 | 0 | sim |
| users | 10 | 10 | sim |
| roles | 9 | 9 | sim |
| groups | 9 | 9 | sim |
| modules | 9 | 7 | sim |
| audit | 4 | 2 | **não** |
| sso_providers | 5 | 5 | **não** |
| sso | 6 | 0 | **não** (público em parte) |
| auth | 6 | 0 | público, por design |
| connections | 6 | 0 | sim (`/connections`) |
| connector_visibility | 2 | 1 | **não** |
| csv_upload | 5 | 0 | sim |
| processing_logs | 4 | 0 | **não** |
| azure_sources | 5 | 0 | **não** |
| azure_mca_commitment | 4 | 0 | **não** |
| azure_resource_graph | 6 | 0 | sim |
| oci_operational | 5 | 0 | sim |
| integrations | 4 | 0 | sim (`/integrations`) |
| periods | 5 | 0 | sim (`/etl/periods`) |
| schema | 1 | 0 | sim |
| fact | 4 | 0 | sim |
| ai_dashboards | 4 | 0 | sim |
| ai_provider_dashboards | 2 | 0 | sim |

Pontos de atenção:
- Routers sem `require()` dependem só do middleware. Um GET é `read` para o módulo inteiro, sem escopo.
- Routers fora do `PATH_MODULE` (`audit`, `processing_logs`, `connector_visibility`, `financial_contracts`, `azure_sources`, `azure_mca_commitment`, `sso`) não têm controle pelo middleware. A cobertura depende só de `require()` onde existe.
- Nenhuma consulta de dados aplica filtro de linha por escopo.

## 3. Mapa de rotas: front × API

O front usa rotas de produto (`/dados/fontes`, `/planejamento`, `/custos`). A API usa nomes internos (`/connections`, `/planning`, `/gold`, `/etl`, `/rules`). Isso impede a regra "a chamada do backend segue a rota do front", pedida pelo Fernando.

| Rota do front (ACL de tela) | Módulo | Prefixo atual da API |
|---|---|---|
| `/dados/fontes` | connections | `/connections`, `/integrations` |
| `/dados/catalogo` | destinations | `/destinations` |
| `/dados/explorador` | analysis | `/analysis` |
| `/dados/fluxos` | etl | `/etl` |
| `/dados/mapeamentos` | rules | `/mappings` |
| `/operacoes/dados`, `/operacoes/execucoes`, `/operacoes/custos-compartilhados`, `/operacoes/extracoes` | etl | `/etl`, `/etl/periods` |
| `/projecoes`, `/projecoes/precisao` | forecast | `/forecast` |
| `/custos` | gold | `/gold` |
| `/planejamento` | planning | `/planning` |
| `/otimizacao` | savings | `/savings` |
| `/reservations` | reservations | `/reservations` |
| `/governanca` (abas) | planning, rules, azure_tag_policy, gold, reservations | `/planning`, `/mappings`, `/azure-tag-policy`, `/gold`, `/reservations` |
| `/iniciativas` | ideas | `/ideas` |
| `/alertas` | anomalies | `/anomalies` |
| `/administracao/acessos` | users, groups, roles | `/users`, `/groups`, `/roles` |
| `/administracao/identidade` | auth | `/auth/sso`, `/sso/providers` |
| `/administracao/auditoria` | audit | `/audit` |

Decisão adotada para seguir com o plano (aprovada pelo Fernando na mensagem de 20:24Z): as rotas novas da API seguem a árvore do front. Os prefixos atuais continuam como aliases durante a migração (Fase 6) e são removidos depois do corte.

## 4. Lacunas que a Fase 1 e 2 vão resolver

1. Sem ACL de tabela, linha ou campo.
2. Sem escopo de hierarquia ou de orçamento (orçamento só existe como módulo `planning`).
3. Sem regra de negócio antes ou depois de gravar.
4. Sem negação explícita: sem permissão é negado, mas grant não tem efeito `deny`.
5. Cobertura desigual: alguns routers não têm nenhuma verificação.
6. Front e API mantêm listas separadas de telas (`moduleMap.ts` e `accessCatalog.ts`).

## 5. Testes de caracterização a criar antes da Fase 1

- 403 por módulo para cada router com `require()`.
- Herança de perfis e expiração de `user_grant`.
- Invalidação de cache ao alterar perfil, grupo ou grant.
- Rotas sem verificação listadas em teste com `xfail`, para o teste de cobertura da Fase 3 passar a exigir ACL.
