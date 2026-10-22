# Mapa de rotas do backend: EN → PT

Documento de trabalho para a migração das rotas FastAPI para português, espelhando as rotas do front.
Status: **rascunho para revisão**. Nenhum endpoint foi renomeado ainda.

Regras:
- Prefixo novo é o caminho real. Os prefixos em inglês saem da API, sem alias.
- Endpoints de funcionalidades removidas (IA, Carbon, visão geral, Unit Economics, restore do planning) saem junto.
- Cada mudança de prefixo precisa atualizar: `core/authz_middleware.py` (mapa de módulos, `AUTHZ_EXTRA_MODULES`, ACLs e escopos), `core/audit_middleware.py` (`audit_module_for`, `AUDIT_MODELS`), `seed.py` / `seed_identity.py` (módulos), `tests/test_authz_route_coverage.py`, testes de auditoria, `apps/web/src/services/api.ts` e `accessApi.ts`.
- `core/route_aliases.py` deixa de ser necessário depois da troca e é removido.

## Routers

| Router (arquivo) | Prefixo atual | Prefixo novo (PT) | Observação |
|---|---|---|---|
| auth.py | /auth | /autenticacao | |
| sso.py | /auth/sso | /autenticacao/sso | |
| sso_providers.py | /sso/providers | /administracao/identidade/provedores | |
| access.py | /access | /administracao/acessos | Confirmar: front usa `/administracao/acessos` como área, hoje alias para /users |
| users.py | /users | /administracao/usuarios | |
| groups.py | /groups | /administracao/grupos | |
| roles.py | /roles | /administracao/perfis | |
| modules.py | (sem prefixo) | /administracao/modulos | Hoje `/modules` e `/navigation` (confirmar) |
| audit.py | /audit | /administracao/auditoria | |
| connections.py | /connections | /dados/fontes | Já alias no front |
| connector_visibility.py | /connector-visibility | /dados/fontes/visibilidade | |
| destinations.py | /destinations | /dados/catalogo | Já alias no front |
| analysis.py | /analysis | /dados/explorador | Já alias no front |
| csv_upload.py | /csv-upload | /dados/upload-csv | |
| schema.py | /schema | /dados/esquema | |
| fact.py | /fact | /dados/fato | Confirmar uso no front |
| rules.py | (sem prefixo) | /dados/regras | Confirmar paths |
| clone_rules.py | /clone-rules | /dados/regras-clonagem | |
| mappings | (sem router próprio) | /dados/mapeamentos | Alias no front; ver `etl.py` |
| integrations.py | /integrations | /dados/integracoes | |
| azure_sources.py | /azure-sources | /dados/fontes-azure | |
| etl.py | /etl | /operacoes | Front divide em /dados/fluxos, /operacoes/execucoes, /operacoes/extracoes, /operacoes/custos-compartilhados. Separar em sub-rotas na Fase 5 |
| periods.py | /etl/periods | /operacoes/periodos | |
| processing_logs.py | /processing-logs | /operacoes/logs-processamento | |
| gold.py | /gold | /custos | Já alias no front |
| reservations.py | /reservations | /custos/reservas | |
| azure_ptu.py | /visoes-consumo-ia | /custos/consumo-azure | Consumo PTU. Confirmar se é IA (candidato à remoção) |
| azure_mca_commitment.py | /mca-commitment | /custos/compromisso-mca | |
| oci_operational.py | /oci-operational | /custos/oci-operacional | |
| financial_contracts.py | /financial-contracts | /governanca/contratos-financeiros | |
| azure_tag_policy.py | /azure-tag-policy | /governanca/politica-tags-azure | |
| azure_resource_graph.py | /azure-resource-graph | /governanca/grafo-recursos-azure | |
| forecast.py | /forecast | /projecoes | Já alias no front |
| anomalies.py | /anomalies | /alertas | Já alias no front |
| planning.py | /planning | /planejamento | Já alias no front. Remover endpoints de restore |
| savings.py | /savings | /otimizacao | Já alias no front |
| scheduled_shutdown.py | /scheduled-shutdown | /otimizacao/desligamento-agendado | |
| ideas.py | /ideas | /iniciativas | Já alias no front |

## Removidos (não migram)

| Item | Motivo |
|---|---|
| ai_copilot.py | Funcionalidade IA removida |
| ai_dashboards.py | Funcionalidade IA removida |
| ai_provider_dashboards.py | Funcionalidade IA removida |
| Carbon, visão geral, Unit Economics | Funcionalidades removidas, confirmar arquivos |
| Restore do planning | Endpoint de restore em planning.py |

## Pendências para decisão

1. `/visoes-consumo-ia` (azure_ptu.py): é IA ou custo Azure PTU? Define se sai ou vira `/custos/consumo-azure`.
2. `/etl` dividido em 4 áreas do front: manter um router com sub-caminhos ou separar agora.
3. `modules` e `rules` sem prefixo: confirmar caminhos reais antes de renomear.
4. `/auth` e `/auth/sso`: confirmar se o front espera `/autenticacao` ou mantém `/auth`.

## Pendências do bloco Alertas (anomalies)

- Remover do backend os campos que o front não usa mais: `evaluation` em `/metrics`, e `by_deviation_status` e `detected` em `/impact/summary`.
- Manter `by_severity` (usado pelo KPI "Críticas").
- O painel migrou para `/alertas/painel` (AnomaliesPainel.tsx). Reler o arquivo antes de editar.
