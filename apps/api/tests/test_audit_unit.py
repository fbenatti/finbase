"""Testes unitários de auditoria: sanitização e diff pré-calculado."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.audit_sanitize import (  # noqa: E402
    sanitize_dict, compute_diff, snapshot_from_dict,
)


def test_sanitize_removes_secrets_recursively():
    data = {
        "name": "conn",
        "client_secret": "s3cr3t",
        "config": {"password": "p", "nested": [{"token": "t"}]},
    }
    out = sanitize_dict(data)
    assert out["client_secret"] == "***"
    assert out["config"]["password"] == "***"
    assert out["config"]["nested"][0]["token"] == "***"
    assert out["name"] == "conn"


def test_sanitize_truncates_long_strings():
    out = sanitize_dict({"big": "x" * 20_000})
    assert len(out["big"]) < 10_100
    assert out["big"].endswith("...[truncated]")


def test_compute_diff_two_fields():
    before = {"a": 1, "b": 2, "updated_at": "x"}
    after = {"a": 1, "b": 3, "c": 4, "updated_at": "y"}
    diff = compute_diff(before, after)
    assert diff == [
        {"field": "b", "old": 2, "new": 3},
        {"field": "c", "old": None, "new": 4},
    ]
    assert "updated_at" not in [d["field"] for d in diff]


def test_compute_diff_none_both():
    assert compute_diff(None, None) is None
    assert compute_diff({}, {}) is None


def test_snapshot_from_dict_dates_iso_and_sanitized():
    from datetime import datetime
    snap = snapshot_from_dict({"created_at": datetime(2026, 1, 2, 3, 4, 5), "refresh_token": "rt"})
    assert snap["created_at"].startswith("2026-01-02T03:04:05")
    assert snap["refresh_token"] == "***"


def test_sanitize_config_matches_connections_pattern():
    from app.core.audit_sanitize import SECRET_KEYS
    # O conjunto cobre o padrão de routers/connections.py
    assert "client_secret" in SECRET_KEYS
    assert "private_key" in SECRET_KEYS
    assert "password_hash" in SECRET_KEYS
    assert "access_token" in SECRET_KEYS


def test_audit_module_fallback_covers_unmapped_prefixes():
    from app.core.audit_middleware import audit_module_for
    # Prefixos que antes não estavam em PATH_MODULE e ficavam sem auditoria
    assert audit_module_for("/governanca/contratos-financeiros/12") == "contratos-financeiros"
    assert audit_module_for("/administracao/identidade/provedores/3/testar") == "sso"
    assert audit_module_for("/operacoes/logs-processamento/cubo/reconstruir") == "processing-logs"
    assert audit_module_for("/dados/fontes-azure/1/fontes/x/selecao") == "fontes-azure"
    assert audit_module_for("/custos/compromisso-mca/credenciais") == "reservations"
    assert audit_module_for("/autenticacao/alterar-senha") == "auth"


def test_audit_module_mapped_prefixes_keep_module():
    from app.core.audit_middleware import audit_module_for
    assert audit_module_for("/operacoes/periodos/configuracao/1/2026-01") == "periods"
    assert audit_module_for("/dados/integracoes/x") == "connections"


def test_audit_module_excludes_audit_endpoints():
    from app.core.audit_middleware import audit_module_for
    assert audit_module_for("/administracao/auditoria") is None
    assert audit_module_for("/administracao/auditoria/registros") is None


def test_every_mutating_route_has_audit_module():
    """Cobertura: toda rota POST/PUT/PATCH/DELETE da API precisa de módulo de auditoria."""
    from app.core.audit_middleware import audit_module_for
    from app.main import app

    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    missing = []
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        if not methods & mutating:
            continue
        if audit_module_for(route.path) is None:
            missing.append(f"{sorted(methods & mutating)} {route.path}")
    assert missing == []


def test_model_lookup_by_parent_segment():
    from app.core.audit_middleware import _model_for
    from app.models.destination import SourceDestinationMapping
    from app.models.forecast import ForecastConfig
    from app.models.identity import User
    from app.models.rules import EtlMapping
    # Rota aninhada usa "primeiro/pai"; mesmo nome de pai em módulos diferentes não colide
    assert _model_for(["projecoes", "configuracoes", "5"]) is ForecastConfig
    assert _model_for(["dados", "catalogo", "vinculos", "3"]) is SourceDestinationMapping
    assert _model_for(["dados", "mapeamentos", "4"]) is EtlMapping
    assert _model_for(["administracao", "usuarios", "2"]) is User
    assert _model_for(["unknown", "thing", "9"]) is None
