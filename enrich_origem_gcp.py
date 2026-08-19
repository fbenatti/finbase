"""
Enriquece o CSV de origem GCP com 3 colunas de classificação:
Diretoria, Projeto e Centro de Custos.

Classificação (3 grupos):
- Departamentais -> Diretoria=DIR INFRAESTRUTURA E CLOUD, Projeto=DEPARTAMENTAIS
- Vicky          -> Diretoria=DIR ARQUITETURA E ESTRATEGIA TECH, Projeto=Vicky
- TI             -> demais projetos; o departamento é o nome lógico
  (ti-billing → Billing, ti-logging-network-* → Logging Network).
  O label landing-zone-gcp é mapeamento, não departamento.

Centro de Custos:
- TI / Departamentais -> 29TN161700 (CC da Diretoria DIR INFRAESTRUTURA E CLOUD na referência)
- Vicky -> em branco (sem CC na referência diretorias_download.csv)
Os nomes de Diretoria/Projeto e os CCs são defaults e podem ser revisados.

Uso: python3 enrich_origem_gcp.py
"""

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from gcp_logical_department import logical_department_name

INPUT = "origem_gcp-ttech_202608.csv"
OUTPUT = "origem_gcp-ttech_202608_classificado.csv"

# Defaults por classificação (diretório/projeto/CC de referência em diretorias_download.csv)
DEPARTAMENTAIS = {
    "Diretoria": "DIR INFRAESTRUTURA E CLOUD",
    "Projeto": "DEPARTAMENTAIS",
    "CentroCusto": "29TN161700",  # CC da Diretoria DIR INFRAESTRUTURA E CLOUD
}
VICKY = {
    "Diretoria": "DIR ARQUITETURA E ESTRATEGIA TECH",
    "Projeto": "Vicky",
    "CentroCusto": "",  # sem CC na referência (GCP-TI/Huwaei estão em branco)
}
TI_DEFAULT = {
    "Diretoria": "DIR INFRAESTRUTURA E CLOUD",
    "Projeto": "",       # preenchido com nome lógico (Billing, Logging Network, ...)
    "CentroCusto": "29TN161700",  # CC da Diretoria DIR INFRAESTRUTURA E CLOUD
}


def parse_labels(s: str | None) -> dict:
    """Extrai dicionário key->value dos labels ('[{'key': ..., 'value': ...}]')."""
    result: dict[str, str] = {}
    for m in re.finditer(r"'key':\s*'([^']*)',\s*'value':\s*'([^']*)'", s or ""):
        result[m.group(1)] = m.group(2)
    return result


def classify(rec: dict) -> dict:
    """Retorna {Diretoria, Projeto, Centro de Custos} dado o registro do projeto."""
    name = rec.get("name", "")
    lab = parse_labels(rec.get("labels"))

    # 1) Departamentais
    if (
        lab.get("projeto") == "departamentais"
        or name.lower().endswith("-dpt")
        or str(lab.get("id-orcamento", "")).lower() == "id000003"
    ):
        return {
            **DEPARTAMENTAIS,
            "projeto_nome": name,
            "classificacao": "DEPARTAMENTAIS",
        }

    # 2) Vicky
    if "vicky" in name.lower():
        return {
            **VICKY,
            "projeto_nome": name,
            "classificacao": "VICKY",
        }

    # 3) TI: usa nome lógico do projeto (não o slug nem o label landing-zone-gcp)
    projeto = logical_department_name(rec.get("id") or name, name, lab)

    return {
        **TI_DEFAULT,
        "Projeto": projeto,
        "projeto_nome": name,
        "classificacao": "TI",
    }


def main() -> None:
    rows: list[list[str]] = []
    with open(INPUT, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, quotechar='"', delimiter=",", escapechar="\\")
        header = next(reader)
        for line in reader:
            if line:
                rows.append(line)

    new_columns = ["Diretoria", "Projeto", "Centro de Custos"]
    out_header = [c for c in header if c.strip()] + new_columns

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quotechar='"', delimiter=",", escapechar="\\")
        writer.writerow(out_header)
        for r in rows:
            raw = r[0]
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                # mantém linha intacta e colunas vazias
                writer.writerow(r + ["", "", ""])
                continue
            info = classify(rec)
            writer.writerow([
                raw,
                info["Diretoria"],
                info["Projeto"],
                info.get("CentroCusto", ""),
            ])

    print(f"Gerado: {OUTPUT} com {len(rows)} linhas e colunas {new_columns}")


if __name__ == "__main__":
    main()
