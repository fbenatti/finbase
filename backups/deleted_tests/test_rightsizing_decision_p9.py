"""P9: histórico, versão e revisão — os três aceites do plano.

1. veto determinístico sobrevive a qualquer provider
2. revisão não muda premissas históricas
3. reexecução cria versão adequada, sem duplicação indevida
"""
from __future__ import annotations

from app.services import rightsizing_decision_service as ds


def _candidato(**extra):
    base = {
        "resource_id": "/x/vm1", "sku_from": "Standard_E32s_v3",
        "sku_to": "Standard_E16s_v3", "deterministic_verdict": "VALID",
        "veto_reason": "", "cost_from_current": 1000.0,
        "cost_to_estimated": 500.0, "saving_monthly": 500.0,
        "price_source_from": "CONTRACTED", "price_source_to": "CONTRACTED",
        "cpu_p95": 2.0, "mem_p95": 90.0, "observed_days": 30,
    }
    base.update(extra)
    return base


def test_mesma_evidencia_produz_mesmo_hash():
    """Reexecutar sem mudança de evidência NÃO pode parecer versão nova.

    Se `run_id` ou o instante entrassem no hash, toda reexecução criaria versão e
    o aceite "sem duplicação indevida" seria violado.
    """
    a = ds.evidence_hash(_candidato())
    b = ds.evidence_hash(_candidato())
    assert a == b
    # O hash é estável mesmo variando campos que NÃO são evidência.
    c = ds.evidence_hash(_candidato(resource_name="outro nome", run_id="999"))
    assert a == c, "campos não-materiais não podem compor o hash"


def test_evidencia_diferente_produz_hash_diferente():
    """Mudar o veredito é mudança material: nasce outra versão."""
    a = ds.evidence_hash(_candidato())
    b = ds.evidence_hash(_candidato(deterministic_verdict="INVALID"))
    assert a != b


def test_saving_ausente_nao_vira_zero_no_hash():
    """Ausência de preço é evidência diferente de preço zero.

    Se `None` e `0.0` produzissem o mesmo hash, um candidato sem preço seria
    tratado como "saving zero" — que é uma afirmação falsa.
    """
    com_preco = ds.evidence_hash(_candidato(saving_monthly=0.0))
    sem_preco = ds.evidence_hash(_candidato(saving_monthly=None))
    assert com_preco != sem_preco


def test_aprovado_nao_pode_ser_revertido():
    """Revisão é terminal: reabrir apagaria o registro de quem decidiu."""
    recusa = ds.validar_transicao("APPROVED", "REJECTED")
    assert recusa is not None
    assert "evidência nova" in recusa


def test_rejeitado_nao_pode_ser_reaberto():
    assert ds.validar_transicao("REJECTED", "APPROVED") is not None


def test_pendente_aceita_qualquer_decisao():
    for destino in ("APPROVED", "REJECTED", "DEFERRED"):
        assert ds.validar_transicao("PENDING", destino) is None


def test_estado_invalido_e_recusado():
    assert ds.validar_transicao("PENDING", "TALVEZ") is not None


def test_mesmo_estado_e_idempotente():
    """Reafirmar a revisão não é transição — não deve ser recusado."""
    assert ds.validar_transicao("APPROVED", "APPROVED") is None


def test_veredito_deterministico_esta_nos_campos_de_evidencia():
    """O veredito do motor compõe a evidência: mudá-lo cria versão e não
    sobrescreve o veto anterior. É o aceite "veto sobrevive a qualquer provider".
    """
    assert "deterministic_verdict" in ds.CAMPOS_EVIDENCIA
    assert "veto_reason" in ds.CAMPOS_EVIDENCIA
