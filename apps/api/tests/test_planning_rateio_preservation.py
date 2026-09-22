"""Regressões do merge de rateio no planejamento (P1A).

O bug corrigido: ``merge_planning_rateio`` **lia** a tabela de planejamento de
``gold`` enquanto ``_publish_planning_dataframe`` **escrevia** em ``silver``. A
leitura voltava sempre vazia, então cada execução de rateio republicava a tabela
apenas com linhas ``tipo='rateio'`` e apagava silenciosamente ``realizado`` e
``forecast``.

Os testes abaixo falham se qualquer um desses tipos desaparecer, e também
verificam que o merge nunca destrói dados que não conseguiu ler.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.services import iceberg_service as ice
from app.services import lakehouse_layers as layers


class _FakeTable:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def scan(self):
        return self

    def to_pandas(self) -> pd.DataFrame:
        return self._frame.copy()


class _MissingTableError(Exception):
    """Nome de classe contendo ``NoSuchTable`` para o caminho de tabela ausente."""


_MissingTableError.__name__ = "NoSuchTableError"


class _FakeCatalog:
    """Catálogo mínimo que registra em qual namespace cada tabela foi lida."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame], *, fail_read: bool = False):
        self.frames = frames
        self.fail_read = fail_read
        self.loaded: list[tuple[str, str]] = []

    def load_table(self, tid):
        self.loaded.append(tuple(tid))
        if self.fail_read:
            raise RuntimeError("metadados corrompidos")
        if tid not in self.frames:
            raise _MissingTableError(f"Table does not exist: {tid[0]}.{tid[1]}")
        return _FakeTable(self.frames[tid])


def _planning_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": ["202608", "202608", "202608"],
            "tipo": ["realizado", "forecast", "rateio"],
            "servicename": ["Virtual Machines", "Virtual Machines", "Virtual Machines"],
            "realizado": [1000.0, 0.0, 111.0],
        }
    )


def test_merge_reads_from_the_detail_layer(monkeypatch):
    """A leitura precisa acontecer na mesma camada em que a publicação escreve."""
    namespace = layers.detail_layer()
    catalog = _FakeCatalog({(namespace, "dest_modelo_focus_planning"): _planning_frame()})
    published: list[tuple[str, pd.DataFrame]] = []

    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda safe_name, df, value_column: published.append((safe_name, df)) or safe_name,
    )

    rateio = pd.DataFrame(
        {"period": ["202608"], "tipo": ["rateio"], "servicename": ["Virtual Machines"], "realizado": [222.0]}
    )
    written = ice.merge_planning_rateio("dest_modelo_focus_planning", rateio)

    assert written == 1
    assert catalog.loaded == [(namespace, "dest_modelo_focus_planning")], (
        "o merge deve ler da camada de detalhe; ler de gold reproduz o bug de perda de dado"
    )
    assert published, "a tabela combinada precisa ser publicada"


def test_merge_preserves_realizado_forecast_and_rateio(monkeypatch):
    """Gate de P1: nenhum dos três tipos pode desaparecer no merge."""
    namespace = layers.detail_layer()
    catalog = _FakeCatalog({(namespace, "dest_modelo_focus_planning"): _planning_frame()})
    published: list[pd.DataFrame] = []

    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda safe_name, df, value_column: published.append(df) or safe_name,
    )

    rateio = pd.DataFrame(
        {"period": ["202608"], "tipo": ["rateio"], "servicename": ["Virtual Machines"], "realizado": [333.0]}
    )
    ice.merge_planning_rateio("dest_modelo_focus_planning", rateio)

    combined = published[-1]
    counts = ice._type_counts(combined)
    assert counts.get("realizado") == 1, f"realizado foi perdido: {counts}"
    assert counts.get("forecast") == 1, f"forecast foi perdido: {counts}"
    assert counts.get("rateio") == 1, f"rateio incorreto: {counts}"
    # O rateio antigo é substituído, não somado.
    assert len(combined) == 3
    assert sorted(combined["realizado"].tolist()) == [0.0, 333.0, 1000.0]


def test_merge_aborts_when_it_cannot_read_existing_rows(monkeypatch):
    """Falha de leitura com linhas a preservar não pode virar publicação parcial."""
    namespace = layers.detail_layer()
    catalog = _FakeCatalog({}, fail_read=True)

    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda *_a, **_k: pytest.fail("não pode publicar quando a leitura falhou"),
    )

    rateio = pd.DataFrame({"period": ["202608"], "tipo": ["rateio"], "realizado": [1.0]})
    with pytest.raises(RuntimeError, match="recusando publicar|perder"):
        ice.merge_planning_rateio("dest_modelo_focus_planning", rateio)


def test_merge_guard_blocks_dropping_a_preserved_type(monkeypatch):
    """Guarda explícita: se um tipo preservado sumir, o merge levanta erro."""
    namespace = layers.detail_layer()
    catalog = _FakeCatalog({(namespace, "dest_modelo_focus_planning"): _planning_frame()})

    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    # Simula um merge defeituoso que descarta tudo antes de publicar.
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda *_a, **_k: pytest.fail("a guarda deve disparar antes da publicação"),
    )

    def broken_concat(*_a, **_k):
        return pd.DataFrame({"period": ["202608"], "tipo": ["rateio"], "realizado": [1.0]})

    monkeypatch.setattr(ice.pd, "concat", broken_concat)
    rateio = pd.DataFrame({"period": ["202608"], "tipo": ["rateio"], "realizado": [1.0]})
    with pytest.raises(RuntimeError, match="realizado|forecast"):
        ice.merge_planning_rateio("dest_modelo_focus_planning", rateio)


def test_merge_creates_table_when_absent(monkeypatch):
    """Tabela ausente com rateio novo é aceitável: cria do zero."""
    namespace = layers.detail_layer()
    catalog = _FakeCatalog({})
    published: list[pd.DataFrame] = []

    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda safe_name, df, value_column: published.append(df) or safe_name,
    )

    rateio = pd.DataFrame({"period": ["202608"], "tipo": ["rateio"], "realizado": [10.0]})
    assert ice.merge_planning_rateio("dest_modelo_focus_planning", rateio) == 1
    assert ice._type_counts(published[-1]) == {"rateio": 1}


def test_nothing_to_merge_returns_zero(monkeypatch):
    """Sem rateio novo e sem rateio anterior, a tabela não é republicada."""
    namespace = layers.detail_layer()
    sem_rateio = _planning_frame()
    sem_rateio = sem_rateio[sem_rateio["tipo"] != "rateio"].reset_index(drop=True)
    catalog = _FakeCatalog({(namespace, "dest_modelo_focus_planning"): sem_rateio})
    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda *_a, **_k: pytest.fail("sem rateio não deve republicar"),
    )
    assert ice.merge_planning_rateio("dest_modelo_focus_planning", pd.DataFrame()) == 0


def test_empty_rateio_clears_previous_rateio_rows(monkeypatch):
    """Rateio vazio com rateio anterior precisa remover as linhas antigas."""
    namespace = layers.detail_layer()
    catalog = _FakeCatalog({(namespace, "dest_modelo_focus_planning"): _planning_frame()})
    published: list[pd.DataFrame] = []
    monkeypatch.setattr(ice, "_get_catalog", lambda: catalog)
    monkeypatch.setattr(
        ice,
        "_publish_planning_dataframe",
        lambda safe_name, df, value_column: published.append(df) or safe_name,
    )
    assert ice.merge_planning_rateio("dest_modelo_focus_planning", pd.DataFrame()) == 0
    counts = ice._type_counts(published[-1])
    assert counts == {"realizado": 1, "forecast": 1}, counts


def test_layer_resolver_declares_silver_as_detail(monkeypatch):
    """Trava a arquitetura: o detalhe é Silver e o cubo é Gold."""
    assert layers.detail_layer() == "silver"
    assert layers.cube_layer() == "gold"
    assert layers.bronze_layer() == "bronze"
    assert layers.is_legacy("gold") is True
    assert layers.is_legacy("silver") is False
    assert layers.normalize("silver.db") == "silver"
    assert layers.normalize("GOLD") == "gold"
    with pytest.raises(ValueError):
        layers.normalize("nao_existe")
