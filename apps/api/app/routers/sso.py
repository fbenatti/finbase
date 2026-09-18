"""Fluxos SSO: OIDC e SAML2. Provisionamento JIT e grupos compartilhados."""
import base64
import logging
import re
import secrets
import urllib.parse
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.sso_crypto import decrypt_config
from app.core.sso_provisioning import jit_provision_user, touch_login
from app.database import get_db
from app.models.identity import SsoProvider, User
from app.routers.auth import _issue_tokens, REFRESH_COOKIE_NAME, _set_refresh_cookie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/sso", tags=["sso"])

_STATE_COOKIE = "sso_state"
_NONCE_COOKIE = "sso_nonce"
_FRONTEND_REDIRECT = "/"
_LOGIN_IDENTIFIER_RE = re.compile(r"^[^@\s]+(?:@[^@\s]+)?$")


async def _get_provider(db: AsyncSession, name: str, enabled_only: bool = True) -> SsoProvider:
    result = await db.execute(select(SsoProvider).where(SsoProvider.name == name))
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provedor SSO não encontrado")
    if enabled_only and not provider.enabled:
        raise HTTPException(status_code=404, detail="Provedor SSO não encontrado")
    return provider


async def _finish_login(
    db: AsyncSession, request: Request,
    *, provider: SsoProvider, external_id: str, email: str | None,
    full_name: str | None, group_names: list[str],
) -> RedirectResponse:
    cfg = decrypt_config(provider.config_json)
    user, created = await jit_provision_user(
        db, auth_provider=provider.protocol, external_id=external_id,
        email=email, full_name=full_name, group_names=group_names,
        provider_config=cfg,
    )
    await touch_login(db, user)
    await db.commit()
    # Emite access + refresh; o refresh cookie é gravado no próprio redirect
    redirect = RedirectResponse(url=_FRONTEND_REDIRECT, status_code=303)
    await _issue_tokens(redirect, db, user, request)
    return redirect


# ── Lista pública de provedores habilitados (para a tela de login) ───────────

@router.get("/providers")
async def public_providers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SsoProvider).where(SsoProvider.enabled == True).order_by(SsoProvider.name))  # noqa: E712
    providers = []
    for p in result.scalars().all():
        cfg = decrypt_config(p.config_json)
        providers.append({
            "name": p.name,
            "protocol": p.protocol,
            # Exposto apenas como dica visual; não é segredo. O backend é a
            # fonte de verdade e sempre normaliza o login_hint.
            "username_domain": cfg.get("username_domain", "") if p.protocol == "oidc" else "",
        })
    return providers


# ── OIDC ──────────────────────────────────────────────────────────────────────

@router.get("/{provider}/login")
async def sso_login(
    provider: str,
    request: Request,
    login_hint: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    prov = await _get_provider(db, provider)
    if prov.protocol == "oidc":
        return await _oidc_authorize(prov, request, login_hint=login_hint)
    if prov.protocol == "saml":
        return await _saml_login(prov, request)
    raise HTTPException(status_code=422, detail="Protocolo desconhecido")


def _set_cookie(response: Response, key: str, value: str) -> None:
    response.set_cookie(key=key, value=value, max_age=600, httponly=True,
                        samesite="lax", path="/", secure=settings.sso_cookie_secure or settings.env == "production")


def _oidc_cookie_key(prefix: str, prov: SsoProvider) -> str:
    """Cookie names cannot contain spaces or ':' from a display provider name."""
    return f"{prefix}_{prov.id}"


def _normalize_login_hint(value: str | None, cfg: dict) -> str | None:
    """Use only the local part entered by the user plus the configured domain.

    This deliberately discards any supplied domain. It offers a simple branded
    login screen without trusting client-side domain selection.
    """
    if not value:
        return None
    identifier = value.strip().lower()
    if not _LOGIN_IDENTIFIER_RE.fullmatch(identifier):
        raise HTTPException(status_code=422, detail="Informe um usuário ou e-mail válido")
    domain = str(cfg.get("username_domain") or "").strip().lower().lstrip("@")
    if not domain:
        return identifier
    if "@" in domain or not re.fullmatch(r"[a-z0-9.-]+", domain):
        logger.error("Provedor OIDC %s possui username_domain inválido", cfg.get("client_id", "?"))
        raise HTTPException(status_code=500, detail="Domínio corporativo OIDC inválido")
    local_part = identifier.split("@", 1)[0]
    return f"{local_part}@{domain}"


async def _oidc_authorize(prov: SsoProvider, request: Request, login_hint: str | None = None):
    cfg = decrypt_config(prov.config_json)
    from authlib.integrations.httpx_client import AsyncOAuth2Client

    discovery, metadata = await _oidc_metadata(cfg)
    authorization_endpoint = metadata["authorization_endpoint"]
    client = AsyncOAuth2Client(
        client_id=cfg.get("client_id"),
        client_secret=cfg.get("client_secret"),
        redirect_uri=cfg.get("redirect_uri") or settings.sso_redirect_uri or _base_url(request) + f"/auth/sso/{prov.name}/callback",
        scope=cfg.get("scopes") or "openid profile email",
    )
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    hint = _normalize_login_hint(login_hint, cfg)
    authorization_kwargs = {"state": state, "nonce": nonce}
    if hint:
        authorization_kwargs["login_hint"] = hint
    uri, _ = client.create_authorization_url(authorization_endpoint, **authorization_kwargs)
    response = RedirectResponse(url=uri, status_code=303)
    _set_cookie(response, _oidc_cookie_key(_STATE_COOKIE, prov), state)
    _set_cookie(response, _oidc_cookie_key(_NONCE_COOKIE, prov), nonce)
    return response


async def _oidc_metadata(cfg: dict) -> tuple[str, dict]:
    """Load the provider discovery document and validate required endpoints.

    Authlib receives actual authorization/token endpoints, never the discovery
    document URL itself. Keeping the discovery URL also makes the callback and
    signature validation use the same issuer metadata.
    """
    import httpx

    issuer = str(cfg.get("issuer") or "").rstrip("/")
    discovery = str(cfg.get("discovery_url") or (issuer + "/.well-known/openid-configuration"))
    if not discovery.startswith("https://"):
        raise HTTPException(status_code=422, detail="Discovery OIDC deve usar HTTPS")
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.get(discovery)
            response.raise_for_status()
            metadata = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao carregar discovery OIDC %s: %s", discovery, exc)
        raise HTTPException(status_code=502, detail="Não foi possível carregar a configuração OIDC") from exc
    if not metadata.get("authorization_endpoint") or not metadata.get("token_endpoint"):
        raise HTTPException(status_code=422, detail="Discovery OIDC sem endpoints obrigatórios")
    return discovery, metadata


def _base_url(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-proto")
    proto = forwarded or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@router.get("/{provider}/callback")
async def oidc_callback(provider: str, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    prov = await _get_provider(db, provider)
    if prov.protocol != "oidc":
        raise HTTPException(status_code=422, detail="Callback inválido para este provedor")
    cfg = decrypt_config(prov.config_json)
    state_cookie = request.cookies.get(_oidc_cookie_key(_STATE_COOKIE, prov))
    expected_state = request.query_params.get("state")
    if not state_cookie or not expected_state or not secrets.compare_digest(state_cookie, expected_state):
        raise HTTPException(status_code=401, detail="State inválido")

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Código de autorização ausente")

    from authlib.integrations.httpx_client import AsyncOAuth2Client
    discovery, metadata = await _oidc_metadata(cfg)
    client = AsyncOAuth2Client(
        client_id=cfg.get("client_id"),
        client_secret=cfg.get("client_secret"),
        redirect_uri=cfg.get("redirect_uri") or settings.sso_redirect_uri or _base_url(request) + f"/auth/sso/{prov.name}/callback",
    )
    token = await client.fetch_token(metadata["token_endpoint"], code=code)

    id_token = token.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="id_token ausente na resposta")
    claims = await _verify_id_token(prov, cfg, id_token, discovery)

    nonce_cookie = request.cookies.get(_oidc_cookie_key(_NONCE_COOKIE, prov))
    if not nonce_cookie or not claims.get("nonce") or not secrets.compare_digest(nonce_cookie, claims.get("nonce", "")):
        raise HTTPException(status_code=401, detail="Nonce inválido")

    external_id = str(claims.get("sub") or claims.get("oid") or "")
    if not external_id:
        raise HTTPException(status_code=400, detail="Claims sem sub")
    groups = claims.get(cfg.get("groups_claim") or "groups") or []

    return await _finish_login(
        db, request, provider=prov,
        external_id=external_id,
        # Entra ID normalmente emite ``preferred_username`` no ID token;
        # ``email`` é opcional e pode não existir mesmo com o scope email.
        # Usá-lo evita criar uma conta JIT pelo ``sub`` opaco para uma pessoa
        # que já possui conta SAML/local com o mesmo UPN.
        email=claims.get("email") or claims.get("preferred_username") or claims.get("upn"),
        full_name=claims.get("name") or claims.get("displayName"),
        group_names=groups if isinstance(groups, list) else [groups],
    )


async def _verify_id_token(prov: SsoProvider, cfg: dict, id_token: str, discovery_url: str) -> dict:
    import httpx
    from authlib.jose import JsonWebKey, JsonWebToken

    issuer = cfg.get("issuer") or ""
    client_id = cfg.get("client_id") or ""
    async with httpx.AsyncClient() as http:
        if discovery_url:
            meta = (await http.get(discovery_url)).json()
            jwks_uri = meta.get("jwks_uri")
            issuer = issuer or meta.get("issuer") or ""
        else:
            jwks_uri = None
        if not jwks_uri:
            raise HTTPException(status_code=400, detail="Sem jwks_uri no discovery")
        jwks = (await http.get(jwks_uri)).json()

    keyset = JsonWebKey.import_key_set(jwks)
    jwt = JsonWebToken(["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"])
    try:
        claims = jwt.decode(id_token, keyset)
        claims.validate()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"id_token inválido: {exc}")
    now = datetime.now(timezone.utc).timestamp()
    if "exp" in claims and claims["exp"] < now:
        raise HTTPException(status_code=401, detail="id_token expirado")
    if issuer and claims.get("iss") != issuer:
        raise HTTPException(status_code=401, detail="iss inesperado")
    if claims.get("aud") != client_id and client_id not in (claims.get("aud") or []):
        raise HTTPException(status_code=401, detail="aud inesperado")
    return claims


# ── SAML2 ────────────────────────────────────────────────────────────────────

def _saml_toolkit(cfg: dict):
    """Instancia OneLogin_Saml2_Settings a partir da config do provider."""
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
        from onelogin.saml2.settings import OneLogin_Saml2_Settings
        return OneLogin_Saml2_Auth, OneLogin_Saml2_Settings
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=501,
            detail="SAML requer xmlsec1 + python3-saml instalados no servidor: "
                   f"{exc}",
        )


def _saml_request(request: Request, prov: SsoProvider, post_data: dict | None = None) -> dict:
    """Converte o request FastAPI no formato esperado pelo python3-saml."""
    http_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    https = (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
    script_name = request.url.path
    get_data = dict(request.query_params)
    return {
        "https": https,
        "http_host": http_host,
        "script_name": script_name,
        "get_data": get_data,
        "post_data": post_data or {},
    }


@router.get("/{provider}/login-saml")
async def saml_redirect(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    prov = await _get_provider(db, provider)
    return await _saml_login(prov, request)


async def _saml_login(prov: SsoProvider, request: Request):
    cfg = decrypt_config(prov.config_json)
    if not cfg.get("idp_sso_url") or not cfg.get("idp_entity_id"):
        raise HTTPException(status_code=422, detail="Configuração SAML incompleta (idp_sso_url, idp_entity_id)")
    # AuthnRequest via redirect binding (deflate+base64), unsigned por simplicidade;
    # assinatura deve ser habilitada em produção.
    import zlib
    request_id = "_" + uuid.uuid4().hex
    sp_entity = cfg.get("sp_entity_id") or _base_url(request) + "/auth/sso/metadata"
    acs = cfg.get("acs_url") or _base_url(request) + f"/auth/sso/{prov.name}/acs"
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{request_id}" Version="2.0" IssueInstant="{datetime.now(timezone.utc).isoformat()}" '
        f'Destination="{cfg.get("idp_sso_url")}" AssertionConsumerServiceURL="{acs}" '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f'<saml:Issuer>{sp_entity}</saml:Issuer>'
        f'<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/>'
        f'</samlp:AuthnRequest>'
    )
    compressed = base64.b64encode(zlib.compress(xml.encode("utf-8"))[2:-4]).decode("ascii")
    url = cfg["idp_sso_url"] + ("&" if "?" in cfg["idp_sso_url"] else "?") + "SAMLRequest=" + urllib.parse.quote(compressed)
    response = RedirectResponse(url=url, status_code=303)
    # guarda request_id esperado
    # Nome exibido do provedor pode conter espaços/caracteres que não são
    # permitidos em nomes de cookie. O id persistido é estável e seguro.
    response.set_cookie(key=f"sso_saml_{prov.id}", value=request_id, max_age=600,
                        httponly=True, samesite="lax", path="/", secure=settings.sso_cookie_secure or settings.env == "production")
    return response


@router.post("/{provider}/acs")
async def saml_acs(provider: str, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    prov = await _get_provider(db, provider)
    if prov.protocol != "saml":
        raise HTTPException(status_code=422, detail="ACS inválido para este provedor")
    cfg = decrypt_config(prov.config_json)
    form = await request.form()
    saml_response = form.get("SAMLResponse")
    if not saml_response:
        raise HTTPException(status_code=400, detail="SAMLResponse ausente")

    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
        from onelogin.saml2.response import OneLogin_Saml2_Response
        settings_cls_import = None
        # Usa OneLogin_Saml2_Auth com settings criadas a partir da config
        from onelogin.saml2.settings import OneLogin_Saml2_Settings
        settings_dict = _build_saml_settings(prov, cfg, request)
        # python3-saml validates the assertion from the request dictionary.
        # The endpoint had already read the form but previously passed an
        # empty post_data mapping, causing every SAML ACS response to fail.
        req = _saml_request(request, prov, {
            "SAMLResponse": str(saml_response),
            **({"RelayState": str(form["RelayState"])} if form.get("RelayState") else {}),
        })
        auth = OneLogin_Saml2_Auth(req, settings_dict)
        auth.process_response()
        if auth.get_errors():
            raise HTTPException(status_code=401, detail=f"Falha SAML: {auth.get_last_error_reason() or auth.get_errors()}")
        if not auth.is_authenticated():
            raise HTTPException(status_code=401, detail="Não autenticado pelo IdP")
        attrs = auth.get_attributes()
        groups_attr = cfg.get("groups_attribute") or "groups"
        groups = attrs.get(groups_attr) or []
        name_id = auth.get_nameid() or ""
        external_id = name_id
        email = attrs.get("email", [name_id])[0] if isinstance(attrs.get("email", [name_id]), list) else name_id
        full_name = None
        for key in ("displayname", "givenname", "cn"):
            if attrs.get(key):
                full_name = attrs[key][0] if isinstance(attrs[key], list) else str(attrs[key])
                break
        return await _finish_login(db, request, provider=prov,
                                   external_id=external_id, email=str(email),
                                   full_name=str(full_name) if full_name else None,
                                   group_names=groups if isinstance(groups, list) else [groups])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha no ACS SAML")
        raise HTTPException(status_code=500, detail=f"Falha no processamento SAML: {exc}")


def _build_saml_settings(prov: SsoProvider, cfg: dict, request: Request) -> dict:
    base = _base_url(request)
    sp_entity = cfg.get("sp_entity_id") or base + "/auth/sso/metadata"
    return {
        "strict": False,
        "debug": True,
        "sp": {
            "entityId": sp_entity,
            "assertionConsumerService": {
                "url": cfg.get("acs_url") or base + f"/auth/sso/{prov.name}/acs",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "x509cert": cfg.get("sp_x509_cert") or "",
            "privateKey": cfg.get("sp_x509_private_key") or "",
        },
        "idp": {
            "entityId": cfg.get("idp_entity_id", ""),
            "singleSignOnService": {"url": cfg.get("idp_sso_url", ""), "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"},
            "singleLogoutService": {"url": cfg.get("idp_slo_url") or "", "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"},
            "x509cert": cfg.get("x509_cert") or "",
        },
    }


@router.get("/metadata")
async def sp_metadata(request: Request, db: AsyncSession = Depends(get_db)):
    """Metadata do SP — pode exigir um provider; usa o primeiro SAML habilitado."""
    result = await db.execute(
        select(SsoProvider).where(SsoProvider.enabled == True, SsoProvider.protocol == "saml").limit(1)  # noqa: E712
    )
    prov = result.scalar_one_or_none()
    if prov is None:
        raise HTTPException(status_code=404, detail="Nenhum provedor SAML habilitado")
    cfg = decrypt_config(prov.config_json)
    base = _base_url(request)
    sp_entity = cfg.get("sp_entity_id") or base + "/auth/sso/metadata"
    acs = cfg.get("acs_url") or base + f"/auth/sso/{prov.name}/acs"
    xml = f'''<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{sp_entity}">
  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{acs}" index="0" isDefault="true"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>'''
    return Response(content=xml, media_type="application/xml")
