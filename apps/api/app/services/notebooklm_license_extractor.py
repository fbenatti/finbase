import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.ai_extractor_base import current_period, snapshot_timestamp
from app.services.google_extractor_utils import (
    google_client_from_config,
    is_human_user_email,
    iter_gcp_billing_service_accounts,
    list_discovery_user_licenses,
    list_project_iam_users,
)

logger = logging.getLogger(__name__)


class NotebookLMLicenseExtractor:
    """NotebookLM Enterprise seats via Discovery Engine UserLicense (users + lastLogin/createTime)."""

    PRODUCT_NAME = "NotebookLM"

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.project_id = self.config_json.get("project_id", "")
        self.project_number = self.config_json.get("project_number", "")
        self.license_product_id = self.config_json.get("license_product_id", "Google-Apps")
        self.license_sku_id = self.config_json.get("license_sku_id", "")

    def test_connection(self) -> dict:
        try:
            rows = list_discovery_user_licenses(self.project_id, self.project_number)
            return {
                "status": "OK",
                "message": f"Discovery Engine UserLicense ({self.project_id}) — {len(rows)} licenças ASSIGNED",
            }
        except Exception as exc:
            return {"status": "ERROR", "message": str(exc)[:400]}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        period = current_period()
        now = datetime.now(timezone.utc)
        existing = db_session.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == period,
            )
        ).scalar_one_or_none()
        status = "CHANGED" if existing else "NEW"
        return [
            BlobScanItem(
                blob_path=period,
                blob_prefix="notebooklm_license",
                last_modified=now,
                content_length=None,
                status=status,
                billing_period=period,
            )
        ]

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        period = current_period()
        now = datetime.now(timezone.utc)
        existing = await db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == period,
            )
        )
        blob_row = existing.scalar_one_or_none()
        status = "CHANGED" if blob_row else "NEW"
        items = [
            BlobScanItem(
                blob_path=period,
                blob_prefix="notebooklm_license",
                last_modified=now,
                content_length=None,
                status=status,
                billing_period=period,
            )
        ]
        return BlobScanResult(
            connection_id=self.connection.id,
            total=1,
            new=1 if status == "NEW" else 0,
            changed=1 if status == "CHANGED" else 0,
            unchanged=0,
            items=items,
        )

    def _record(
        self,
        *,
        user_id: str,
        email: str,
        full_name: str = "",
        role_name: str = "",
        is_admin: bool = False,
        is_delegated_admin: bool = False,
        org_unit_path: str = "",
        license_sku_id: str = "",
        license_sku_name: str = "",
        assigned_time: str = "",
        created_time: str = "",
        updated_time: str = "",
        last_login_time: str = "",
        license_assignment_state: str = "",
        license_config: str = "",
        suspension_reason: str = "",
        is_enrolled_in_2sv: bool = False,
        identity_source: str = "",
    ) -> dict:
        created = created_time or assigned_time
        return {
            "user_id": user_id or email,
            "primary_email": email,
            "full_name": full_name or email.split("@")[0].replace(".", " ").replace("_", " "),
            "role_name": role_name,
            "is_admin": is_admin,
            "is_delegated_admin": is_delegated_admin,
            "org_unit_path": org_unit_path,
            "license_sku_id": license_sku_id or self.license_sku_id or license_config or "",
            "license_sku_name": license_sku_name or self.PRODUCT_NAME,
            "license_quantity": 1,
            "assigned_time": created,
            "created_time": created,
            "updated_time": updated_time,
            "last_login_time": last_login_time,
            "license_assignment_state": license_assignment_state,
            "license_config": license_config,
            "suspension_reason": suspension_reason,
            "is_enrolled_in_2sv": is_enrolled_in_2sv,
            "identity_source": identity_source,
            "project_id": self.project_id,
            "project_number": self.project_number,
            "snapshot_date": snapshot_timestamp(),
        }

    def _fetch_licenses(self) -> pd.DataFrame:
        discovery_error = ""
        try:
            return self._fetch_discovery_licenses()
        except Exception as exc:
            logger.warning("[NotebookLMLicense] Discovery Engine UserLicense indisponível (%s)", exc)
            discovery_error = str(exc)
        try:
            return self._fetch_workspace_licenses()
        except Exception as exc:
            logger.warning("[NotebookLMLicense] Workspace indisponível (%s)", exc)
        try:
            return self._fetch_licenses_from_iam()
        except Exception as iam_exc:
            raise RuntimeError(discovery_error or str(iam_exc)) from iam_exc

    def _fetch_discovery_licenses(self) -> pd.DataFrame:
        licenses = list_discovery_user_licenses(self.project_id, str(self.project_number or ""))
        records = []
        seen: set[str] = set()
        for item in licenses:
            email = (item.get("userPrincipal") or "").strip()
            if not is_human_user_email(email) or email.lower() in seen:
                continue
            seen.add(email.lower())
            state = item.get("licenseAssignmentState") or "ASSIGNED"
            records.append(
                self._record(
                    user_id=email,
                    email=email,
                    full_name=item.get("userProfile") or "",
                    license_config=item.get("licenseConfig") or "",
                    license_sku_id=item.get("licenseConfig") or "",
                    created_time=item.get("createTime") or "",
                    updated_time=item.get("updateTime") or "",
                    last_login_time=item.get("lastLoginTime") or "",
                    license_assignment_state=state,
                    identity_source="discoveryengine_user_license",
                )
            )
        if not records:
            raise RuntimeError("Discovery Engine retornou licenças sem e-mail de usuário")
        return pd.DataFrame(records).fillna("")

    def _fetch_workspace_licenses(self) -> pd.DataFrame:
        """Licença = assignment de SKU para um usuário, não a linha de SKU/billing."""
        client = google_client_from_config(self.config_json)
        records: list[dict] = []
        seen: set[str] = set()

        if self.license_sku_id:
            for item in client.iter_license_assignments(self.license_product_id, self.license_sku_id):
                email = (item.get("userId") or item.get("userEmail") or "").strip()
                if not is_human_user_email(email) or email.lower() in seen:
                    continue
                seen.add(email.lower())
                records.append(
                    self._record(
                        user_id=email,
                        email=email,
                        license_sku_id=item.get("skuId") or self.license_sku_id,
                        license_sku_name=item.get("skuName") or self.PRODUCT_NAME,
                        assigned_time=item.get("assignedTime") or "",
                        identity_source="workspace_license",
                    )
                )

        if not records:
            for user in client.iter_users():
                email = (user.get("primaryEmail") or "").strip()
                if not is_human_user_email(email) or email.lower() in seen:
                    continue
                seen.add(email.lower())
                name = user.get("name")
                full_name = name.get("fullName") if isinstance(name, dict) else (name or "")
                records.append(
                    self._record(
                        user_id=str(user.get("id") or email),
                        email=email,
                        full_name=str(full_name or ""),
                        role_name="admin" if user.get("isAdmin") else "",
                        is_admin=bool(user.get("isAdmin", False)),
                        is_delegated_admin=bool(user.get("isDelegatedAdmin", False)),
                        org_unit_path=user.get("orgUnitPath") or "",
                        assigned_time=user.get("creationTime") or "",
                        last_login_time=user.get("lastLoginTime") or "",
                        suspension_reason=user.get("suspensionReason") or "",
                        is_enrolled_in_2sv=bool(user.get("isEnrolledIn2Sv", False)),
                        identity_source="workspace_directory",
                    )
                )

        if not records:
            raise RuntimeError("Google Directory/License Manager não retornou usuários (e-mails) para NotebookLM")
        return pd.DataFrame(records).fillna("")

    def _fetch_licenses_from_iam(self) -> pd.DataFrame:
        emails: list[str] = []
        last_error = ""
        if self.project_id:
            for _code, _cfg, info in iter_gcp_billing_service_accounts():
                try:
                    emails = list_project_iam_users(info, self.project_id)
                    if emails:
                        break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
        emails = sorted({e.strip() for e in emails if is_human_user_email(e)})
        if not emails:
            raise RuntimeError(
                "O endpoint de licenças NotebookLM precisa de usuários (e-mail), não de SKU/projeto. "
                "A chave AIza e o billing export listam SKU; configure service account Workspace + "
                f"admin_email, ou IAM getIamPolicy em {self.project_id} com membros user:email. "
                f"Último erro: {last_error or 'nenhum e-mail humano no IAM'}"
            )
        records = [
            self._record(
                user_id=email,
                email=email,
                identity_source="gcp_iam_user",
            )
            for email in emails
        ]
        return pd.DataFrame(records).fillna("")
