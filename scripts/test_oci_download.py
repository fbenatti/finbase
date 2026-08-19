#!/usr/bin/env python3
"""
Script para testar a conexão com a OCI e listar/baixar relatórios de Cost e Usage Reports de Billing.
Logs detalhados de output em conformidade com as diretrizes do OpenFinops.
"""

import os
import sys
import argparse
import gzip
import io
import oci
from datetime import datetime

def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def parse_args():
    parser = argparse.ArgumentParser(description="Testar conexao OCI e baixar reports de faturamento")
    parser.add_argument("--user-ocid", required=True, help="User OCID da OCI")
    parser.add_argument("--tenancy-ocid", required=True, help="Tenancy OCID da OCI")
    parser.add_argument("--region", default="sa-saopaulo-1", help="Regiao OCI (padrao: sa-saopaulo-1)")
    parser.add_argument("--fingerprint", default="d1:e1:83:86:7c:98:12:1b:83:fd:a2:37:50:e4:96:4e", help="Fingerprint da chave")
    parser.add_argument("--key-file", default=os.path.expanduser("~/Downloads/fernando.benatti@telefonicati.onmicrosoft.com-2026-08-18T14_42_44.952Z.pem"), help="Caminho para chave privada .pem")
    parser.add_argument("--month", default="2026-08", help="Mes para filtrar (ex: 2026-08, 2025-08, 2024-08)")
    parser.add_argument("--output-dir", default="./data/oci_test_downloads", help="Diretorio de destino para downloads")
    parser.add_argument("--max-files", type=int, default=3, help="Maximo de arquivos para baixar no teste")
    return parser.parse_args()

def test_oci_connection_and_download(args):
    log("=" * 70)
    log("INICIANDO TESTE DE CONEXAO OCI & DOWNLOAD DE REPORTS")
    log("=" * 70)
    
    if not os.path.exists(args.key_file):
        log(f"[ERRO] Arquivo de chave privada nao encontrado: {args.key_file}")
        sys.exit(1)
        
    log(f"[CONFIG] Tenancy OCID: {args.tenancy_ocid[:20]}...{args.tenancy_ocid[-10:] if len(args.tenancy_ocid) > 30 else ''}")
    log(f"[CONFIG] User OCID:    {args.user_ocid[:20]}...{args.user_ocid[-10:] if len(args.user_ocid) > 30 else ''}")
    log(f"[CONFIG] Regiao:       {args.region}")
    log(f"[CONFIG] Fingerprint:  {args.fingerprint}")
    log(f"[CONFIG] Chave PEM:    {args.key_file}")
    log(f"[CONFIG] Filtro Mes:   {args.month}")
    
    config = {
        "user": args.user_ocid,
        "key_file": args.key_file,
        "fingerprint": args.fingerprint,
        "tenancy": args.tenancy_ocid,
        "region": args.region
    }
    
    # 1. Validar configuracao OCI
    try:
        log("[ETAPA 1/4] Validando configuracao e autenticacao OCI...")
        oci.config.validate_config(config)
        log("[OK] Configuracao OCI validada com sucesso.")
    except Exception as e:
        log(f"[FALHA] Erro na validacao da configuracao OCI: {e}")
        sys.exit(1)

    # 2. Testar Object Storage Client no namespace de faturamento (bling)
    # A OCI armazena relatórios de custo e uso no namespace 'bling' e bucket = tenancy_ocid
    namespace = "bling"
    bucket_name = args.tenancy_ocid
    
    log(f"[ETAPA 2/4] Conectando ao Object Storage (Namespace: '{namespace}', Bucket: '{bucket_name[:25]}...')...")
    
    try:
        object_storage_client = oci.object_storage.ObjectStorageClient(config)
        
        # Testar listagem de objetos com prefixo reports/cost-csv/ ou reports/usage-csv/
        prefixes_to_test = ["reports/cost-csv", "reports/usage-csv"]
        found_objects = []
        
        for prefix in prefixes_to_test:
            log(f"[BUSCA] Listando objetos no bucket com prefixo '{prefix}'...")
            list_objects_response = object_storage_client.list_objects(
                namespace_name=namespace,
                bucket_name=bucket_name,
                prefix=prefix,
                fields="name,size,timeCreated,md5"
            )
            
            objs = list_objects_response.data.objects
            log(f"[INFO] Encontrados {len(objs)} arquivos com prefixo '{prefix}'.")
            found_objects.extend(objs)
            
        if not found_objects:
            log("[AVISO] Nenhum objeto encontrado com prefixos padrao. Listando raiz do bucket...")
            list_root = object_storage_client.list_objects(
                namespace_name=namespace,
                bucket_name=bucket_name,
                fields="name,size,timeCreated,md5",
                limit=50
            )
            found_objects = list_root.data.objects
            log(f"[INFO] Encontrados {len(found_objects)} arquivos na raiz do bucket.")
            
    except oci.exceptions.ServiceError as se:
        log(f"[FALHA OCI SERVICE ERROR] Codigo: {se.code}, Status: {se.status}, Mensagem: {se.message}")
        log("[DICA] Verifique se a politica IAM para leitura de usage-reports foi criada no compartimento raiz.")
        sys.exit(1)
    except Exception as e:
        log(f"[FALHA INESPERADA] {e}")
        sys.exit(1)

    # 3. Filtrar relatórios pelo mês especificado
    log(f"[ETAPA 3/4] Filtrando relatórios para o período '{args.month}'...")
    filtered_objects = []
    for obj in found_objects:
        # Formatos comuns da OCI:
        # reports/cost-csv/0001000000123456.csv.gz (data esta no conteudo ou no timeCreated)
        time_created_str = str(obj.time_created) if obj.time_created else ""
        if args.month in time_created_str or args.month in obj.name:
            filtered_objects.append(obj)
            
    if not filtered_objects:
        log(f"[AVISO] Nenhum arquivo filtrado pelo mes '{args.month}'. Mostrando os 5 arquivos mais recentes:")
        # Ordenar por data de criacao decrescente
        sorted_objs = sorted(found_objects, key=lambda x: x.time_created or datetime.min, reverse=True)
        for idx, obj in enumerate(sorted_objs[:5], 1):
            log(f"   {idx}. {obj.name} (Tamanho: {obj.size or 0} bytes, Criado em: {obj.time_created})")
        target_downloads = sorted_objs[:args.max_files]
    else:
        log(f"[OK] Encontrados {len(filtered_objects)} relatórios correspondentes ao mês '{args.month}'.")
        for idx, obj in enumerate(filtered_objects[:10], 1):
            log(f"   {idx}. {obj.name} (Tamanho: {obj.size or 0} bytes, Criado em: {obj.time_created})")
        target_downloads = filtered_objects[:args.max_files]

    # 4. Download e inspeção de cabeçalho / linhas de teste
    log(f"[ETAPA 4/4] Baixando {len(target_downloads)} arquivo(s) de teste...")
    os.makedirs(args.output_dir, exist_ok=True)
    
    for idx, obj in enumerate(target_downloads, 1):
        filename = os.path.basename(obj.name)
        dest_path = os.path.join(args.output_dir, filename)
        log(f"\n---> [DOWNLOAD {idx}/{len(target_downloads)}] Baixando '{obj.name}' ({obj.size} bytes)...")
        
        get_obj_response = object_storage_client.get_object(
            namespace_name=namespace,
            bucket_name=bucket_name,
            object_name=obj.name
        )
        
        # Salvar arquivo localmente
        content = get_obj_response.data.content
        with open(dest_path, "wb") as f:
            f.write(content)
        log(f"[SALVO] Arquivo gravado em: {dest_path}")
        
        # Descompactar e inspecionar primeiras linhas
        try:
            if filename.endswith(".gz"):
                with gzip.open(io.BytesIO(content), "rt", encoding="utf-8", errors="ignore") as gz:
                    header = gz.readline().strip()
                    sample_row1 = gz.readline().strip()
                    sample_row2 = gz.readline().strip()
            else:
                with open(dest_path, "r", encoding="utf-8", errors="ignore") as f:
                    header = f.readline().strip()
                    sample_row1 = f.readline().strip()
                    sample_row2 = f.readline().strip()
                    
            log(f"[INSPECAO SCHEMA OCI] Colunas encontradas ({len(header.split(','))} colunas):")
            log(f"   Header: {header[:200]}...")
            log(f"   Amostra Linha 1: {sample_row1[:200]}...")
            log(f"   Amostra Linha 2: {sample_row2[:200]}...")
        except Exception as ex:
            log(f"[AVISO] Nao foi possivel inspecionar o arquivo descompactado: {ex}")

    log("\n" + "=" * 70)
    log("[CONCLUIDO] TESTE DE CONEXAO E DOWNLOAD EXECUTADO COM SUCESSO!")
    log(f"Arquivos salvos em: {os.path.abspath(args.output_dir)}")
    log("=" * 70)

if __name__ == "__main__":
    test_oci_connection_and_download(parse_args())
