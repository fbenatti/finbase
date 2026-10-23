import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://finops:finops@localhost:5432/finops",
    )
    database_url_sync: str = os.getenv(
        "DATABASE_URL_SYNC",
        "postgresql://finops:finops@localhost:5432/finops",
    )
    cors_origins: str = os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
    )

    # Environment control
    env: str = os.getenv("ENV", "development")
    skip_seed: bool = os.getenv("SKIP_SEED", "false").lower() == "true"
    # Production replicas are stateless.  A single dedicated scheduler service
    # performs startup/bootstrap work and owns APScheduler.
    bootstrap_enabled: bool = os.getenv("BOOTSTRAP_ENABLED", "true").lower() == "true"
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"

    # --- Auth / RBAC ---
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "dev-insecure-secret-change-me")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    admin_initial_password: str = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    auth_enabled: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    dev_master_token: str = os.getenv("DEV_MASTER_TOKEN", "")
    audit_retention_days: int = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))
    allow_local_login: bool = os.getenv("ALLOW_LOCAL_LOGIN", "true").lower() == "true"
    sso_cookie_secure: bool = os.getenv("SSO_COOKIE_SECURE", "false").lower() == "true"
    sso_redirect_uri: str = os.getenv("SSO_REDIRECT_URI", "")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
