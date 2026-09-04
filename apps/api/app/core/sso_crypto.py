"""Criptografia AES-GCM dos segredos de SSO usando chave derivada do JWT_SECRET_KEY.

client_secret nunca é persistido em claro: a API recebe o valor em claro apenas no
momento do create/update, cifra e guarda sob a chave 'client_secret_enc'. GET
nunca devolve o segredo (nem cifrado).
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

from app.config import settings

_SECRET_FIELDS = {"client_secret", "x509_cert_private_key"}
_ENC_SUFFIX = "_enc"


def _key() -> bytes:
    return hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()


def _encrypt(value: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, value.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt(value: str) -> str:
    raw = base64.b64decode(value)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_key()).decrypt(nonce, ct, None).decode("utf-8")


def protect_config(config: dict) -> dict:
    """Cifra campos secretos em claro → *_enc. Retorna cópia sem segredos em claro."""
    out = dict(config)
    for field in _SECRET_FIELDS:
        plain = out.get(field)
        if plain:
            out[field + _ENC_SUFFIX] = _encrypt(str(plain))
        out.pop(field, None)
    return out


def decrypt_config(config: dict | None) -> dict:
    """Config com segredos decifrados (para uso interno nos fluxos SSO)."""
    out = dict(config or {})
    for field in _SECRET_FIELDS:
        enc = out.pop(field + _ENC_SUFFIX, None)
        if enc:
            out[field] = _decrypt(enc)
    return out


def sanitize_config(config: dict | None) -> dict:
    """Config segura para resposta da API: sem segredos em claro nem cifrados."""
    out = dict(config or {})
    for key in list(out):
        if key.endswith(_ENC_SUFFIX) or key in _SECRET_FIELDS:
            out.pop(key, None)
    if any(k in (config or {}) for k in _SECRET_FIELDS) or any(k.endswith(_ENC_SUFFIX) for k in (config or {})):
        out["secrets_configured"] = True
    return out
