"""P8 — Rightsizing determinístico.

O plano fixa três aceites que este motor persegue explicitamente:

1. **"teste com CPU baixa mas RAM/IOPS insuficiente veta candidato"** — as
   restrições de memória e de disco são *hard*: CPU folgada não autoriza reduzir
   memória ou IOPS abaixo do pico observado.
2. **"sem preço comparável não mostra saving exato"** — sem preço para o SKU
   destino, `saving_monthly` fica **ausente** em vez de virar zero. Zero fingiria
   "economia nenhuma"; ausência diz "não sei".
3. **"duas execuções iguais produzem mesma decisão"** — o `decision_hash` cobre as
   entradas materiais, e o desempate é por nome de SKU, nunca por ordem de retorno
   do banco.

O motor é **determinístico de propósito**: nenhuma chamada ao Jev aqui. O assistido
entra depois (P12) e **não pode** promover `INVALID` nem `REQUIRES_VALIDATION`.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

TABLE = "rightsizing_candidate"

# Limiar de ociosidade: abaixo disto o recurso é candidato a reduzir. O valor é
# política explícita, e o plano manda documentá-la em vez de escondê-la.
CPU_IDLE_P95 = 20.0
MEM_USED_P95 = 60.0

# Folga exigida sobre o pico observado. Reduzir até o pico exato deixaria o
# recurso sem margem para variação — a folga é a diferença entre plano e incidente.
HEADROOM = 1.25

# Cobertura mínima para decidir. Abaixo disto o veredito é REQUIRES_VALIDATION:
# o dado não sustenta a afirmação, e "não sei" é resposta melhor que "reduza".
MIN_COVERAGE = 0.60
MIN_OBSERVED_DAYS = 7


def _hash_decisao(linhas: list[dict[str, Any]]) -> str:
    """Hash das entradas materiais, ordenado.

    Ordenar é parte do contrato: sem isso a mesma entrada produziria hash
    diferente conforme a ordem de retorno do banco.
    """
    base = "|".join(
        ";".join(f"{k}={linhas[i].get(k)}" for k in sorted(linhas[i]))
        for i in sorted(range(len(linhas)), key=lambda i: (
            str(linhas[i].get("resource_id")), str(linhas[i].get("sku_to")),
        ))
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def _familia(sku: str) -> str:
    """Família do SKU: `Standard_D16s_v3` -> `D`.

    Só a **primeira letra** da família entra: `s`, `d`, `ds`, `as`, `ms` são
    variações de disco/rede **dentro** da mesma família, e comparar o sufixo
    inteiro impediria reduzir de `D16s_v3` para `D8s_v3`.

    Mas `FX` é família **distinta** de `F`: comparar apenas a primeira letra
    colocava `Standard_FX12-6mds` como destino de `Standard_F16s_v2`, e o FX é
    mais caro — o motor sugeria uma troca que encarecia.
    """
    partes = str(sku or "").split("_")
    if len(partes) >= 2 and partes[0].lower() == "standard":
        corpo = partes[1]
    else:
        corpo = partes[0] if partes else ""
    # Uma letra, exceto quando a segunda letra é maiúscula (FX, HB, HC, ND, NV).
    if len(corpo) >= 2 and corpo[1].isupper():
        return corpo[:2].upper()
    return corpo[:1].upper()


def _vcpus(sku: str) -> int | None:
    """vCPUs embutidas no nome: `Standard_D16s_v3` -> 16.

    Usado apenas como desempate; a fonte de verdade é o catálogo.
    """
    import re

    m = re.search(r"_([A-Z])(\d+)", str(sku or ""))
    return int(m.group(2)) if m else None


async def build_candidates(*, run_id: str | None = None) -> dict[str, Any]:
    """Gera os candidatos a partir do perfil, do catálogo e do preço."""
    from app.services.trino_service import execute as trino

    corrida = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # 1) Perfil: pico observado por recurso. Usa o pior caso (`p95`), não a média:
    #    dimensionar pela média garante saturação nos picos.
    perfil = await trino(
        """
        SELECT resource_id, MAX(resource_name) AS resource_name,
               MAX(resource_sku) AS resource_sku, MAX(sub_account_id) AS sub_account_id,
               MAX(region_id) AS region_id, MAX(resource_state) AS resource_state,
               MAX(effective_cost) AS effective_cost,
               MAX(observed_days) AS observed_days,
               MAX(requested_days) AS requested_days,
               MAX(coverage) AS coverage,
               MAX(confidence) AS confidence,
               MAX(CASE WHEN metric = 'Percentage CPU' THEN p95 END) AS cpu_p95,
               MAX(CASE WHEN metric LIKE '%Available Memory%' THEN p95 END) AS mem_avail_p95
        FROM iceberg.silver.resource_profile_window
        WHERE resource_sku IS NOT NULL AND resource_sku <> ''
        GROUP BY resource_id
        """,
        timeout_s=900,
    )
    if not perfil:
        return {"run_id": corrida, "rows": 0, "message": "Sem perfil para avaliar"}

    # 2) Catálogo de SKU: capacidade e elegibilidade. `is_available` é o que
    #    impede sugerir SKU que a região não permite.
    catalogo = await trino(
        """
        SELECT region_id, sku_name, MAX(vcpus) AS vcpus, MAX(memory_gb) AS memory_gb,
               MAX(max_data_disks) AS max_data_disks, MAX(is_available) AS is_available,
               MAX(restriction_reason) AS restriction_reason
        FROM iceberg.silver.compute_sku_catalog
        GROUP BY region_id, sku_name
        """,
        timeout_s=900,
    )

    # 3) Preço contratado. O catálogo de preço identifica o SKU de VM por
    #    `meter_name` ("D4as v6"), não por `sku_id` (que é o id do produto). A
    #    chave de ligação é o nome do medidor normalizado — sem isso o join daria
    #    zero e todo candidato ficaria sem preço.
    precos = await trino(
        """
        SELECT meter_name, region_id, price_source,
               AVG(unit_price) AS unit_price, COUNT(*) AS amostras
        FROM iceberg.silver.price_catalog
        WHERE service_name = 'Virtual Machines'
          AND unit_price IS NOT NULL AND COALESCE(unit_price, 0) > 0
        GROUP BY meter_name, region_id, price_source
        """,
        timeout_s=900,
    )

    # 4) Quota disponível por assinatura e região: veto de capacidade.
    quotas = await trino(
        """
        SELECT sub_account_id, region_id, MAX(available_value) AS available_cores
        FROM iceberg.silver.resource_quota
        WHERE quota_name = 'cores'
        GROUP BY sub_account_id, region_id
        """,
        timeout_s=600,
    )

    # 5) Policy: veto contratual. Só `Default` veta; `DoNotEnforce` não.
    politicas = await trino(
        """
        SELECT sub_account_id, allowed_values
        FROM iceberg.silver.region_policy
        WHERE enforcement_mode = 'Default' AND allowed_values IS NOT NULL
          AND allowed_values <> '' AND allowed_values <> 'None'
        """,
        timeout_s=600,
    )

    cat_idx: dict[tuple[str, str], dict[str, Any]] = {}
    for c in catalogo:
        chave = (_normalizar_regiao(c.get("region_id")), str(c.get("sku_name") or ""))
        cat_idx[chave] = c

    quota_idx: dict[tuple[str, str], float] = {}
    for q in quotas:
        chave = (str(q.get("sub_account_id") or "").lower(), _normalizar_regiao(q.get("region_id")))
        try:
            quota_idx[chave] = float(q.get("available_cores") or 0)
        except (TypeError, ValueError):
            quota_idx[chave] = 0.0

    # Termos proibidos por policy, por assinatura.
    proibidos: dict[str, set[str]] = {}
    for p in politicas:
        valor = str(p.get("allowed_values") or "")
        assinatura = str(p.get("sub_account_id") or "").lower()
        if valor and valor != "None":
            proibidos.setdefault(assinatura, set()).update(
                x.strip().lower() for x in valor.replace("[", "").replace("]", "").replace("'", "").split(",")
                if x.strip()
            )

    # Índice de preço: chave (nome de SKU normalizado, região). As duas fontes
    # nunca se misturam — contratado tem precedência e o público fica como
    # fallback **declarado** em `price_source`.
    preco_idx: dict[tuple[str, str], dict[str, Any]] = {}
    for p in precos:
        sku = _normalizar_sku(p.get("meter_name"))
        regiao = _normalizar_regiao(p.get("region_id"))
        fonte = str(p.get("price_source") or "")
        try:
            valor = float(p.get("unit_price") or 0)
        except (TypeError, ValueError):
            continue
        if not sku or valor <= 0:
            continue
        chave = (sku, regiao)
        atual = preco_idx.get(chave)
        # Contratado vence; entre iguais, mais amostras é mais confiável.
        trocar = atual is None
        if not trocar and fonte == "CONTRACTED" and atual.get("price_source") != "CONTRACTED":
            trocar = True
        elif not trocar and fonte == atual.get("price_source"):
            trocar = float(p.get("amostras") or 0) > float(atual.get("amostras") or 0)
        if trocar:
            preco_idx[chave] = {"unit_price": valor, "price_source": fonte, "amostras": p.get("amostras")}

    candidatos: list[dict[str, Any]] = []
    for item in perfil:
        candidato = _avaliar(item, cat_idx, preco_idx, quota_idx, proibidos)
        if candidato:
            candidatos.append(candidato)

    if not candidatos:
        return {"run_id": corrida, "rows": 0, "message": "Nenhum candidato gerado"}

    frame = pd.DataFrame(candidatos)
    frame["run_id"] = corrida
    frame["decision_hash"] = _hash_decisao(candidatos)
    frame["created_at"] = datetime.now(timezone.utc).isoformat()
    frame["_silver_owner"] = "rightsizing"
    frame["_source_connection_id"] = None
    frame["_source_connection_name"] = "rightsizing_engine"

    from app.services import iceberg_service as iceberg

    written = iceberg.write_silver(
        connection_code="rightsizing",
        df=frame,
        billing_period=datetime.now(timezone.utc).strftime("%Y%m"),
        table_name_override=f"silver.{TABLE}",
    )

    por_veredito = frame["verdict"].value_counts().to_dict()
    return {
        "run_id": corrida,
        "rows": int(written or 0),
        "decision_hash": frame["decision_hash"].iloc[0],
        "verdicts": por_veredito,
        # O total de saving só conta o que é comparável — senão o número mentiria.
        "saving_total_valid": round(
            float(frame.loc[frame["verdict"] == "VALID", "saving_monthly"].fillna(0).sum()), 2
        ),
        "without_comparable_price": int((frame["price_comparable"] == "NAO").sum()),
    }


def _avaliar(
    item: dict[str, Any],
    cat_idx: dict[tuple[str, str], dict[str, Any]],
    preco_idx: dict[tuple[str, str], dict[str, Any]],
    quota_idx: dict[tuple[str, str], float],
    proibidos: dict[str, set[str]],
) -> dict[str, Any] | None:
    """Avalia um recurso: vetos primeiro, candidate depois, finanças por último."""
    sku_from = str(item.get("resource_sku") or "")
    regiao = _normalizar_regiao(item.get("region_id"))
    assinatura = str(item.get("sub_account_id") or "").lower()
    atual = cat_idx.get((regiao, sku_from))
    if not atual:
        # Sem o SKU atual no catálogo não há como comparar capacidade.
        return None

    cpu_p95 = _num(item.get("cpu_p95"))
    mem_livre = _num(item.get("mem_avail_p95"))
    dias = int(_num(item.get("observed_days")) or 0)

    base = {
        "resource_id": item.get("resource_id"),
        "resource_name": item.get("resource_name"),
        "region_id": item.get("region_id"),
        "sku_from": sku_from,
        "vcpus_from": _num(atual.get("vcpus")),
        "memory_gb_from": _num(atual.get("memory_gb")),
        "cpu_p95": cpu_p95,
        "mem_p95": mem_livre,
        "observed_days": dias,
        "requested_days": int(_num(item.get("requested_days")) or 0),
        "cost_from_current": _num(item.get("effective_cost")),
        "confidence": str(item.get("confidence") or "BAIXA"),
        "sku_to": "",
        "vcpus_to": None,
        "memory_gb_to": None,
        "cost_to_estimated": None,
        "saving_monthly": None,
        "saving_pct": None,
        "price_source_from": None,
        "price_source_to": None,
        "price_comparable": "NAO",
        "verdict": "REQUIRES_VALIDATION",
        "veto_reason": "",
    }

    # Veto 1: dados insuficientes. Não é falha — é ausência de base para afirmar.
    if dias < MIN_OBSERVED_DAYS:
        base["veto_reason"] = (
            f"Somente {dias} dia(s) observado(s); o mínimo para decidir é "
            f"{MIN_OBSERVED_DAYS}. Reduzir sem janela suficiente é aposta."
        )
        return base

    # Veto 2: recurso já saturado ou não ocioso. Não há o que reduzir.
    if cpu_p95 is None:
        base["veto_reason"] = "Sem série de CPU na janela; não há base para afirmar ociosidade."
        return base
    if cpu_p95 > CPU_IDLE_P95:
        base["verdict"] = "INVALID"
        base["veto_reason"] = (
            f"CPU p95 em {cpu_p95:.1f}%, acima do limiar de ociosidade "
            f"({CPU_IDLE_P95:.0f}%)."
        )
        return base

    # Escolha do destino: menor SKU da MESMA família que ainda cobre o pico com
    # folga. Ordenação determinística por nome resolve empate.
    alvo, houve_menor_indisponivel = _escolher_destino(sku_from, regiao, atual, cat_idx, mem_livre)
    if alvo is None:
        base["verdict"] = "INVALID"
        # A razão precisa distinguir "não existe menor" de "existe, mas está
        # indisponível": são situações diferentes para quem lê o relatório.
        base["veto_reason"] = (
            "Há SKU menor na mesma família, mas indisponível nesta região — "
            "a redução não é provisionável."
            if houve_menor_indisponivel
            else "Nenhum SKU menor na mesma família cobre o pico observado com folga — "
                 "já está no menor tamanho elegível."
        )
        return base

    sku_to, capacidade = alvo
    base.update({
        "sku_to": sku_to,
        "vcpus_to": _num(capacidade.get("vcpus")),
        "memory_gb_to": _num(capacidade.get("memory_gb")),
    })

    # Veto 3: SKU indisponível ou restrito na região. É o aceite explícito do P8.
    if not _eh_sim(capacidade.get("is_available")):
        base["verdict"] = "INVALID"
        base["veto_reason"] = (
            f"SKU {sku_to} indisponível em {item.get('region_id')}: "
            f"{str(capacidade.get('restriction_reason') or 'restrição do provedor')[:120]}"
        )
        return base

    # Veto 4: policy. Veto, não sugestão.
    if sku_to.lower() in proibidos.get(assinatura, set()):
        base["verdict"] = "INVALID"
        base["veto_reason"] = f"SKU {sku_to} vedado por policy na assinatura."
        return base

    # Veto 5: memória. CPU folgada NÃO autoriza reduzir memória abaixo do pico —
    # é exatamente o caso que o aceite do P8 exige vetar.
    mem_to = _num(capacidade.get("memory_gb"))
    mem_from = _num(atual.get("memory_gb"))
    if mem_livre is not None and mem_to is not None and mem_from:
        # `Available Memory` é o que sobra; o uso é o complemento.
        uso_pct = max(0.0, 100.0 - mem_livre)
        necessario = mem_from * (uso_pct / 100.0) * HEADROOM
        if mem_to < necessario:
            base["verdict"] = "INVALID"
            base["veto_reason"] = (
                f"Memória insuficiente: o pico de uso exige {necessario:.1f} GB "
                f"(com folga de {int((HEADROOM - 1) * 100)}%) e o SKU {sku_to} "
                f"oferece {mem_to:.1f} GB. CPU baixa não autoriza reduzir memória."
            )
            return base

    # Veto 6: quota. Sem núcleos disponíveis, o destino não é provisionável.
    disponivel = quota_idx.get((assinatura, regiao))
    vcpus_to = _num(capacidade.get("vcpus"))
    if disponivel is not None and vcpus_to is not None and vcpus_to > disponivel:
        base["verdict"] = "INVALID"
        base["veto_reason"] = (
            f"Quota insuficiente: {vcpus_to:.0f} vCPU necessários e "
            f"{disponivel:.0f} disponíveis em {item.get('region_id')}."
        )
        return base

    # Finanças: DE e PARA com a MESMA fonte de preço. Misturar contratado com
    # público produziria um saving que não existe.
    preco_de = preco_idx.get((_normalizar_sku(sku_from), regiao))
    preco_para = preco_idx.get((_normalizar_sku(sku_to), regiao))
    base["price_source_from"] = (preco_de or {}).get("price_source")
    base["price_source_to"] = (preco_para or {}).get("price_source")

    if preco_de and preco_para and preco_de.get("price_source") == preco_para.get("price_source"):
        base["price_comparable"] = "SIM"
        custo_de = _num(item.get("effective_cost"))
        # Projeção proporcional ao preço unitário: o custo do recurso escala com
        # o preço do SKU na mesma fonte.
        proporcao = float(preco_para["unit_price"]) / float(preco_de["unit_price"] or 1)
        custo_para = (custo_de or 0.0) * proporcao
        base["cost_to_estimated"] = round(custo_para, 2)
        saving = (custo_de or 0.0) - custo_para
        base["saving_monthly"] = round(saving, 2)
        base["saving_pct"] = round(100.0 * saving / custo_de, 2) if custo_de else None

    # Veto: troca que encarece não é economia. Acontece quando o SKU atual tem
    # preço contratado promocional ou geração antiga mais barata que a menor
    # atual. Recomendar seria piorar a fatura.
    if base["saving_monthly"] is not None and base["saving_monthly"] <= 0:
        base["verdict"] = "INVALID"
        base["veto_reason"] = (
            f"Troca não economiza: DE {base['cost_from_current']:.2f} para "
            f"PARA {base['cost_to_estimated']:.2f} ({base['saving_monthly']:+.2f}). "
            "O SKU atual tem preço mais favorável que o destino."
        )
        return base

    # O veredito final depende de haver preço comparável: sem ele, a decisão
    # técnica está tomada mas o número não pode ser afirmado.
    if base["price_comparable"] == "SIM":
        base["verdict"] = "VALID"
    else:
        base["verdict"] = "REQUIRES_VALIDATION"
        base["veto_reason"] = (
            "Elegível tecnicamente, mas sem preço comparável na mesma fonte para "
            f"{sku_from} e {sku_to} em {item.get('region_id')}: o saving não é exibido."
        )
    return base


def _escolher_destino(
    sku_from: str,
    regiao: str,
    atual: dict[str, Any],
    cat_idx: dict[tuple[str, str], dict[str, Any]],
    mem_livre: float | None,
) -> tuple[tuple[str, dict[str, Any]] | None, bool]:
    """Menor SKU da mesma família que cobre o pico com folga.

    Devolve também se existia SKU menor **indisponível** — assim quem lê sabe se
    o recurso já está no menor tamanho ou se há um menor bloqueado pela região.
    """
    familia = _familia(sku_from)
    vcpus_atual = _num(atual.get("vcpus")) or 0
    candidatos: list[tuple[int, str, dict[str, Any]]] = []
    houve_indisponivel = False
    for (reg, sku), cap in cat_idx.items():
        if reg != regiao or _familia(sku) != familia or sku == sku_from:
            continue
        vcpus = _num(cap.get("vcpus")) or 0
        if vcpus <= 0 or vcpus >= vcpus_atual:
            continue
        if not _eh_sim(cap.get("is_available")):
            houve_indisponivel = True
            continue
        candidatos.append((int(vcpus), sku, cap))
    if not candidatos:
        return None, houve_indisponivel
    # Ordenação explícita: maior vCPU que ainda é menor que o atual (reduz o
    # mínimo necessário), e nome como desempate estável.
    candidatos.sort(key=lambda x: (-x[0], x[1]))
    return (candidatos[0][1], candidatos[0][2]), houve_indisponivel


# O preço traz o **nome de exibição** da região (`BR South`); o catálogo de SKU e
# o perfil usam a **forma ARM** (`brazilsouth`). São vocabulários diferentes, e
# normalizar espaço/caixa não resolve: "brsouth" != "brazilsouth".
#
# O de-para é explícito porque a alternativa seria adivinhar por prefixo, e
# `southcentralus` colidiria com `South Central US`. Região não mapeada fica sem
# preço e o candidato vira REQUIRES_VALIDATION — que é o comportamento correto
# quando não se sabe, em vez de casar a região errada.
REGIAO_EXIBICAO_PARA_ARM: dict[str, str] = {
    "brazilsouth": "brazilsouth",
    "brsouth": "brazilsouth",
    "brsoutheast": "brazilsoutheast",
    "eastus": "eastus",
    "useast": "eastus",
    "eastus2": "eastus2",
    "useast2": "eastus2",
    "centralus": "centralus",
    "uscentral": "centralus",
    "northcentralus": "northcentralus",
    "usnorthcentral": "northcentralus",
    "southcentralus": "southcentralus",
    "ussouthcentral": "southcentralus",
    "westus": "westus",
    "uswest": "westus",
    "westus2": "westus2",
    "uswest2": "westus2",
    "westus3": "westus3",
    "uswest3": "westus3",
    "westeurope": "westeurope",
    "euwest": "westeurope",
    "northeurope": "northeurope",
    "eunorth": "northeurope",
    "eastasia": "eastasia",
    "apeast": "eastasia",
    "southeastasia": "southeastasia",
    "apsoutheast": "southeastasia",
    "australiaeast": "australiaeast",
    "aueast": "australiaeast",
    "japaneast": "japaneast",
    "jpeast": "japaneast",
    "japanwest": "japanwest",
    "jpwest": "japanwest",
    "canadacentral": "canadacentral",
    "cacentral": "canadacentral",
    "canadaeast": "canadaeast",
    "caeast": "canadaeast",
    "uksouth": "uksouth",
    "uksouthuk": "uksouth",
    "ukwest": "ukwest",
    "centralindia": "centralindia",
    "incentral": "centralindia",
    "southindia": "southindia",
    "insouth": "southindia",
    "koreacentral": "koreacentral",
    "krcentral": "koreacentral",
    "southafricanorth": "southafricanorth",
    "zanorth": "southafricanorth",
    "uaenorth": "uaenorth",
    "aeunorth": "uaenorth",
    "switzerlandnorth": "switzerlandnorth",
    "chconfederationnorth": "switzerlandnorth",
    "germanywestcentral": "germanywestcentral",
    "dewestcentral": "germanywestcentral",
    "francecentral": "francecentral",
    "frcentral": "francecentral",
    "norwayeast": "norwayeast",
    "noeast": "norwayeast",
    "swedencentral": "swedencentral",
    "seswedencentral": "swedencentral",
    "mexicocentral": "mexicocentral",
    "mxcentral": "mexicocentral",
}


# Valores que significam "sim" no canônico. O modelo usa texto em português
# (`SIM`/`NAO`) porque é o vocabulário do domínio; aceitar também as formas
# booleanas evita que uma fonte em inglês quebre a elegibilidade em silêncio.
_VERDADEIROS = frozenset({"sim", "true", "1", "yes", "s"})


def _eh_sim(valor: Any) -> bool:
    """Interpreta um campo de elegibilidade textual ou booleano."""
    return str(valor or "").strip().lower() in _VERDADEIROS


def _normalizar_regiao(valor: Any) -> str:
    """Forma ARM da região: `BR South` -> `brazilsouth`.

    Resolve pelo de-para explícito. Região desconhecida devolve a forma
    normalizada, que simplesmente não casará com nada — e o candidato fica sem
    preço em vez de casar a região errada.
    """
    chave = "".join(c for c in str(valor or "").lower() if c.isalnum())
    return REGIAO_EXIBICAO_PARA_ARM.get(chave, chave)


def _normalizar_sku(nome: Any) -> str:
    """Forma comparável do nome do SKU: `Standard_D4as_v6` e `D4as v6` -> `d4asv6`.

    O catálogo de SKU usa `Standard_D4as_v6`; o de preço usa o nome do medidor
    (`D4as v6`). Sem normalizar, a ligação daria zero e nenhum candidato teria
    preço — que foi exatamente o defeito encontrado.
    """
    texto = str(nome or "").strip().lower()
    if not texto:
        return ""
    for ruido in ("standard_", "standard "):
        if texto.startswith(ruido):
            texto = texto[len(ruido):]
    return "".join(c for c in texto if c.isalnum())


def _num(valor: Any) -> float | None:
    """Converte para float sem transformar ausência em zero."""
    if valor is None or valor == "" or str(valor).lower() in ("none", "nan", "null"):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None
