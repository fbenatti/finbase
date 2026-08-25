"""
Script de correção de regras de classificação de Resource Group (Solução 1).
Corrige o campo 'ResourceName' para 'source_resource_group_name' no array 'conditions' (JSONB)
e em 'source_field' nas regras onde o padrão de busca refere-se a Resource Groups.
"""

from __future__ import annotations

import json
import logging
import os
import sys

# Configurar logging estruturado com outputs claros
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL_SYNC",
    "postgresql://finops:finops@localhost:5433/finops",
)


def fix_resource_group_rules() -> int:
    logger.info("Iniciando conexão com o banco de dados...")
    engine = create_engine(DATABASE_URL)
    updated_count = 0

    with engine.begin() as conn:
        logger.info("Buscando regras de classificação ativas...")
        rules = conn.execute(
            text("""
            SELECT id, name, match_value, pattern, source_field, conditions, cloud_scope
            FROM classification_rule
            ORDER BY id
        """)
        ).fetchall()

        logger.info("Total de regras encontradas: %d", len(rules))

        for rule in rules:
            r_dict = dict(rule._mapping)
            rule_id = r_dict["id"]
            pattern = (r_dict.get("pattern") or "").lower()
            source_field = r_dict.get("source_field") or ""
            conditions = r_dict.get("conditions") or []

            is_rg_rule = (
                "resourcegroup" in pattern
                or "source_resource_group" in pattern
                or "rg-" in pattern
                or "-rg" in pattern
                or "databricks-rg" in pattern
            )

            needs_update = False
            new_conditions = []

            if isinstance(conditions, list):
                for cond in conditions:
                    if isinstance(cond, dict):
                        new_cond = dict(cond)
                        field = new_cond.get("field", "")
                        if field == "ResourceName" and is_rg_rule:
                            new_cond["field"] = "source_resource_group_name"
                            needs_update = True
                        new_conditions.append(new_cond)
                    else:
                        new_conditions.append(cond)

            new_source_field = source_field
            if source_field == "ResourceName" and is_rg_rule:
                new_source_field = "source_resource_group_name"
                needs_update = True

            if needs_update:
                conn.execute(
                    text("""
                    UPDATE classification_rule
                    SET conditions = CAST(:conditions AS jsonb),
                        source_field = :source_field,
                        updated_at = NOW()
                    WHERE id = :rule_id
                """),
                    {
                        "conditions": json.dumps(new_conditions, ensure_ascii=False),
                        "source_field": new_source_field,
                        "rule_id": rule_id,
                    },
                )
                updated_count += 1
                if updated_count <= 10 or updated_count % 200 == 0:
                    logger.info(
                        "Regra ID %d ('%s') atualizada: field -> source_resource_group_name",
                        rule_id,
                        r_dict.get("name"),
                    )

    logger.info("Sucesso! Total de regras corrigidas: %d", updated_count)
    return updated_count


if __name__ == "__main__":
    try:
        total = fix_resource_group_rules()
        print(f"\n[OUTPUT] Processo finalizado com sucesso. Total de regras corrigidas: {total}")
    except Exception as exc:
        logger.exception("Erro ao executar script de correção: %s", exc)
        sys.exit(1)
