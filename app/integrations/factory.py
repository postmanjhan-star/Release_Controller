"""Construct upstream clients with this deployment's timeout and retry policy."""

from app.core.config import Settings
from app.core.crypto import token_cipher
from app.domain.registry.entities import Connection
from app.integrations.drone.client import DroneClient
from app.integrations.gitea.client import GiteaClient
from app.integrations.retry import RetryPolicy


def retry_policy_from(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        attempts=settings.upstream_retry_attempts,
        base_seconds=settings.upstream_retry_base_seconds,
        max_seconds=settings.upstream_retry_max_seconds,
    )


def build_drone_client(settings: Settings) -> DroneClient:
    return DroneClient(
        settings.drone_server,
        settings.drone_token,
        settings.drone_timeout_seconds,
        connect_timeout=settings.drone_connect_timeout_seconds,
        write_timeout=settings.drone_write_timeout_seconds,
        retry_policy=retry_policy_from(settings),
    )


def build_gitea_client(settings: Settings) -> GiteaClient:
    return GiteaClient(
        settings.gitea_server,
        settings.gitea_token,
        settings.gitea_timeout_seconds,
        connect_timeout=settings.gitea_connect_timeout_seconds,
        write_timeout=settings.gitea_write_timeout_seconds,
        retry_policy=retry_policy_from(settings),
    )


# The registry variants below take their server and credential from a stored
# connection instead of from Settings.  Everything else -- the split timeouts and
# the retry policy -- still comes from Settings, so this file remains the single
# place where a client's behaviour is decided.  Raises TokenDecryptionError when
# the stored token predates the current APP_SECRET_KEY.


def build_drone_client_for(connection: Connection, settings: Settings) -> DroneClient:
    return DroneClient(
        connection.base_url,
        token_cipher(settings).decrypt(connection.token_encrypted),
        settings.drone_timeout_seconds,
        connect_timeout=settings.drone_connect_timeout_seconds,
        write_timeout=settings.drone_write_timeout_seconds,
        retry_policy=retry_policy_from(settings),
    )


def build_gitea_client_for(connection: Connection, settings: Settings) -> GiteaClient:
    return GiteaClient(
        connection.base_url,
        token_cipher(settings).decrypt(connection.token_encrypted),
        settings.gitea_timeout_seconds,
        connect_timeout=settings.gitea_connect_timeout_seconds,
        write_timeout=settings.gitea_write_timeout_seconds,
        retry_policy=retry_policy_from(settings),
    )
