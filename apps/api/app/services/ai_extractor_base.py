"""Shared helpers for AI provider extractors."""

from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import httpx


ANTHROPIC_BASE = "https://api.anthropic.com"
GITHUB_API = "https://api.github.com"
ANTHROPIC_VERSION = "2023-06-01"


def current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def period_range(period: str) -> tuple[str, str]:
    """Return ISO start/end dates for a YYYYMM period."""
    year = int(period[:4])
    month = int(period[4:6])
    last_day = monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day:02d}"
    return start, end


def iter_monthly_periods(count: int = 12) -> list[str]:
    now = datetime.now(timezone.utc)
    periods: list[str] = []
    y, m = now.year, now.month
    for _ in range(count):
        periods.append(f"{y}{m:02d}")
        m -= 1
        if m <= 0:
            m = 12
            y -= 1
    return periods


def snapshot_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def cap_analytics_ending_date(ending_date: str) -> str:
    """Analytics API rejects ending_date beyond the latest available day (~1d lag)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return ending_date if ending_date <= today else today


def period_analytics_dates(period: str) -> tuple[str, str]:
    """Inclusive start and exclusive end dates (YYYY-MM-DD) for Enterprise Analytics API."""
    year = int(period[:4])
    month = int(period[4:6])
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    return start, cap_analytics_ending_date(end)


def is_enterprise_analytics_key(api_token: str) -> bool:
    """Claude Enterprise (claude.ai) keys use read:analytics, not api:admin."""
    token = (api_token or "").strip()
    return token.startswith("sk-ant-api") and not token.startswith("sk-ant-admin")


def iter_period_days(period: str, *, lag_days: int = 2) -> list[str]:
    """ISO dates (YYYY-MM-DD) inside a YYYYMM period, capped by Copilot metrics lag."""
    start, end = period_range(period)
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    latest = datetime.now(timezone.utc).date() - timedelta(days=lag_days)
    if end_d > latest:
        end_d = latest
    if start_d > end_d:
        return []
    days: list[str] = []
    current = start_d
    while current <= end_d:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


class AnthropicAdminClient:
    def __init__(self, api_token: str, org_id: str = ""):
        if not api_token:
            raise ValueError("Anthropic admin API token is required")
        self.api_token = api_token
        self.org_id = org_id
        self._headers = {
            "x-api-key": api_token,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{ANTHROPIC_BASE}{path}"
        with httpx.Client(timeout=120) as client:
            resp = client.get(url, headers=self._headers, params=params or {})
            if resp.status_code >= 400:
                body = resp.text[:500]
                if resp.status_code == 403 and "permission_error" in body:
                    if "read:analytics" in body:
                        raise RuntimeError(
                            "Chave Claude Enterprise sem escopo read:analytics. Em claude.ai → "
                            "Configurações → APIs, habilite a API de Analytics e crie uma chave com "
                            "read:analytics (e read:members para licenças). A chave atual só tem escopos "
                            "de compliance/RBAC. "
                            f"Detalhe: {body}"
                        )
                    if "read:members" in body:
                        raise RuntimeError(
                            "Chave Claude Enterprise sem escopo read:members. Crie uma chave com "
                            "read:members para listar usuários/licenças. "
                            f"Detalhe: {body}"
                        )
                    if "api:admin" in body:
                        raise RuntimeError(
                            "Endpoint do Claude Console (usage_report) exige Admin API Key sk-ant-admin "
                            "com api:admin. Organizações Enterprise (claude.ai) devem usar Analytics API "
                            "com read:analytics, não api:admin. "
                            f"Detalhe: {body}"
                        )
                    raise RuntimeError(f"Anthropic permission_error em {path}: {body}")
                if resp.status_code == 400 and "Latest available data" in body and params:
                    match = re.search(
                        r"Latest available data for this query is (\d{4}-\d{2}-\d{2})", body
                    )
                    if match and params.get("ending_date"):
                        latest = match.group(1)
                        retry_end = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()
                        if retry_end != params.get("ending_date"):
                            retry_params = dict(params)
                            retry_params["ending_date"] = retry_end
                            retry = client.get(url, headers=self._headers, params=retry_params)
                            if retry.status_code < 400:
                                return retry.json()
                raise RuntimeError(f"Anthropic API {path} failed ({resp.status_code}): {body}")
            return resp.json()

    def test(self) -> None:
        self._get("/v1/organizations/users", {"limit": 1})

    def test_usage(self, period: str | None = None) -> None:
        period = period or current_period()
        start, end = period_range(period)
        self._get(
            "/v1/organizations/usage_report/claude_code",
            {"starting_at": start, "ending_at": end, "limit": 1},
        )

    def test_analytics_usage(self, period: str | None = None) -> None:
        period = period or current_period()
        start, end = period_analytics_dates(period)
        self._get(
            "/v1/organizations/analytics/users",
            {"starting_date": start, "ending_date": end, "limit": 1},
        )

    def iter_analytics_users(self, starting_date: str, ending_date: str) -> Iterator[dict[str, Any]]:
        page: str | None = None
        while True:
            params: dict[str, Any] = {
                "starting_date": starting_date,
                "ending_date": ending_date,
                "limit": 1000,
            }
            if page:
                params["page"] = page
            data = self._get("/v1/organizations/analytics/users", params)
            rows = data.get("data") or []
            for row in rows:
                yield row
            page = data.get("next_page")
            if not page:
                break

    def iter_users(self) -> Iterator[dict[str, Any]]:
        after_id: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if after_id:
                params["after_id"] = after_id
            data = self._get("/v1/organizations/users", params)
            users = data.get("data") or data.get("users") or []
            if not users:
                break
            for user in users:
                yield user
            if not data.get("has_more"):
                break
            after_id = users[-1].get("id")

    def iter_usage_claude_code(self, start: str, end: str) -> Iterator[dict[str, Any]]:
        after_id: str | None = None
        while True:
            params: dict[str, Any] = {
                "starting_at": start,
                "ending_at": end,
                "limit": 1000,
            }
            if after_id:
                params["page"] = after_id
            data = self._get("/v1/organizations/usage_report/claude_code", params)
            rows = data.get("data") or []
            if not rows:
                break
            for row in rows:
                yield row
            if not data.get("has_more"):
                break
            after_id = data.get("next_page")

    def iter_usage_messages(self, start: str, end: str) -> Iterator[dict[str, Any]]:
        after_id: str | None = None
        while True:
            params: dict[str, Any] = {
                "starting_at": start,
                "ending_at": end,
                "limit": 1000,
            }
            if after_id:
                params["page"] = after_id
            data = self._get("/v1/organizations/usage_report/messages", params)
            rows = data.get("data") or []
            if not rows:
                break
            for row in rows:
                yield row
            if not data.get("has_more"):
                break
            after_id = data.get("next_page")

    def iter_cost_report(self, start: str, end: str) -> Iterator[dict[str, Any]]:
        after_id: str | None = None
        while True:
            params: dict[str, Any] = {
                "starting_at": start,
                "ending_at": end,
                "limit": 1000,
            }
            if after_id:
                params["page"] = after_id
            data = self._get("/v1/organizations/cost_report", params)
            rows = data.get("data") or []
            if not rows:
                break
            for row in rows:
                yield row
            if not data.get("has_more"):
                break
            after_id = data.get("next_page")


def _normalize_github_login_key(value: str) -> str:
    """Normalize enterprise login/email variants to a shared lookup key."""
    value = (value or "").strip().lower()
    if value.endswith("_tefbr"):
        value = value[:-6]
    if "@" in value:
        value = value.split("@", 1)[0]
    return value.replace("-", ".").replace("_", ".")


class GitHubEnterpriseClient:
    def __init__(self, pat_token: str, enterprise_slug: str, email_domain: str = "telefonica.com.br"):
        if not pat_token:
            raise ValueError("GitHub PAT token is required")
        if not enterprise_slug:
            raise ValueError("GitHub enterprise_slug is required")
        self.pat_token = pat_token
        self.enterprise_slug = enterprise_slug
        self.email_domain = email_domain
        self._headers = {
            "Authorization": f"Bearer {pat_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._scim_headers = {
            **self._headers,
            "Accept": "application/scim+json",
        }

    def test(self) -> None:
        url = f"{GITHUB_API}/enterprises/{self.enterprise_slug}/copilot/billing/seats?per_page=1"
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, headers=self._headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"GitHub API test failed ({resp.status_code}): {resp.text[:500]}")

    def iter_seats(self) -> Iterator[dict[str, Any]]:
        page = 1
        with httpx.Client(timeout=120) as client:
            while True:
                url = (
                    f"{GITHUB_API}/enterprises/{self.enterprise_slug}/copilot/billing/seats"
                    f"?per_page=100&page={page}"
                )
                resp = client.get(url, headers=self._headers)
                if resp.status_code >= 400:
                    raise RuntimeError(f"GitHub seats API failed ({resp.status_code}): {resp.text[:500]}")
                seats = resp.json().get("seats") or []
                if not seats:
                    break
                for seat in seats:
                    yield seat
                page += 1

    def resolve_email_map(self) -> dict[str, tuple[str, str]]:
        """Return normalized login key -> (email, resolution_source)."""
        mapping: dict[str, tuple[str, str]] = {}
        start_index = 1
        with httpx.Client(timeout=120) as client:
            while True:
                url = (
                    f"{GITHUB_API}/scim/v2/enterprises/{self.enterprise_slug}/Users"
                    f"?count=100&startIndex={start_index}"
                )
                resp = client.get(url, headers=self._scim_headers)
                if resp.status_code >= 400:
                    break
                payload = resp.json()
                resources = payload.get("Resources") or []
                if not resources:
                    break
                for resource in resources:
                    login = (resource.get("userName") or "").strip()
                    emails = resource.get("emails") or []
                    primary = next((e.get("value") for e in emails if e.get("primary")), None)
                    if not primary and emails:
                        primary = emails[0].get("value")
                    if not primary:
                        continue
                    for candidate in {login, primary, resource.get("externalId", "")}:
                        key = _normalize_github_login_key(str(candidate))
                        if key:
                            mapping[key] = (primary, "scim")
                total = int(payload.get("totalResults") or 0)
                items_per_page = int(payload.get("itemsPerPage") or len(resources))
                start_index += items_per_page
                if start_index > total or not resources:
                    break
        return mapping

    def resolve_email(
        self,
        login: str,
        scim_map: dict[str, tuple[str, str]],
        *,
        use_api_fallback: bool = True,
    ) -> tuple[str, str]:
        key = _normalize_github_login_key(login)
        if key in scim_map:
            return scim_map[key]
        if use_api_fallback:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{GITHUB_API}/users/{login}", headers=self._headers)
                if resp.status_code == 200:
                    email = (resp.json().get("email") or "").strip()
                    if email:
                        return email, "api"
        if self.email_domain:
            return f"{login}@{self.email_domain}", "heuristica"
        return "", "heuristica"

    def _copilot_usage_headers(self) -> dict[str, str]:
        return {
            **self._headers,
            "X-GitHub-Api-Version": "2026-03-10",
        }

    @staticmethod
    def _parse_copilot_report_body(text: str) -> list[dict[str, Any]]:
        text = text.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            rows: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
            return rows
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            day_totals = payload.get("day_totals")
            if isinstance(day_totals, list):
                return day_totals
            return [payload]
        return []

    def fetch_copilot_daily_report(self, report_kind: str, day: str) -> list[dict[str, Any]]:
        """Download a daily Copilot usage metrics report (users-1-day or enterprise-1-day)."""
        url = (
            f"{GITHUB_API}/enterprises/{self.enterprise_slug}/copilot/metrics/reports/{report_kind}"
        )
        with httpx.Client(timeout=120) as client:
            resp = client.get(url, headers=self._copilot_usage_headers(), params={"day": day})
            if resp.status_code in (404, 204):
                return []
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"GitHub Copilot usage report failed ({resp.status_code}) "
                    f"for {report_kind} {day}: {resp.text[:500]}"
                )
            links = resp.json().get("download_links") or []
            rows: list[dict[str, Any]] = []
            for link in links:
                dl = client.get(link, follow_redirects=True)
                if dl.status_code != 200:
                    continue
                rows.extend(self._parse_copilot_report_body(dl.text))
            return rows

    def iter_copilot_metrics(self, since: str, until: str) -> Iterator[dict[str, Any]]:
        """Yield daily Copilot usage rows for a date range via the current usage metrics API."""
        start_d = date.fromisoformat(since[:10])
        end_d = date.fromisoformat(until[:10])
        latest = datetime.now(timezone.utc).date() - timedelta(days=2)
        if end_d > latest:
            end_d = latest
        current = start_d
        while current <= end_d:
            day = current.isoformat()
            user_rows = self.fetch_copilot_daily_report("users-1-day", day)
            if user_rows:
                for row in user_rows:
                    yield row
            else:
                for row in self.fetch_copilot_daily_report("enterprise-1-day", day):
                    row = dict(row)
                    row.setdefault("day", day)
                    yield row
            current += timedelta(days=1)
