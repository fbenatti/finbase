-- Controle de acesso (RBAC + ACL): ACLs por tela/tabela/campo/regra, escopos,
-- filtros de linha e regras de negócio. Migração aditiva: o RBAC legado
-- (role_permission, user_grant) continua valendo até o corte da Fase 6.

CREATE TABLE IF NOT EXISTS access_acl (
    id SERIAL PRIMARY KEY,
    role_id INTEGER NOT NULL REFERENCES role(id) ON DELETE CASCADE,
    resource_type VARCHAR(20) NOT NULL CHECK (resource_type IN ('screen', 'table', 'field', 'rule')),
    resource VARCHAR(200) NOT NULL,
    operation VARCHAR(20) NOT NULL CHECK (operation IN ('read', 'create', 'write', 'delete')),
    effect VARCHAR(10) NOT NULL DEFAULT 'allow' CHECK (effect IN ('allow', 'deny')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    legacy_module VARCHAR(80),
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_access_acl UNIQUE (role_id, resource_type, resource, operation, effect)
);
CREATE INDEX IF NOT EXISTS ix_access_acl_role ON access_acl(role_id);

CREATE TABLE IF NOT EXISTS access_scope (
    id SERIAL PRIMARY KEY,
    scope_type VARCHAR(40) NOT NULL CHECK (scope_type IN ('hierarchy', 'budget', 'cost_center')),
    value VARCHAR(300) NOT NULL,
    label VARCHAR(200),
    node_ref VARCHAR(120),
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_access_scope UNIQUE (scope_type, value)
);

CREATE TABLE IF NOT EXISTS access_acl_condition (
    id SERIAL PRIMARY KEY,
    acl_id INTEGER NOT NULL REFERENCES access_acl(id) ON DELETE CASCADE,
    column_name VARCHAR(120) NOT NULL,
    scope_type VARCHAR(40) NOT NULL,
    match_mode VARCHAR(10) NOT NULL DEFAULT 'subtree' CHECK (match_mode IN ('exact', 'subtree'))
);
CREATE INDEX IF NOT EXISTS ix_access_acl_condition_acl ON access_acl_condition(acl_id);

CREATE TABLE IF NOT EXISTS user_scope (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    scope_id INTEGER NOT NULL REFERENCES access_scope(id) ON DELETE CASCADE,
    match_mode VARCHAR(10) NOT NULL DEFAULT 'subtree' CHECK (match_mode IN ('exact', 'subtree')),
    granted_by_id INTEGER REFERENCES user_account(id) ON DELETE SET NULL,
    reason VARCHAR(500) NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_user_scope UNIQUE (user_id, scope_id)
);
CREATE INDEX IF NOT EXISTS ix_user_scope_user ON user_scope(user_id);

CREATE TABLE IF NOT EXISTS group_scope (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES group_account(id) ON DELETE CASCADE,
    scope_id INTEGER NOT NULL REFERENCES access_scope(id) ON DELETE CASCADE,
    match_mode VARCHAR(10) NOT NULL DEFAULT 'subtree' CHECK (match_mode IN ('exact', 'subtree')),
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_group_scope UNIQUE (group_id, scope_id)
);
CREATE INDEX IF NOT EXISTS ix_group_scope_group ON group_scope(group_id);

CREATE TABLE IF NOT EXISTS access_business_rule (
    id SERIAL PRIMARY KEY,
    code VARCHAR(120) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    event VARCHAR(20) NOT NULL CHECK (event IN ('before_query', 'before_write', 'after_write')),
    resource VARCHAR(200) NOT NULL,
    condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority INTEGER NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Backfill: cada permissão de módulo vira uma ACL de tela equivalente, sem filtro de linha.
-- A ação `update` vira `write`. Perfis de sistema recebem as mesmas ACLs.
INSERT INTO access_acl (role_id, resource_type, resource, operation, effect, active, legacy_module)
SELECT rp.role_id,
       'screen',
       rp.module,
       CASE rp.action WHEN 'update' THEN 'write' ELSE rp.action END,
       'allow',
       TRUE,
       rp.module
FROM role_permission rp
WHERE rp.scope IS NULL
  AND rp.action IN ('read', 'create', 'update', 'delete')
ON CONFLICT ON CONSTRAINT uq_access_acl DO NOTHING;
