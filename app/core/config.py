from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "release-controller"
    app_env: str = "production"
    log_level: str = "INFO"
    database_url: str = "sqlite:////data/release.db"
    drone_server: str = "http://drone.example.test"
    drone_token: str = ""
    # Which repositories a release promotes, and to what target, are
    # project_components rows now -- not environment variables.  The DRONE_*_REPO_*
    # and DRONE_DEFAULT_TARGET variables are still read once, by migration
    # 20260902_0014, to seed the first project from an existing installation.
    # drone_timeout_seconds remains the read budget.  Connecting is held to a much
    # shorter budget, and promote gets its own, longer one: a single number had to
    # be short enough for a poll and long enough for a promote at the same time.
    drone_timeout_seconds: float = 10.0
    drone_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    drone_write_timeout_seconds: float = Field(default=30.0, gt=0)
    gitea_server: str = "http://gitea.test"
    gitea_token: str = ""
    gitea_timeout_seconds: float = Field(default=10.0, gt=0)
    gitea_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    gitea_write_timeout_seconds: float = Field(default=30.0, gt=0)
    # Encrypts the upstream tokens stored in upstream_connections.  Free-form;
    # anything with real entropy will do.  See app/core/crypto.py.
    app_secret_key: str = ""
    # Whether changing project and connection configuration requires a Gitea
    # admin.  Left off by default because turning it on can lock an installation
    # out of its own settings pages if nobody's Gitea account carries the admin
    # flag -- check /api/v1/auth/me first, then turn it on.
    project_admin_writes: bool = False
    auth_enabled: bool = True
    gitea_oauth_client_id: str = ""
    gitea_oauth_client_secret: str = ""
    gitea_oauth_callback_url: str | None = None
    auth_session_cookie_name: str = "release_controller_session"
    auth_session_hours: int = Field(default=8, ge=1, le=720)
    auth_cookie_secure: bool = True
    deployment_timeout_seconds: int = 1800
    # Recovery. The worker drives every non-terminal deployment, not just the ones
    # a schedule created, so a deployment no longer depends on a browser tab
    # staying open. The poll interval keeps that from hammering Drone.
    recovery_enabled: bool = True
    recovery_batch_size: int = Field(default=25, ge=1, le=500)
    deployment_poll_interval_seconds: int = Field(default=15, ge=1)
    publish_timeout_seconds: int = Field(default=600, ge=1)
    schedule_claim_timeout_seconds: int = Field(default=900, ge=1)
    # Applied to idempotent upstream reads only; promote and create-release are
    # never retried automatically.
    upstream_retry_attempts: int = Field(default=3, ge=1, le=10)
    upstream_retry_base_seconds: float = Field(default=0.25, gt=0)
    upstream_retry_max_seconds: float = Field(default=2.0, gt=0)
    background_worker_enabled: bool = False
    background_worker_poll_seconds: float = Field(default=5.0, gt=0)
    scheduler_batch_size: int = Field(default=20, ge=1, le=1000)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str = "release-controller@localhost"
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_tls_verify: bool = True
    smtp_tls_ca_file: str | None = None
    smtp_timeout_seconds: float = Field(default=10.0, gt=0)
    smtp_default_recipients: str = ""
    smtp_managed_recipient_targets: str = "production"
    smtp_max_attempts: int = Field(default=5, ge=1)
    smtp_retry_seconds: int = Field(default=60, ge=1)
    smtp_claim_timeout_seconds: int = Field(default=300, ge=1)
    notification_attachment_max_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1, le=25 * 1024 * 1024
    )

    @model_validator(mode="after")
    def refuse_unauthenticated_production(self) -> "Settings":
        """Production may not run with authentication disabled.

        With AUTH_ENABLED=false the operator recorded against every promote and
        publish is whatever the client sent, so the audit trail becomes
        unfalsifiable in the environment where it matters most.
        """
        if self.app_env.strip().lower() == "production" and not self.auth_enabled:
            raise ValueError(
                "AUTH_ENABLED=false is not permitted when APP_ENV=production: the "
                "recorded operator would be supplied by the client and the audit "
                "trail could not be trusted. Set AUTH_ENABLED=true and configure "
                "GITEA_OAUTH_CLIENT_ID/SECRET, or set APP_ENV to a non-production "
                "value for local work."
            )
        return self

    @model_validator(mode="after")
    def refuse_unencrypted_production(self) -> "Settings":
        """Production may not store upstream tokens under the development key.

        Connection tokens are live deployment credentials.  Without APP_SECRET_KEY
        they are encrypted with a constant that is published in this repository,
        which protects nothing -- and failing loudly at startup is far better than
        discovering it from a leaked database file.
        """
        if self.app_env.strip().lower() == "production" and not self.app_secret_key.strip():
            raise ValueError(
                "APP_SECRET_KEY must be set when APP_ENV=production: it encrypts the "
                "Drone and Gitea tokens stored in upstream_connections, and without "
                "it they would be encrypted with a constant from the source tree. "
                'Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.'
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
