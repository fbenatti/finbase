"""Cadastra (ou atualiza) a conexão OCI a partir do arquivo de config em env/.

Lê o config OCI (formato ~/.oci/config) e a chave privada indicada nele, da
pasta montada em ``OCI_CONFIG_DIR`` (/run/finbase-env), e grava tenancy,
usuário, fingerprint, região e chave na conexão. Depois testa o acesso.

Uso (dentro do container da API):
    python cadastrar_conexao_oci.py [--arquivo oci_config.conf] [--perfil DEFAULT]
                                    [--codigo oci] [--nome "Oracle Cloud"]

Só imprime dados não sensíveis; a chave e o fingerprint nunca vão para a tela.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.source_connection import SourceConnection
from app.services.oci_extractor import OCI_CONFIG_DIR, OciExtractor, _oci_config_from_file


def _config_file_padrao() -> str:
    arquivos = sorted(p.name for p in OCI_CONFIG_DIR.glob("*.conf"))
    if len(arquivos) != 1:
        raise SystemExit(f"Informe --arquivo; encontrados em {OCI_CONFIG_DIR}: {arquivos or 'nenhum'}")
    return arquivos[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arquivo", help="Arquivo de config dentro de env/ (padrão: o único .conf da pasta)")
    parser.add_argument("--perfil", default="DEFAULT", help="Perfil do arquivo de config")
    parser.add_argument("--codigo", default="oci", help="Identificador da conexão")
    parser.add_argument("--nome", default="Oracle Cloud", help="Nome da conexão")
    args = parser.parse_args()

    arquivo = args.arquivo or _config_file_padrao()
    config = _oci_config_from_file(arquivo, args.perfil)
    chave = Path(config["key_file"]).read_text()

    engine = create_engine(os.environ["DATABASE_URL_SYNC"])
    with Session(engine) as session:
        conexao = session.scalar(select(SourceConnection).where(SourceConnection.code == args.codigo))
        anterior = dict(conexao.config_json or {}) if conexao else {}
        if conexao is None:
            conexao = SourceConnection(
                code=args.codigo, name=args.nome, connector_type="oci",
                cloud_name=args.nome, cloud_scope="oci", blob_prefixes=["reports/cost-csv"],
            )
            session.add(conexao)
        # Mantém o que não é credencial (namespace, compartments de IA, etc.).
        anterior.pop("config_file", None)
        anterior.pop("config_profile", None)
        conexao.config_json = {
            **anterior,
            "tenancy_ocid": config["tenancy"],
            "user_ocid": config["user"],
            "fingerprint": config["fingerprint"],
            "region": config.get("region") or "sa-saopaulo-1",
            "private_key": chave,
        }
        session.commit()
        session.refresh(conexao)
        print(f"Conexão '{conexao.code}' (id {conexao.id}) gravada a partir de {arquivo}.")
        print(f"  tenancy: {config['tenancy']}")
        print(f"  usuário: {config['user']}")
        print(f"  região:  {conexao.config_json['region']}")
        resultado = OciExtractor(conexao).test_connection()
        print(f"Teste de acesso: {resultado['status']} - {resultado['message']}")


if __name__ == "__main__":
    main()
