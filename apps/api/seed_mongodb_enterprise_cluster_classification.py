"""Seed Enterprise cluster classification rules from the working Pro mapping."""

from sqlalchemy import select

from app.database import SyncSession
from app.models.rules import ClassificationRule
from app.models.source_connection import SourceConnection  # noqa: F401
from app.models.destination import DestinationTable  # noqa: F401


RULES = [
    ("br-pro70", "MongoDB - Aura Meu Vivo"),
    ("br-est70", "MongoDB - Aura Meu Vivo"),
    ("br-pre70", "MongoDB - Aura Meu Vivo"),
    ("cl-code-brsouth-002-dev", "MongoDB - CODE"),
    ("sav-pro", "MongoDB - SAV"),
]


def ensure_rules() -> list[int]:
    ids: list[int] = []
    with SyncSession() as session:
        connection_id = session.execute(
            select(SourceConnection.id).where(SourceConnection.code == "mongodb-atlas-enterprise")
        ).scalar_one()
        destination_id = session.execute(
            select(DestinationTable.id).where(DestinationTable.physical_name == "dest_billing_cloud")
        ).scalar_one()
        for cluster, project in RULES:
            name = f"Enterprise Cluster {cluster} → {project}"
            rule = session.execute(select(ClassificationRule).where(ClassificationRule.name == name)).scalar_one_or_none()
            conditions = [{"field": "resourceid", "operator": "=", "values": [cluster], "logic": "AND"}]
            if rule is None:
                rule = ClassificationRule(
                    connection_id=connection_id, destination_table_id=destination_id,
                    name=name, source_field="resourceid", pattern=f"resourceid={cluster}",
                    pattern_type="multi_condition", match_value=project, target_field="organograma",
                    conditions=conditions, priority=1, cloud_scope="atlas", active=True,
                )
                session.add(rule)
            else:
                rule.connection_id = connection_id
                rule.destination_table_id = destination_id
                rule.source_field = "resourceid"
                rule.pattern = f"resourceid={cluster}"
                rule.pattern_type = "multi_condition"
                rule.match_value = project
                rule.target_field = "organograma"
                rule.conditions = conditions
                rule.priority = 1
                rule.cloud_scope = "atlas"
                rule.active = True
            session.flush()
            ids.append(rule.id)
        session.commit()
    return ids


if __name__ == "__main__":
    print(f"MongoDB Enterprise cluster classification rule ids={ensure_rules()}")
