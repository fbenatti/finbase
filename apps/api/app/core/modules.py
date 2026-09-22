"""Lista central de módulos RBAC (cada router da API = um módulo).

Manter sincronizada com docs/plano-auth-rbac-auditoria/00-indice.md.
"""
ALL_MODULES = [
    "connections", "rules", "etl", "analysis", "forecast", "planning",
    "reservations", "savings", "periods", "destinations", "schema", "fact",
    "csv_upload", "clone_rules", "gold", "ai_copilot", "ai_dashboards",
    "ai_provider_dashboards", "azure_ptu", "azure_resource_graph",
    "azure_tag_policy", "oci_operational", "scheduled_shutdown",
    # módulos de administração
    "auth", "users", "groups", "roles", "audit", "ideas",
]

ALL_ACTIONS = ("read", "create", "update", "delete")
