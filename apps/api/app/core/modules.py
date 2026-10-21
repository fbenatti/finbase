"""Lista central de módulos RBAC (cada router da API = um módulo).

Manter sincronizada com docs/plano-auth-rbac-auditoria/00-indice.md.
"""
ALL_MODULES = [
    "connections", "rules", "etl", "analysis", "forecast", "planning",
    "reservations", "savings", "periods", "destinations", "schema", "fact",
    "csv_upload", "clone_rules", "gold", "azure_ptu", "azure_resource_graph",
    "azure_tag_policy", "oci_operational", "scheduled_shutdown", "anomalies",
    # Centro de Decisão FinOps: resumo, custo, drivers, eficiência, AKS, PTU e
    # qualidade. Módulo próprio porque a permissão de leitura de custo agregado não
    # é a mesma de administrar a governança Gold.
    "finops",
    # módulos de administração
    "auth", "users", "groups", "roles", "audit", "ideas", "modules",
]

ALL_ACTIONS = ("read", "create", "update", "delete")
