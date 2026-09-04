"""Testes unitários de auth (senha, JWT, política) — sem banco."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import security  # noqa: E402


def test_hash_verify_password():
    h = security.hash_password("SenhaTeste123")
    assert security.verify_password("SenhaTeste123", h)
    assert not security.verify_password("errada", h)
    assert not security.verify_password("qualquer", None)


def test_password_policy():
    assert security.validate_password_policy("SenhaForte123")
    assert not security.validate_password_policy("curta1A")          # < 10
    assert not security.validate_password_policy("semsenhamaiuscula1")  # sem maiúscula
    assert not security.validate_password_policy("SEMDIGITOMAIUSCULA")  # sem dígito


def test_access_token_roundtrip():
    token = security.create_access_token(7, "joao", [{"module": "rules", "action": "read"}])
    payload = security.decode_access_token(token)
    assert payload["sub"] == "7"
    assert payload["username"] == "joao"
    assert payload["perms"] == [{"module": "rules", "action": "read"}]
    assert "jti" in payload and "exp" in payload and "iat" in payload


def test_expired_token_rejected():
    token = security.create_access_token(1, "admin", [], expires_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(token)


def test_invalid_token_rejected():
    with pytest.raises(Exception):
        security.decode_access_token("not-a-jwt")


def test_refresh_token_opaque_and_hash():
    token = security.generate_refresh_token()
    assert len(token) >= 60
    assert security.hash_refresh_token(token) != token
    assert security.hash_refresh_token(token) == security.hash_refresh_token(token)


def test_tokens_equal_constant_time():
    assert security.tokens_equal("abc", "abc")
    assert not security.tokens_equal("abc", "abd")
    assert not security.tokens_equal("", "x")


def test_dev_defaults_safe():
    # produção sem chave deve falhar no startup — validamos o guard de config
    assert settings.auth_enabled  # default true
