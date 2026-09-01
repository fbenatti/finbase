# Fase 5 — Frontend por permissão, hardening e fechamento

**Pré-requisito:** Fases 1–4 concluídas.

## Tarefa 5.1 — Guardas de rota e de ação por permissão

**Passos:**
1. No `AuthContext` (Fase 1), `hasPerm(module, action)` com `action ∈ {read, create, update, delete}` — checagem exata, sem implicações.
2. **Menu lateral**: renderizar item só se `hasPerm(modulo, 'read')` (mapa página → módulo, criar `src/auth/moduleMap.ts`).
3. **Route guard**: `RequirePerm module="rules" action="read"` envolvendo cada rota; sem permissão → página "Acesso negado" (não redirect para login, pois o usuário está autenticado).
4. **Ações nas telas**: botões mapeados pela ação real — "Novo/Salvar novo" → `create`, "Editar/Salvar" → `update`, "Excluir" → `delete`, "Executar" (ex.: rodar ETL) → `create`. Renderizar só se `hasPerm(modulo, <ação>)` (ou desabilitar com tooltip "Sem permissão"). Fazer varredura tela a tela; listar cobertura no PR.
5. A API continua sendo a autoridade (403) — o frontend é apenas UX.

**Critérios de aceite:** usuário "Leitor Global" navega tudo, mas não vê nenhum botão de mutação; usuário sem acesso a `forecast` não vê o menu nem acessa a URL.

## Tarefa 5.2 — Hardening

**Passos:**
1. **Rate limiting** em `/auth/login` e `/auth/refresh` (ex.: `slowapi`: 10/min por IP no login).
2. **Lockout local**: após 5 falhas consecutivas, bloquear conta local por 15 min (`failed_attempts`, `locked_until` no `User`).
3. CORS: restringir origens em produção (config `CORS_ORIGINS`).
4. Cookies: `Secure` em produção (config), `SameSite=Lax`, path restrito a `/auth`.
5. `JWT_SECRET_KEY`: falhar o startup se valor default em produção (`ENV=production`).
6. Headers de segurança básicos (middleware): `X-Content-Type-Options`, `X-Frame-Options=DENY`, `Referrer-Policy`.
7. Garantir que nenhum log imprime senha, token ou segredo de conexão (grep por `logger.*password|token|secret`).

**Critérios de aceite:** 6ª tentativa de login errado → 423/429; startup em produção sem segredo falha com mensagem clara.

## Tarefa 5.3 — Testes automatizados (mínimo)

**Passos:** criar `apps/api/tests/` (pytest + httpx AsyncClient, banco de teste):
1. Auth: login ok/errado, refresh rotação + reuso revoga família, troca de senha, política de senha.
2. RBAC: resolução de permissões (role direta, via grupo, grant direto, expiração de grant), 401/403 nos endpoints.
3. Auditoria: update gera before/after; sanitização remove segredos; diff calculado correto.
4. Seed idempotente.
5. Rodar no CI ou via script `scripts/test-auth.sh`.

**Critérios de aceite:** suíte verde.

## Tarefa 5.4 — Documentação e runbook

**Passos:** atualizar `README.md` e criar `docs/auth-runbook.md` com:
1. Variáveis de ambiente novas (tabela: nome, obrigatório, default, descrição) — incluindo `ENV`, `AUTH_ENABLED`, `DEV_MASTER_TOKEN`.
2. **Seção "Desenvolvimento sem fricção de auth"** (destaque no topo do runbook, pois é o caso mais comum para humanos e IAs):
   - Default DEV recomendado: `AUTH_ENABLED=false` + `ENV=development` → API e frontend entram direto como `dev-admin`, sem login, sem token; auditoria continua registrando.
   - Para testar telas de login/RBAC em DEV: manter `AUTH_ENABLED=true` e usar `DEV_MASTER_TOKEN=<valor>` no `Authorization: Bearer` (curl/httpie) — sem precisar de fluxo de login/refresh.
   - Aviso explícito: ambos os atalhos são **ignorados em produção** (`ENV=production`); staging/homologação devem rodar com auth ligada.
3. Como criar o primeiro admin; break-glass.
3. Como configurar OIDC no Entra ID (app registration, redirect URIs, claims de grupo) e SAML (enterprise app, metadata).
4. Como criar roles/grupos e cenários comuns ("dar leitura de análises para a diretoria X").
5. Como consultar auditoria e histórico; retenção.
6. Migração/rollback: como desligar auth em emergência (feature flag `AUTH_ENABLED` default true — implementar guard global que, se false, usa um usuário de sistema; documentar que é só para emergência).

**Critérios de aceite:** um desenvolvedor novo consegue subir o ambiente e configurar SSO seguindo só o runbook.

## Checklist final do projeto (validar tudo antes de encerrar)

- [ ] Todo endpoint de negócio exige autenticação; mutações exigem `write` do módulo.
- [ ] Toda mutação (`POST/PUT/PATCH/DELETE`) gera auditoria com snapshot antes/depois sanitizado.
- [ ] Histórico por registro (timeline estilo ServiceNow) acessível na aba "Histórico de alterações" em pelo menos `rules` e `connections`, com quem/quando/o quê e diff campo a campo.
- [ ] Login local + OIDC + SAML2 funcionando; JIT e mapeamento de grupos validados.
- [ ] Roles, grupos, grants nominais e matriz de permissões gerenciáveis pela UI.
- [ ] Token de 1h + refresh com rotação; logout revoga.
- [ ] Hardening (rate limit, lockout, cookies, CORS, segredos) aplicado.
- [ ] Testes mínimos verdes; runbook escrito.
