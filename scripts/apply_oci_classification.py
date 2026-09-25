#!/usr/bin/env python3
"""Organograma Showback > OCI e uma regra por projeto em SubAccountName."""

from __future__ import annotations

import json
import logging

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("oci_classification")

PG_URL = "postgresql://finops:finops@localhost:5433/finops"

# Uma regra por projeto. Campo único: SubAccountName (compartment FOCUS).
# (diretoria, projeto, operator, valores)
CATALOG: list[tuple[str, str, str, list[str]]] = [
    ("DIR DADOS E INTELIGENCIA ARTIFICIAL", "Wisper", "~", ["whisper", "llm", "poc-stt"]),
    ("DIR DADOS E INTELIGENCIA ARTIFICIAL", "Data Integration - Talend", "~", ["DataIntegration"]),
    ("DIR DADOS E INTELIGENCIA ARTIFICIAL", "ADW", "~", ["ADW-OAC"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "Programa Fenix", "~", ["ProgramaFenix"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "IOBS", "~", ["IOBS"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "MQSR", "~", ["MQSR"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "SIU", "~", ["SIU"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "Mediação", "~", ["MEDIACAO"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "FTMM", "~", ["FTMM"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "ESIMB2C", "~", ["ESIMB2C"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "ECVV", "~", ["ECVV"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "BSS RL", "~", ["BSS_RL", "BSS_HL"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "DIP", "~", ["SCP-DIP"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "WOK", "~", ["SCP-WOK"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "MVP OSB", "~", ["MVPOSB"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "M2C", "~", ["SCP-M2C"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "SGOE", "~", ["SGOE"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "DTMI", "~", ["DTMI"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "PIPEDB", "~", ["PIPEDB"]),
    ("DIR TECNOLOGIA B2C E NOVOS NEGOCIOS", "Ericsson EDR", "~", ["EricssonEDR"]),
    ("DIR TEC FINANCAS E CORPORATIVO", "BDFIN", "~", ["BDFIN"]),
    ("DIR TEC FINANCAS E CORPORATIVO", "HubPag", "~", ["HubPag"]),
    ("DIR TECNOLOGIA ENGENHARIA E CX", "Missão Crítica", "~", ["MISSAOCRITICA"]),
    ("DIR TECNOLOGIA ENGENHARIA E CX", "Amdocs", "~", ["Amdocs"]),
    ("DIR TERRA E VIVO ADS", "Terra Mexico", "~", ["TerraMX", "TCN-Mexico"]),
    ("DIR TERRA E VIVO ADS", "Terra Brasil", "~", ["Terra-Databases", "Terra-Connectivity"]),
    ("DIR ARQUITETURA E ESTRATEGIA TECH", "PlatCode", "~", ["PlatCode"]),
    ("DIR ARQUITETURA E ESTRATEGIA TECH", "OAM", "~", ["OAMC"]),
    ("DIR SEGURANCA DIGITAL", "Wazuh", "~", ["Wazuh"]),
    ("DIR SEGURANCA DIGITAL", "Prisma Cloud", "~", ["PrismaCloud"]),
    ("DIR INFRAESTRUTURA E CLOUD", "OCVS", "=", ["OCVS"]),
    ("DIR INFRAESTRUTURA E CLOUD", "Control-M", "~", ["ControlM"]),
    ("DIR INFRAESTRUTURA E CLOUD", "Patch Management", "~", ["PatchManagement"]),
    ("DIR INFRAESTRUTURA E CLOUD", "Firewall", "=", ["Firewall"]),
    ("DIR INFRAESTRUTURA E CLOUD", "Jenkins", "~", ["JENKINS"]),
    ("DIR INFRAESTRUTURA E CLOUD", "VDI Cybele", "~", ["vdi_cybele", "vdi-cybele"]),
    ("DIR INFRAESTRUTURA E CLOUD", "Landing Zone", "~", [
        "SharedServices", "Connectivity", "NETWORK", "Backup", "Bastion",
        "Logging", "CloudEng", "brtlvcldoci", "rg-tbp-oci",
        "cpvvia", "cpvvsh", "cpkrec", "cpsptn", "cpmvpk", "cptpaz",
        "cpoke", "cpzeus", "cptlco", "cppco2", "cpdtss", "cptrsd", "cpoci",
    ]),
]


def get_or_create_dept(cur, name: str, parent_id: int | None) -> int:
    cur.execute(
        "SELECT id FROM department WHERE name = %s AND parent_id IS NOT DISTINCT FROM %s LIMIT 1",
        (name, parent_id),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE department SET active = true WHERE id = %s AND active = false", (row[0],))
        return row[0]
    cur.execute(
        """
        INSERT INTO department (name, parent_id, active, created_at, updated_at)
        VALUES (%s, %s, true, NOW(), NOW())
        RETURNING id
        """,
        (name, parent_id),
    )
    dept_id = cur.fetchone()[0]
    logger.info("Criado departamento %s (id=%s)", name, dept_id)
    return dept_id


def main() -> None:
    conn = psycopg2.connect(PG_URL)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT id FROM department WHERE name = 'Consumo Cloud' AND parent_id IS NULL LIMIT 1")
    consumo = cur.fetchone()[0]
    cur.execute("SELECT id FROM department WHERE name = 'Showback' AND parent_id = %s LIMIT 1", (consumo,))
    showback = cur.fetchone()[0]
    oci_root = get_or_create_dept(cur, "OCI", showback)

    cur.execute("SELECT id FROM source_connection WHERE connector_type = 'oci' LIMIT 1")
    conn_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM destination_table WHERE physical_name = 'dest_modelo_focus' LIMIT 1")
    dest_id = cur.fetchone()[0]

    cur.execute("DELETE FROM classification_rule WHERE cloud_scope = 'oci' OR connection_id = %s", (conn_id,))
    logger.info("Regras OCI removidas: %s", cur.rowcount)

    keep: set[int] = {oci_root}
    prio = 20
    for diretoria, projeto, operator, values in CATALOG:
        dir_id = get_or_create_dept(cur, diretoria, oci_root)
        proj_id = get_or_create_dept(cur, projeto, dir_id)
        keep.add(dir_id)
        keep.add(proj_id)
        conditions = [{"field": "SubAccountName", "operator": operator, "values": values, "logic": "AND"}]
        pattern = f"SubAccountName{operator}{'|'.join(values)}"[:500]
        cur.execute(
            """
            INSERT INTO classification_rule (
                connection_id, destination_table_id, cloud_scope, department_id, name,
                source_field, pattern, pattern_type, match_value, target_field,
                conditions, priority, active, created_at, updated_at
            ) VALUES (
                %s, %s, 'oci', %s, %s,
                'SubAccountName', %s, 'multi_condition', %s, 'organograma',
                %s, %s, true, NOW(), NOW()
            )
            """,
            (conn_id, dest_id, proj_id, f"OCI | {projeto}", pattern, projeto, json.dumps(conditions), prio),
        )
        prio += 1

    infra = get_or_create_dept(cur, "DIR INFRAESTRUTURA E CLOUD", oci_root)
    fallback = get_or_create_dept(cur, "SEM PROJETO DEFINIDO", infra)
    keep.add(infra)
    keep.add(fallback)
    cur.execute(
        """
        INSERT INTO classification_rule (
            connection_id, destination_table_id, cloud_scope, department_id, name,
            source_field, pattern, pattern_type, match_value, target_field,
            conditions, priority, active, created_at, updated_at
        ) VALUES (
            %s, %s, 'oci', %s, 'OCI | Fallback',
            'SubAccountName', 'SubAccountName~.', 'multi_condition', 'SEM PROJETO DEFINIDO', 'organograma',
            %s, 9990, true, NOW(), NOW()
        )
        """,
        (conn_id, dest_id, fallback, json.dumps([
            {"field": "SubAccountName", "operator": "~", "values": ["."], "logic": "AND"},
        ])),
    )

    cur.execute(
        """
        UPDATE department SET active = false, updated_at = NOW()
        WHERE id IN (
            SELECT d.id FROM department d
            WHERE d.id != %s AND (
                d.parent_id = %s
                OR d.parent_id IN (SELECT id FROM department WHERE parent_id = %s)
            )
        )
        AND id != ALL(%s)
        """,
        (oci_root, oci_root, oci_root, list(keep)),
    )
    logger.info("Departamentos OCI sem regra desativados: %s", cur.rowcount)
    conn.commit()
    logger.info("Gravadas %s regras em SubAccountName (+ fallback).", prio - 20)


if __name__ == "__main__":
    main()
