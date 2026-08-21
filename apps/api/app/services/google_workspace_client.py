"""Google Workspace / Admin SDK / Reports / License Manager client."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest
except ImportError:  # pragma: no cover
    service_account = None  # type: ignore
    GoogleAuthRequest = None  # type: ignore


ADMIN_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
LICENSE_SCOPE = "https://www.googleapis.com/auth/apps.licensing"
REPORTS_SCOPE = "https://www.googleapis.com/auth/admin.reports.usage.readonly"
BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"


class GoogleWorkspaceClient:
    def __init__(
        self,
        *,
        service_account_json: dict | str,
        admin_email: str,
        customer_id: str = "my_customer",
        domain: str = "",
    ):
        if not service_account_json:
            raise ValueError("Google service account JSON is required")
        if not admin_email:
            raise ValueError("Google admin_email for domain-wide delegation is required")
        if service_account is None:
            raise RuntimeError("google-auth library is required for Google Workspace extractors")
        if isinstance(service_account_json, str):
            info = json.loads(service_account_json)
        else:
            info = service_account_json
        self.admin_email = admin_email
        self.customer_id = customer_id
        self.domain = domain
        self._creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=[ADMIN_SCOPE, LICENSE_SCOPE, REPORTS_SCOPE, BIGQUERY_SCOPE],
        ).with_subject(admin_email)

    def _token(self) -> str:
        creds = self._creds
        if not creds.valid:
            creds.refresh(GoogleAuthRequest())
        return creds.token  # type: ignore[return-value]

    def _get(self, url: str, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}"}
        with httpx.Client(timeout=120) as client:
            resp = client.get(url, headers=headers, params=params or {})
            if resp.status_code >= 400:
                raise RuntimeError(f"Google API failed ({resp.status_code}): {resp.text[:500]}")
            return resp.json()

    def test(self) -> None:
        self._get(
            "https://admin.googleapis.com/admin/directory/v1/users",
            {"customer": self.customer_id, "maxResults": 1},
        )

    def iter_users(self) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"customer": self.customer_id, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            data = self._get("https://admin.googleapis.com/admin/directory/v1/users", params)
            users = data.get("users") or []
            for user in users:
                yield user
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    def iter_license_assignments(self, product_id: str, sku_id: str) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"customerId": self.customer_id, "maxResults": 1000}
            if page_token:
                params["pageToken"] = page_token
            url = (
                "https://licensing.googleapis.com/apps/licensing/v1/product"
                f"/{product_id}/sku/{sku_id}/users"
            )
            data = self._get(url, params)
            items = data.get("items") or []
            for item in items:
                yield item
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    def iter_user_usage_report(self, date: str, parameters: str = "gemini_in_workspace_apps") -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "userKey": "all",
                "date": date,
                "parameters": parameters,
                "maxResults": 1000,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get(
                "https://admin.googleapis.com/admin/reports/v1/usage/users/all/dates/" + date,
                params,
            )
            for usage in data.get("usageReports") or []:
                yield usage
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    def query_bigquery(self, project_id: str, sql: str) -> list[dict[str, Any]]:
        token = self._token()
        url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}/queries"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"query": sql, "useLegacySql": False, "timeoutMs": 120000}
        with httpx.Client(timeout=180) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"BigQuery query failed ({resp.status_code}): {resp.text[:500]}")
            data = resp.json()
            schema = [f["name"] for f in (data.get("schema") or {}).get("fields") or []]
            rows: list[dict[str, Any]] = []
            for row in data.get("rows") or []:
                values = [cell.get("v") for cell in row.get("f") or []]
                rows.append(dict(zip(schema, values)))
            return rows


def usage_dates_for_period(period: str) -> list[str]:
    year = int(period[:4])
    month = int(period[4:6])
    from calendar import monthrange

    last = monthrange(year, month)[1]
    return [f"{year}-{month:02d}-{day:02d}" for day in range(1, last + 1)]
