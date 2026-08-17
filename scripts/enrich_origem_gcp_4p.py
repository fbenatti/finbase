import csv
import json
import logging
from pathlib import Path
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("enrich_gcp_4p")

INPUT_CSV = "origem_gcp-4p_202608.csv"
OUTPUT_CLASSIFIED_CSV = "origem_gcp-4p_202608_classificado.csv"
OUTPUT_STANDARD_CSV = "Classificacao-4P-GCP.csv"
PROJETOS_CSV = "projetos_download.csv"
DIRETORIAS_CSV = "diretorias_download.csv"

def build_lookup_tables():
    logger.info("Carregando tabelas de referência...")
    df_p = pd.read_csv(PROJETOS_CSV, sep=';')
    df_d = pd.read_csv(DIRETORIAS_CSV, sep=';')
    
    # 1. Mapeamento SubscriptionRG -> Projeto para GCP-4P
    gcp_proj = df_p[df_p['Cloud'] == 'GCP-4P']
    proj_map = dict(zip(gcp_proj['SubscriptionRG'], gcp_proj['Projeto']))
    
    # 2. Mapeamento Projeto -> {Diretoria, Centro de Custos} para GCP-4P
    gcp_dir = df_d[df_d['Cloud'] == 'GCP-4P'].drop_duplicates(subset=['Projeto'])
    dir_map = {}
    for _, r in gcp_dir.iterrows():
        dir_map[r['Projeto']] = {
            'Diretoria': str(r['Diretoria']).strip() if pd.notna(r['Diretoria']) else '',
            'Centro de Custos': str(r['CentrodeCusto']).strip() if pd.notna(r['CentrodeCusto']) else ''
        }
        
    # Mapeamento de fallback geral do diretorias_download.csv
    fallback_dir = df_d.drop_duplicates(subset=['Projeto'])
    fallback_map = {}
    for _, r in fallback_dir.iterrows():
        fallback_map[r['Projeto']] = {
            'Diretoria': str(r['Diretoria']).strip() if pd.notna(r['Diretoria']) else '',
            'Centro de Custos': str(r['CentrodeCusto']).strip() if pd.notna(r['CentrodeCusto']) else ''
        }
        
    logger.info(f"Tabelas carregadas: {len(proj_map)} projetos GCP-4P e {len(dir_map)} diretorias GCP-4P.")
    return proj_map, dir_map, fallback_map

def classify_project(project_raw, proj_map, dir_map, fallback_map):
    if not project_raw or not project_raw.strip():
        return {
            'Diretoria': 'NAO ENCONTRADO',
            'Projeto': 'SEM PROJETO DEFINIDO',
            'Centro de Custos': ''
        }
        
    try:
        data = json.loads(project_raw)
    except Exception:
        return {
            'Diretoria': 'NAO ENCONTRADO',
            'Projeto': 'SEM PROJETO DEFINIDO',
            'Centro de Custos': ''
        }
        
    pid = data.get('id')
    pname = data.get('name')
    labels = data.get('labels', [])
    
    # 1. Identificar nome do Projeto
    mapped_proj = proj_map.get(pid) or proj_map.get(pname)
    
    # Se não achou no projetos_download, tenta casar com o nome de diretoria
    if not mapped_proj:
        if pname in dir_map:
            mapped_proj = pname
        elif pid in dir_map:
            mapped_proj = pid
        elif pname in fallback_map:
            mapped_proj = pname
        elif pid in fallback_map:
            mapped_proj = pid
        else:
            mapped_proj = pname or pid or 'SEM PROJETO DEFINIDO'
            
    # 2. Obter Diretoria e Centro de Custos
    info = dir_map.get(mapped_proj) or fallback_map.get(mapped_proj)
    
    if info:
        diretoria = info['Diretoria']
        cc = info['Centro de Custos']
    else:
        diretoria = 'NAO ENCONTRADO'
        cc = ''
        
    return {
        'Diretoria': diretoria,
        'Projeto': mapped_proj,
        'Centro de Custos': cc
    }

def main():
    logger.info("=== Iniciando processamento de enriquecimento do GCP-4P ===")
    proj_map, dir_map, fallback_map = build_lookup_tables()
    
    input_path = Path(INPUT_CSV)
    if not input_path.exists():
        logger.error(f"Arquivo de entrada '{INPUT_CSV}' não encontrado!")
        return

    unique_raw_projects = []
    seen = set()

    with open(input_path, mode="r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, quotechar='"', delimiter=",", escapechar="\\")
        header = next(reader)
        for line in reader:
            if line and line[0].strip():
                raw = line[0].strip()
                if raw not in seen:
                    seen.add(raw)
                    unique_raw_projects.append(raw)

    logger.info(f"Total de projetos únicos encontrados: {len(unique_raw_projects)}")

    # 1. Gerar Classificacao-4P-GCP.csv (padrão com delimitador ;)
    std_header = ["project", "Diretoria", "Projeto", "Centro de Custos"]
    with open(OUTPUT_STANDARD_CSV, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, quotechar='"', delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(std_header)
        for raw_project in unique_raw_projects:
            res = classify_project(raw_project, proj_map, dir_map, fallback_map)
            writer.writerow([
                raw_project,
                res['Diretoria'],
                res['Projeto'],
                res['Centro de Custos']
            ])
    logger.info(f"Arquivo gerado com sucesso: {OUTPUT_STANDARD_CSV} ({len(unique_raw_projects)} linhas únicas)")

    # 2. Gerar origem_gcp-4p_202608_classificado.csv (delimitador ,)
    classified_header = [c for c in header if c.strip()] + ["Diretoria", "Projeto", "Centro de Custos"]
    with open(OUTPUT_CLASSIFIED_CSV, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quotechar='"', delimiter=",", escapechar="\\")
        writer.writerow(classified_header)
        for raw_project in unique_raw_projects:
            res = classify_project(raw_project, proj_map, dir_map, fallback_map)
            writer.writerow([
                raw_project,
                res['Diretoria'],
                res['Projeto'],
                res['Centro de Custos']
            ])
    logger.info(f"Arquivo gerado com sucesso: {OUTPUT_CLASSIFIED_CSV} ({len(unique_raw_projects)} linhas únicas)")

    logger.info("=== Processamento concluído com sucesso! ===")

if __name__ == "__main__":
    main()
