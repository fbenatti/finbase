"""C1: toda rota com prefixo de router precisa de módulo RBAC ou de exceção explícita.

Como o middleware fecha por padrão, um router sem mapeamento ficaria inacessível.
Este teste acusa o router novo antes que ele chegue ao ar.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.authz_middleware import (  # noqa: E402
    AUTHZ_EXTRA_MODULES, PATH_MODULE, PUBLIC_PATHS, SKIP_PREFIXES, _authz_module_for,
)

ROUTERS_DIR = ROOT / "app" / "routers"
PREFIX_RE = re.compile(r'APIRouter\(\s*prefix="([^"]+)"', re.S)


def _router_prefixes() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in ROUTERS_DIR.glob("*.py"):
        for prefix in PREFIX_RE.findall(path.read_text(encoding="utf-8")):
            found[prefix] = path.name
    return found


def _covered(prefix: str) -> bool:
    if _authz_module_for(prefix) is not None:
        return True
    return prefix.startswith(SKIP_PREFIXES + PUBLIC_PATHS)


def test_todo_router_com_prefixo_tem_modulo_rbac():
    missing = {p: f for p, f in _router_prefixes().items() if not _covered(p)}
    assert not missing, f"routers sem módulo RBAC (fechados por padrão): {missing}"


def test_rota_desconhecida_nao_tem_modulo():
    assert _authz_module_for("/rota-que-nao-existe") is None


def test_prefixos_apontam_para_modulos_validos():
    from app.core.modules import ALL_MODULES
    invalid = {(p, m) for p, m in PATH_MODULE + AUTHZ_EXTRA_MODULES if m not in ALL_MODULES}
    assert not invalid, f"módulos inexistentes em PATH_MODULE: {invalid}"


def test_audit_continua_nomeando_por_primeiro_segmento():
    from app.core.audit_middleware import audit_module_for
    assert audit_module_for("/financial-contracts/12") == "financial-contracts"
    assert _authz_module_for("/financial-contracts/12") == "rules"
