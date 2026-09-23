"""P8: o veto por memória precisa ser provado, não presumido.

O aceite do plano é explícito: *"teste com CPU baixa mas RAM/IOPS insuficiente veta
candidato"*. Nenhum recurso real caiu nesse caso na execução atual — CPU folgada com
memória apertada é raro — então o caminho ficaria **não exercitado** se dependesse
só do dado de produção.

Estes testes constroem o caso à mão e verificam o veto, mais as outras duas regras:
determinismo e ausência de saving sem preço comparável.
"""
from __future__ import annotations

import pytest

from app.services import rightsizing_service as rs


def _catalogo(**skus) -> dict:
    """Catálogo no formato do índice: `(região, sku) -> capacidade`."""
    return {
        (regiao, sku): cap
        for sku, (regiao, cap) in skus.items()
    }


def _cap(vcpus, memory_gb, disponivel="SIM", motivo=None):
    return {
        "vcpus": vcpus,
        "memory_gb": memory_gb,
        "is_available": disponivel,
        "restriction_reason": motivo,
        "max_data_disks": 8,
    }


def test_cpu_baixa_com_memoria_insuficiente_veta():
    """CPU folgada NÃO autoriza reduzir memória abaixo do pico observado.

    Cenário: 64 GB, usando 60% (38,4 GB de pico) e apenas 1% de CPU. O SKU destino
    oferece 32 GB — cabe na CPU, não cabe na memória. Tem de vetar.
    """
    cat_idx = {
        ("brazilsouth", "Standard_E32s_v3"): _cap(32, 64),
        ("brazilsouth", "Standard_E16s_v3"): _cap(16, 32),
    }
    item = {
        "resource_id": "/x/vm1", "resource_name": "vm-memoria-apertada",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 1000.0,
        "cpu_p95": 1.0,
        # `Available Memory` em 40% => uso de 60% de 64 GB = 38,4 GB de pico.
        "mem_avail_p95": 40.0,
        "observed_days": 30, "requested_days": 30, "confidence": "ALTA",
    }
    r = rs._avaliar(item, cat_idx, {}, {}, {})

    assert r["verdict"] == "INVALID"
    assert "Memória insuficiente" in r["veto_reason"]
    # O veto cita o número: a explicação tem de ser conferível.
    assert "GB" in r["veto_reason"]


def test_memoria_suficiente_permite_reduzir():
    """O contraponto: com memória de sobra, a redução é elegível.

    Sem este caso, o teste anterior passaria mesmo se o motor vetasse tudo.
    """
    cat_idx = {
        ("brazilsouth", "Standard_E32s_v3"): _cap(32, 64),
        ("brazilsouth", "Standard_E16s_v3"): _cap(16, 32),
    }
    item = {
        "resource_id": "/x/vm2", "resource_name": "vm-folgada",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 1000.0,
        "cpu_p95": 2.0,
        # 90% de memória livre => uso de 6,4 GB; 32 GB cabem com folga.
        "mem_avail_p95": 90.0,
        "observed_days": 30, "requested_days": 30, "confidence": "ALTA",
    }
    r = rs._avaliar(item, cat_idx, {}, {}, {})

    assert r["verdict"] != "INVALID", r["veto_reason"]
    assert r["sku_to"] == "Standard_E16s_v3"


def test_sem_preco_comparavel_nao_mostra_saving():
    """Ausência de preço vira `REQUIRES_VALIDATION`, com saving **ausente**.

    Zero fingiria "economia nenhuma"; o correto é não afirmar.
    """
    cat_idx = {
        ("brazilsouth", "Standard_E32s_v3"): _cap(32, 64),
        ("brazilsouth", "Standard_E16s_v3"): _cap(16, 32),
    }
    item = {
        "resource_id": "/x/vm3", "resource_name": "vm-sem-preco",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 1000.0,
        "cpu_p95": 2.0, "mem_avail_p95": 90.0,
        "observed_days": 30, "requested_days": 30, "confidence": "ALTA",
    }
    r = rs._avaliar(item, cat_idx, {}, {}, {})  # sem preço nenhum

    assert r["verdict"] == "REQUIRES_VALIDATION"
    assert r["saving_monthly"] is None
    assert r["price_comparable"] == "NAO"


def test_sku_indisponivel_veta():
    """O aceite: SKU indisponível não é elegível, mesmo com preço bom."""
    cat_idx = {
        ("brazilsouth", "Standard_E32s_v3"): _cap(32, 64),
        ("brazilsouth", "Standard_E16s_v3"): _cap(16, 32, "NAO", "not available for subscription"),
    }
    preco = {
        ("e32sv3", "brazilsouth"): {"unit_price": 2.0, "price_source": "CONTRACTED"},
        ("e16sv3", "brazilsouth"): {"unit_price": 1.0, "price_source": "CONTRACTED"},
    }
    item = {
        "resource_id": "/x/vm4", "resource_name": "vm-sku-indisponivel",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 1000.0,
        "cpu_p95": 2.0, "mem_avail_p95": 90.0,
        "observed_days": 30, "requested_days": 30, "confidence": "ALTA",
    }
    r = rs._avaliar(item, cat_idx, preco, {}, {})

    assert r["verdict"] == "INVALID"
    assert "indisponível" in r["veto_reason"]


def test_troca_que_encarece_veta():
    """Reduzir de tamanho pode sair mais caro; recomendar seria piorar a fatura."""
    cat_idx = {
        ("brazilsouth", "Standard_E32s_v3"): _cap(32, 64),
        ("brazilsouth", "Standard_E16s_v3"): _cap(16, 32),
    }
    preco = {
        ("e32sv3", "brazilsouth"): {"unit_price": 1.0, "price_source": "CONTRACTED"},
        # O destino é mais caro por unidade (geração nova).
        ("e16sv3", "brazilsouth"): {"unit_price": 3.0, "price_source": "CONTRACTED"},
    }
    item = {
        "resource_id": "/x/vm5", "resource_name": "vm-encarece",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 1000.0,
        "cpu_p95": 2.0, "mem_avail_p95": 90.0,
        "observed_days": 30, "requested_days": 30, "confidence": "ALTA",
    }
    r = rs._avaliar(item, cat_idx, preco, {}, {})

    assert r["verdict"] == "INVALID"
    assert "não economiza" in r["veto_reason"]


def test_contratado_e_publico_nao_se_misturam():
    """Preço DE contratado e PARA público não produz saving: a comparação seria falsa."""
    cat_idx = {
        ("brazilsouth", "Standard_E32s_v3"): _cap(32, 64),
        ("brazilsouth", "Standard_E16s_v3"): _cap(16, 32),
    }
    preco = {
        ("e32sv3", "brazilsouth"): {"unit_price": 1.0, "price_source": "CONTRACTED"},
        ("e16sv3", "brazilsouth"): {"unit_price": 0.5, "price_source": "PUBLIC_RETAIL"},
    }
    item = {
        "resource_id": "/x/vm6", "resource_name": "vm-fontes-mistas",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 1000.0,
        "cpu_p95": 2.0, "mem_avail_p95": 90.0,
        "observed_days": 30, "requested_days": 30, "confidence": "ALTA",
    }
    r = rs._avaliar(item, cat_idx, preco, {}, {})

    assert r["price_comparable"] == "NAO"
    assert r["saving_monthly"] is None
    assert r["verdict"] == "REQUIRES_VALIDATION"


def test_duas_execucoes_iguais_produzem_mesma_decisao():
    """Determinismo: o hash não pode depender da ordem de retorno do banco."""
    base = [
        {"resource_id": "/x/a", "sku_to": "Standard_E16s_v3", "verdict": "VALID",
         "saving_monthly": 10.0, "cpu_p95": 2.0},
        {"resource_id": "/x/b", "sku_to": "Standard_D8s_v3", "verdict": "INVALID",
         "saving_monthly": None, "cpu_p95": 40.0},
    ]
    invertido = list(reversed(base))

    assert rs._hash_decisao(base) == rs._hash_decisao(invertido)


def test_metrica_de_memoria_usada_pelo_perfil_bate_com_o_coletor():
    """O motor lê `Available Memory`; o coletor precisa pedir esse nome.

    Um desencontro aqui deixaria o veto de memória **inerte em silêncio** — foi
    exatamente o defeito encontrado: o perfil não tinha métrica de memória.
    """
    from app.services.azure_monitor_vm_service import DEFAULT_METRICS

    assert any("Available Memory" in m for m in DEFAULT_METRICS)


def test_cobertura_insuficiente_vira_requires_validation():
    """Poucos dias observados não sustentam a afirmação de ociosidade."""
    cat_idx = {("brazilsouth", "Standard_E32s_v3"): _cap(32, 64)}
    item = {
        "resource_id": "/x/vm7", "resource_name": "vm-poucos-dias",
        "resource_sku": "Standard_E32s_v3", "region_id": "brazilsouth",
        "sub_account_id": "sub-1", "effective_cost": 100.0,
        "cpu_p95": 0.5, "mem_avail_p95": 95.0,
        "observed_days": 2, "requested_days": 15, "confidence": "BAIXA",
    }
    r = rs._avaliar(item, cat_idx, {}, {}, {})

    assert r["verdict"] == "REQUIRES_VALIDATION"
    assert "dia(s) observado(s)" in r["veto_reason"]
