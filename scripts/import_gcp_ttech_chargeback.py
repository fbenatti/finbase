import json
import logging
import csv
from pathlib import Path
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("import_gcp_chargeback")

def main():
    logger.info("=== Iniciando importação de Projetos e Regras de Negócio sob Chargeback > GCP ===")
    
    conn = psycopg2.connect("postgresql://finops:finops@localhost:5433/finops")
    cur = conn.cursor()
    
    try:
        # 1. Obter nós base (Consumo Cloud -> Chargeback -> GCP)
        cur.execute("SELECT id FROM department WHERE parent_id IS NULL AND name = 'Consumo Cloud'")
        row = cur.fetchone()
        if not row:
            raise Exception("Nó raiz 'Consumo Cloud' não encontrado!")
        consumo_cloud_id = row[0]
        
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'Chargeback'", (consumo_cloud_id,))
        row = cur.fetchone()
        if not row:
            raise Exception("Nó 'Chargeback' não encontrado sob 'Consumo Cloud'!")
        chargeback_id = row[0]
        
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'GCP'", (chargeback_id,))
        row = cur.fetchone()
        if not row:
            raise Exception("Nó 'GCP' não encontrado sob 'Chargeback'!")
        gcp_chargeback_id = row[0]
        logger.info(f"Nó base Chargeback > GCP encontrado com ID: {gcp_chargeback_id}")

        # 2. Garantir Diretorias sob Chargeback > GCP (402)
        # 2a. DIR ARQUITETURA E ESTRATEGIA TECH
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'DIR ARQUITETURA E ESTRATEGIA TECH'", (gcp_chargeback_id,))
        row = cur.fetchone()
        if row:
            dir_arq_id = row[0]
            logger.info(f"Diretoria existente: 'DIR ARQUITETURA E ESTRATEGIA TECH' (ID: {dir_arq_id})")
        else:
            cur.execute("""
                INSERT INTO department (parent_id, name, centro_custo, ambiente, cloud_filter, custom_fields, active, created_at, updated_at)
                VALUES (%s, 'DIR ARQUITETURA E ESTRATEGIA TECH', '29TN161700', 'TI', 'GCP-TI', 
                %s, true, NOW(), NOW()) RETURNING id
            """, (gcp_chargeback_id, json.dumps({
                "tipo": "Diretoria",
                "DiretorResp": "THIAGO ALBERTO GIL",
                "VP": "CHIEF INFORMATION OFFICER",
                "GerCDG": "Ricardo Santilli",
                "RespFinops": "Lucas",
                "RespFinanceiro": "Everaldo Amorim;Fernando Benatti; Fernando Gomes"
            })))
            dir_arq_id = cur.fetchone()[0]
            logger.info(f"Diretoria criada: 'DIR ARQUITETURA E ESTRATEGIA TECH' (ID: {dir_arq_id})")

        # 2b. DIR INFRAESTRUTURA E CLOUD
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'DIR INFRAESTRUTURA E CLOUD'", (gcp_chargeback_id,))
        row = cur.fetchone()
        if row:
            dir_infra_id = row[0]
            logger.info(f"Diretoria existente: 'DIR INFRAESTRUTURA E CLOUD' (ID: {dir_infra_id})")
        else:
            cur.execute("""
                INSERT INTO department (parent_id, name, centro_custo, ambiente, cloud_filter, custom_fields, active, created_at, updated_at)
                VALUES (%s, 'DIR INFRAESTRUTURA E CLOUD', '29TN161700', 'TI', 'GCP-TTech', 
                %s, true, NOW(), NOW()) RETURNING id
            """, (gcp_chargeback_id, json.dumps({
                "tipo": "Diretoria",
                "DiretorResp": "EDUARDO TADEU BOSCOVICH MALICIA",
                "VP": "CHIEF INFORMATION OFFICER",
                "GerCDG": "Ricardo Santilli",
                "RespFinops": "Jorge Marcelino",
                "RespFinanceiro": "Everaldo Amorim;Fernando Benatti; Fernando Gomes"
            })))
            dir_infra_id = cur.fetchone()[0]
            logger.info(f"Diretoria criada: 'DIR INFRAESTRUTURA E CLOUD' (ID: {dir_infra_id})")

        # 3. Garantir Departamentos-Filhos (Projetos Lógicos)
        # 3a. Vicky (sob DIR ARQUITETURA E ESTRATEGIA TECH)
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'Vicky'", (dir_arq_id,))
        row = cur.fetchone()
        if row:
            dept_vicky_id = row[0]
            logger.info(f"Departamento-Projeto existente: 'Vicky' (ID: {dept_vicky_id})")
        else:
            cur.execute("""
                INSERT INTO department (parent_id, name, centro_custo, ambiente, cloud_filter, custom_fields, active, created_at, updated_at)
                VALUES (%s, 'Vicky', '29TN161700', 'TI', 'GCP-TTech', 
                %s, true, NOW(), NOW()) RETURNING id
            """, (dir_arq_id, json.dumps({
                "tipo": "Projeto",
                "DiretorResp": "THIAGO ALBERTO GIL",
                "GerSR": "GER SR ARQUITETUR NEGOCIOS E APLICAC",
                "ContaContabil": "31031338.0",
                "IDOrcamento": "1896",
                "MatriculaResp": "628010.0"
            })))
            dept_vicky_id = cur.fetchone()[0]
            logger.info(f"Departamento-Projeto criado: 'Vicky' (ID: {dept_vicky_id})")

        # 3b. DEPARTAMENTAIS (sob DIR INFRAESTRUTURA E CLOUD)
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'DEPARTAMENTAIS'", (dir_infra_id,))
        row = cur.fetchone()
        if row:
            dept_dpt_id = row[0]
            logger.info(f"Departamento-Projeto existente: 'DEPARTAMENTAIS' (ID: {dept_dpt_id})")
        else:
            cur.execute("""
                INSERT INTO department (parent_id, name, centro_custo, ambiente, cloud_filter, custom_fields, active, created_at, updated_at)
                VALUES (%s, 'DEPARTAMENTAIS', '29TN161700', 'TI', 'GCP-TTech', 
                %s, true, NOW(), NOW()) RETURNING id
            """, (dir_infra_id, json.dumps({
                "tipo": "Projeto",
                "DiretorResp": "EDUARDO TADEU BOSCOVICH MALICIA",
                "GerSR": "GER SR ENGENHARIA PLATAFORMAS E PROD",
                "ContaContabil": "31031338.0",
                "IDOrcamento": "1896",
                "MatriculaResp": "1529040.0"
            })))
            dept_dpt_id = cur.fetchone()[0]
            logger.info(f"Departamento-Projeto criado: 'DEPARTAMENTAIS' (ID: {dept_dpt_id})")

        # 3c. TI / SEM PROJETO DEFINIDO (sob DIR INFRAESTRUTURA E CLOUD)
        cur.execute("SELECT id FROM department WHERE parent_id = %s AND name = 'SEM PROJETO DEFINIDO'", (dir_infra_id,))
        row = cur.fetchone()
        if row:
            dept_ti_id = row[0]
            logger.info(f"Departamento-Projeto existente: 'SEM PROJETO DEFINIDO' (ID: {dept_ti_id})")
        else:
            cur.execute("""
                INSERT INTO department (parent_id, name, centro_custo, ambiente, cloud_filter, custom_fields, active, created_at, updated_at)
                VALUES (%s, 'SEM PROJETO DEFINIDO', '29TN161700', 'TI', 'GCP-TTech', 
                %s, true, NOW(), NOW()) RETURNING id
            """, (dir_infra_id, json.dumps({
                "tipo": "Projeto",
                "DiretorResp": "EDUARDO TADEU BOSCOVICH MALICIA",
                "GerSR": "GER SR ENGENHARIA PLATAFORMAS E PROD",
                "ContaContabil": "31031338.0",
                "IDOrcamento": "1764",
                "MatriculaResp": "1529040.0"
            })))
            dept_ti_id = cur.fetchone()[0]
            logger.info(f"Departamento-Projeto criado: 'SEM PROJETO DEFINIDO' (ID: {dept_ti_id})")

        # 4. Ler projetos da origem e inserir na tabela `project`
        origem_csv = Path("origem_gcp-ttech_202608.csv")
        if not origem_csv.exists():
            raise Exception("Arquivo 'origem_gcp-ttech_202608.csv' não encontrado!")
        
        with open(origem_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        projects_created = 0
        projects_updated = 0
        
        for r in rows:
            p_raw = r.get("project") or r.get("\ufeffproject")
            if not p_raw:
                continue
            p_data = json.loads(p_raw)
            p_id = p_data.get("id")
            p_name = p_data.get("name") or p_id
            labels = p_data.get("labels", [])
            
            # Determinar classificação e departamento alvo
            if "vicky" in p_id.lower() or "vicky" in p_name.lower():
                target_dept_id = dept_vicky_id
                target_proj_name = "Vicky"
            elif "dpt" in p_id.lower() or "dpt" in p_name.lower() or "departament" in str(labels).lower():
                target_dept_id = dept_dpt_id
                target_proj_name = "DEPARTAMENTAIS"
            else:
                target_dept_id = dept_ti_id
                target_proj_name = "SEM PROJETO DEFINIDO"
            
            # Verificar se projeto já existe na tabela project
            cur.execute("""
                SELECT id, department_id, projeto_name FROM project 
                WHERE cloud = 'GCP-TTech' AND subscription_rg = %s
            """, (p_id,))
            proj_row = cur.fetchone()
            
            if proj_row:
                cur.execute("""
                    UPDATE project 
                    SET department_id = %s, projeto_name = %s, active = true, updated_at = NOW()
                    WHERE id = %s
                """, (target_dept_id, target_proj_name, proj_row[0]))
                projects_updated += 1
            else:
                cur.execute("""
                    INSERT INTO project (department_id, projeto_name, cloud, subscription_rg, active, created_at, updated_at)
                    VALUES (%s, %s, 'GCP-TTech', %s, true, NOW(), NOW())
                """, (target_dept_id, target_proj_name, p_id))
                projects_created += 1
                logger.info(f"  [Project] Inserido: {p_id} -> {target_proj_name} (Dept: {target_dept_id})")

        logger.info(f"Processamento de Projetos concluído: {projects_created} criados, {projects_updated} atualizados.")

        # 5. Criar / Atualizar Regras de Classificação na tabela `classification_rule`
        rules_to_insert = [
            {
                "name": "GCP-TTech | Vicky",
                "cloud_scope": "gcp",
                "connection_id": 8, # gcp-ttech
                "department_id": dept_vicky_id,
                "source_field": "resourceGroupName",
                "pattern": "resourceGroupName~vicky",
                "pattern_type": "multi_condition",
                "match_value": "Vicky",
                "target_field": "departamento",
                "priority": 10,
                "conditions": [{"field": "resourceGroupName", "op": "~", "value": "vicky"}]
            },
            {
                "name": "GCP-TTech | DEPARTAMENTAIS",
                "cloud_scope": "gcp",
                "connection_id": 8, # gcp-ttech
                "department_id": dept_dpt_id,
                "source_field": "resourceGroupName",
                "pattern": "resourceGroupName~dpt|departament",
                "pattern_type": "multi_condition",
                "match_value": "DEPARTAMENTAIS",
                "target_field": "departamento",
                "priority": 20,
                "conditions": [{"field": "resourceGroupName", "op": "~", "value": "dpt|departament"}]
            },
            {
                "name": "GCP-TTech | TI Padrão",
                "cloud_scope": "gcp",
                "connection_id": 8, # gcp-ttech
                "department_id": dept_ti_id,
                "source_field": "resourceGroupName",
                "pattern": "resourceGroupName~ti-|vivo-ti",
                "pattern_type": "multi_condition",
                "match_value": "SEM PROJETO DEFINIDO",
                "target_field": "departamento",
                "priority": 100,
                "conditions": [{"field": "resourceGroupName", "op": "~", "value": "ti-|vivo-ti"}]
            }
        ]

        for rule in rules_to_insert:
            cur.execute("""
                SELECT id FROM classification_rule 
                WHERE name = %s AND cloud_scope = 'gcp'
            """, (rule["name"],))
            existing_rule = cur.fetchone()
            
            if existing_rule:
                cur.execute("""
                    UPDATE classification_rule
                    SET department_id = %s, pattern = %s, pattern_type = %s, 
                        match_value = %s, conditions = %s, priority = %s, active = true, updated_at = NOW()
                    WHERE id = %s
                """, (
                    rule["department_id"], rule["pattern"], rule["pattern_type"],
                    rule["match_value"], json.dumps(rule["conditions"]), rule["priority"], existing_rule[0]
                ))
                logger.info(f"Regra de classificação atualizada: '{rule['name']}' (ID: {existing_rule[0]})")
            else:
                cur.execute("""
                    INSERT INTO classification_rule (
                        name, cloud_scope, connection_id, department_id, source_field,
                        pattern, pattern_type, match_value, target_field, priority, conditions, active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, NOW(), NOW()
                    )
                """, (
                    rule["name"], rule["cloud_scope"], rule["connection_id"], rule["department_id"],
                    rule["source_field"], rule["pattern"], rule["pattern_type"], rule["match_value"],
                    rule["target_field"], rule["priority"], json.dumps(rule["conditions"])
                ))
                logger.info(f"Regra de classificação criada: '{rule['name']}'")

        conn.commit()
        logger.info("=== Todas as alterações foram confirmadas no banco com sucesso! ===")

    except Exception as e:
        conn.rollback()
        logger.exception(f"Erro durante a importação: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
