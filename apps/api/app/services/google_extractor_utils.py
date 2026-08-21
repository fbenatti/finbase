"""Build Google clients from connection config or existing GCP FinOps sources."""

from __future__ import annotations

import json
from typing import Any, Iterator

from app.services.google_workspace_client import GoogleWorkspaceClient


def _parse_service_account(value) -> dict | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value if value.get("type") == "service_account" or "private_key" in value else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("AIza"):
        return None
    if text.startswith("{"):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    return None


def service_account_from_config(config_json: dict) -> dict | None:
    for key in (
        "service_account_json",
        "credentials_json",
        "token_json",
        "api_key",
        "client_secret",
    ):
        parsed = _parse_service_account(config_json.get(key))
        if parsed:
            return parsed
    if config_json.get("service_account_path"):
        with open(config_json["service_account_path"], encoding="utf-8") as fh:
            return json.load(fh)
    return None


def iter_gcp_billing_service_accounts() -> Iterator[tuple[str, dict[str, Any], dict]]:
    """Yield (code, config, sa_info) from existing gcp_bigquery connections."""
    from sqlalchemy import select

    from app.database import SyncSession
    from app.models.source_connection import SourceConnection

    session = SyncSession()
    try:
        rows = session.execute(
            select(SourceConnection).where(SourceConnection.connector_type == "gcp_bigquery")
        ).scalars().all()
        for conn in rows:
            cfg = conn.config_json or {}
            info = _parse_service_account(cfg.get("token_json") or cfg.get("service_account_json"))
            if info:
                yield conn.code, cfg, info
    finally:
        session.close()


def google_client_from_config(config_json: dict) -> GoogleWorkspaceClient:
    sa_json = service_account_from_config(config_json)
    admin_email = config_json.get("admin_email") or config_json.get("delegated_admin_email") or ""
    if sa_json and admin_email:
        return GoogleWorkspaceClient(
            service_account_json=sa_json,
            admin_email=admin_email,
            customer_id=config_json.get("customer_id", "my_customer"),
            domain=config_json.get("domain", ""),
        )
    raise ValueError(
        "Workspace Admin SDK exige JSON de service account e admin_email "
        "(domain-wide delegation)."
    )


def bigquery_client(sa_info: dict, project_id: str):
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(sa_info)
    return bigquery.Client(project=project_id, credentials=creds)


def is_human_user_email(value: str) -> bool:
    """True for a person mailbox; false for SKUs, project ids, SAs and workers."""
    email = (value or "").strip().lower()
    if "@" not in email or email.startswith("@"):
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if domain.endswith("gserviceaccount.com") or domain.endswith("iam.gserviceaccount.com"):
        return False
    worker_markers = ("worker", "svc-", "service-account", "sa-", "robot-", "noreply")
    if any(marker in local for marker in worker_markers):
        return False
    return True


def list_project_iam_users(sa_info: dict, project_id: str) -> list[str]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    import httpx

    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"],
    )
    creds.refresh(Request())
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:getIamPolicy"
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
            json={"options": {"requestedPolicyVersion": 3}},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"IAM policy {project_id} failed ({resp.status_code}): {resp.text[:300]}")
        emails = []
        for binding in resp.json().get("bindings") or []:
            for member in binding.get("members") or []:
                if member.startswith("deleted:"):
                    member = member.split(":", 1)[1]
                if member.startswith("user:"):
                    identity = member.split(":", 1)[1]
                    if is_human_user_email(identity):
                        emails.append(identity)
        return sorted(set(emails))


def _oauth_bearer_for_gcp(sa_info: dict, quota_project: str = "") -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    if quota_project:
        creds = creds.with_quota_project(quota_project)
    creds.refresh(Request())
    return creds.token


def _adc_bearer(quota_project: str = "") -> str | None:
    try:
        import google.auth
        from google.auth.transport.requests import Request

        creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if quota_project and hasattr(creds, "with_quota_project"):
            creds = creds.with_quota_project(quota_project)
        creds.refresh(Request())
        return creds.token
    except Exception:
        return None


def iter_gcp_oauth_tokens(quota_project: str = "") -> Iterator[tuple[str, str]]:
    """Yield credentials that can belong to the NotebookLM project.

    Billing service accounts from unrelated projects must not be attempted:
    their 403 responses obscure the actual NotebookLM authentication error.
    """
    from sqlalchemy import select

    from app.database import SyncSession
    from app.models.source_connection import SourceConnection

    session = SyncSession()
    try:
        types = ("notebooklm_license", "notebooklm_token")
        rows = session.execute(select(SourceConnection).where(SourceConnection.connector_type.in_(types))).scalars().all()
        seen: set[str] = set()
        for conn in rows:
            cfg = conn.config_json or {}
            raw_token = (cfg.get("access_token") or "").strip()
            if raw_token and not raw_token.startswith("AIza"):
                yield f"{conn.code}-access-token", raw_token
            info = service_account_from_config(cfg)
            if not info:
                continue
            email = str(info.get("client_email") or conn.code)
            if email in seen:
                continue
            seen.add(email)
            try:
                token = _oauth_bearer_for_gcp(info, quota_project or cfg.get("project_id") or "")
                yield conn.code, token
            except Exception:
                continue
    finally:
        session.close()
    adc = _adc_bearer(quota_project)
    if adc:
        yield "application-default-credentials", adc


def list_discovery_user_licenses(project_id: str, project_number: str = "") -> list[dict[str, Any]]:
    """NotebookLM/Gemini Enterprise seats via Discovery Engine UserLicense (createTime, lastLoginTime)."""
    import httpx

    projects = [p for p in (project_number, project_id) if p]
    locations = ("global", "us", "eu")
    stores = ("default_user_store", "default")
    versions = ("v1", "v1beta")
    last_error = "nenhuma credencial OAuth válida encontrada"
    for source, token in iter_gcp_oauth_tokens(project_id):
        headers = {"Authorization": f"Bearer {token}"}
        if project_id:
            headers["x-goog-user-project"] = project_id
        with httpx.Client(timeout=60) as client:
            for proj in projects:
                for location in locations:
                    for store in stores:
                        for version in versions:
                            parent = f"projects/{proj}/locations/{location}/userStores/{store}"
                            url = f"https://discoveryengine.googleapis.com/{version}/{parent}/userLicenses"
                            licenses: list[dict[str, Any]] = []
                            page_token = ""
                            try:
                                while True:
                                    params: dict[str, Any] = {"pageSize": 1000}
                                    if page_token:
                                        params["pageToken"] = page_token
                                    resp = client.get(url, headers=headers, params=params)
                                    if resp.status_code >= 400:
                                        last_error = f"{source} {parent} ({resp.status_code}): {resp.text[:240]}"
                                        break
                                    data = resp.json()
                                    batch = data.get("userLicenses") or []
                                    licenses.extend(batch)
                                    page_token = data.get("nextPageToken") or ""
                                    if not page_token:
                                        people = [
                                            row for row in licenses
                                            if is_human_user_email(str(row.get("userPrincipal") or ""))
                                        ]
                                        if people:
                                            return people
                                        last_error = f"{source} {parent} retornou 0 usuários"
                                        break
                                if licenses:
                                    return licenses
                            except Exception as exc:  # noqa: BLE001
                                last_error = str(exc)
    raise RuntimeError(
        "Discovery Engine userLicenses (API correta do NotebookLM Enterprise) falhou. "
        "A chave AIza não autentica essa API. Configure o ADC local com a conta "
        "fernando.benatti@telefonica.com.br; credenciais de billing não são usadas. "
        f"Último erro: {last_error}"
    )


def query_billing_export(sql: str) -> list[dict[str, Any]]:
    last_error = None
    for _code, cfg, info in iter_gcp_billing_service_accounts():
        project_id = cfg.get("project_id") or ""
        dataset = cfg.get("dataset_id") or ""
        table = cfg.get("table_name") or ""
        if not (project_id and dataset and table):
            continue
        fqn = f"`{project_id}.{dataset}.{table}`"
        rendered = sql.replace("{BILLING_TABLE}", fqn)
        try:
            client = bigquery_client(info, project_id)
            return [dict(row) for row in client.query(rendered).result()]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error:
        raise RuntimeError(f"Nenhuma conexão GCP BigQuery conseguiu consultar billing: {last_error}")
    raise RuntimeError("Nenhuma conexão gcp_bigquery com token_json encontrada")