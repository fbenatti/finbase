"""Configuração do pytest para apps/api.

Sem este arquivo, `pytest` puro insere `apps/api/tests` no `sys.path` e todo
`from app...` falha. Aqui a raiz do backend entra explicitamente, para que tanto
`python -m pytest tests/` quanto `pytest` funcionem a partir de `apps/api`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
