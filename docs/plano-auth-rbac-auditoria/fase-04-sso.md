# Fase 4 — SSO corporativo: OIDC e SAML2 (AD cloud / Entra ID)

**Pré-requisito:** Fases 1–3 concluídas. Login local continua funcionando (contingência/break-glass).

## Tarefa 4.1 — Configuração de provedores

**Passos:**
1. Model `SsoProvider`: `id`, `name` (único, ex.: "Entra ID"), `protocol` (`oidc`|`saml`), `enabled` (bool), `config_json` (JSONB), `created_at`, `updated_at`.
   - Config OIDC: `client_id`, `client_secret` (armazenar **criptografado** ou via env referenciado — nunca em claro; no mínimo nunca retornar pela API), `issuer`/`discovery_url`, `redirect_uri`, `scopes` (default `openid profile email`), `groups_claim` (default `groups`).
   - Config SAML: `entity_id`, `sso_url`, `x509_cert`, `sp_entity_id`, `acs_url`, `groups_attribute`.
2. Router `app/routers/sso_providers.py` (módulo `auth`, write): CRUD de providers. **Sanitizar** segredos na resposta (mesmo padrão `_sanitize_config`). Auditoria cobre estas mutações (Fase 3 já ativa).
3. Tela admin "Provedores SSO" (habilitar/desabilitar, formulário por protocolo, botão "testar" = validar discovery/metadata).

**Critérios de aceite:** criar provider OIDC pela UI; segredo não retorna no GET.

## Tarefa 4.2 — Fluxo OIDC

**Passos:** usar `authlib` (adicionar às dependências).
1. `GET /auth/sso/{provider}/login` (público): redireciona ao IdP com `state` e `nonce` (guardar em cookie de curta duração, httpOnly).
2. `GET /auth/sso/{provider}/callback` (público): troca code → tokens, valida `id_token` (assinatura via JWKS do discovery, `iss`, `aud`, `exp`, `nonce`).
3. **Provisionamento JIT**: localizar usuário por `external_id` (`sub`/`oid`) ou `email`; se não existir, criar (`auth_provider=oidc`, sem senha, `is_active=True`). Atualizar `full_name`/`email` a cada login.
4. **Mapeamento de grupos**: ler claim de grupos; para cada valor, casar com `Group.external_id`; grupos novos podem ser **auto-criados** (config por provider: `auto_create_groups` bool) e ficam sem roles até um admin associar. Vincular o usuário aos grupos casados; **remover** vínculos de grupos externos que sumiram do claim (apenas os que têm `external_id` — vínculos manuais nunca são tocados).
5. Emitir o mesmo par access/refresh da Fase 1 e redirecionar ao frontend (`/` com cookie de refresh setado).
6. Se o usuário não tiver **nenhuma** permissão efetiva após o login: permitir login mas frontend mostra tela "Solicite acesso ao administrador" (não bloquear autenticação, bloquear uso).

**Critérios de aceite:** login OIDC real contra Entra ID (app registration de teste); usuário novo é provisionado e herda permissões via grupo mapeado; grupo removido no IdP remove acesso no próximo login.

## Tarefa 4.3 — Fluxo SAML2

**Passos:** usar `python3-saml` (OneLogin) — adicionar dependência (atenção a `xmlsec` no ambiente; documentar instalação no README).
1. `GET /auth/sso/{provider}/login` → AuthnRequest; `POST /auth/sso/{provider}/acs` → valida Response (assinatura, `InResponseTo`, condições de tempo); `GET /auth/sso/{provider}/metadata` → expõe metadata do SP para cadastrar no IdP.
2. Provisionamento JIT e mapeamento de grupos: **reutilizar as mesmas funções** da Tarefa 4.2 (extrair para `app/core/sso_provisioning.py`).
3. Atributo de grupos configurável (`groups_attribute`).

**Critérios de aceite:** login SAML funcional (pode validar com Entra ID Enterprise Application ou um IdP de teste como saml.id); JIT e grupos funcionam igual ao OIDC.

## Tarefa 4.4 — UX de login e regras de coexistência

**Passos:**
1. Tela de login mostra botões "Entrar com {provider.name}" (buscar providers habilitados em `GET /auth/sso/providers` — endpoint público que retorna só `name`/`protocol`).
2. Config global `ALLOW_LOCAL_LOGIN` (default true). Se false, esconder formulário local (manter acessível via `?local=1` para break-glass de admin — documentar).
3. Mesmo e-mail em local + SSO: não fundir automaticamente; admin pode vincular na tela de usuário (campo `external_id`).

**Critérios de aceite:** com `ALLOW_LOCAL_LOGIN=false`, só SSO aparece; break-glass funciona.

## Entregáveis da fase
- `SsoProvider`, router/tela de providers, fluxos OIDC e SAML2, `sso_provisioning.py`, botões de SSO no login.
