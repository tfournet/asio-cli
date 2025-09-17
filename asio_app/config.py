from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ENV_VAR_BASE_URL = "ASIO_BASE_URL"
_ENV_VAR_CLIENT_ID = "ASIO_CLIENT_ID"
_ENV_VAR_CLIENT_SECRET = "ASIO_CLIENT_SECRET"
_ENV_VAR_SCOPE = "ASIO_SCOPE"

_DEFAULT_SCOPES = (
    "platform.companies.read",
    "platform.devices.read",
    "platform.custom_fields_values.read",
    "platform.sites.write",
    "platform.tickets.update",
    "platform.sites.read",
    "platform.policies.read",
    "platform.dataMapping.read",
    "platform.tickets.create",
    "platform.asset.read",
    "platform.deviceGroups.read",
    "platform.automation.read",
    "platform.automation.create",
    "platform.policies.create",
    "platform.custom_fields_definitions.write",
    "platform.tickets.read",
    "platform.agent.delete",
    "platform.policies.delete",
    "platform.policies.update",
    "platform.custom_fields_values.write",
    "platform.custom_fields_definitions.read",
    "platform.patching.read",
    "platform.agent-token.read",
    "platform.agent.read",
)
_DEFAULT_SCOPE_STRING = " ".join(_DEFAULT_SCOPES)


@dataclass(frozen=True)
class AsioConfig:
    """Holds configuration required for authenticating against the Asio API."""

    base_url: str
    client_id: str
    client_secret: str
    scope: str

    @property
    def token_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/token"


def load_config(dotenv_path: Optional[str] = None) -> AsioConfig:
    """Load configuration from environment variables and optional .env."""
    if dotenv_path is None:
        dotenv_path = ".env"
    env_file = Path(dotenv_path)
    if env_file.exists():
        load_dotenv(env_file)

    base_url = os.getenv(_ENV_VAR_BASE_URL)
    client_id = os.getenv(_ENV_VAR_CLIENT_ID)
    client_secret = os.getenv(_ENV_VAR_CLIENT_SECRET)
    scope = os.getenv(_ENV_VAR_SCOPE, _DEFAULT_SCOPE_STRING)

    missing = [
        name
        for name, value in (
            (_ENV_VAR_BASE_URL, base_url),
            (_ENV_VAR_CLIENT_ID, client_id),
            (_ENV_VAR_CLIENT_SECRET, client_secret),
        )
        if not value
    ]
    if missing:
        missing_clause = ", ".join(missing)
        raise RuntimeError(f"Missing required Asio configuration environment variables: {missing_clause}")

    return AsioConfig(
        base_url=base_url.rstrip("/"),
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
    )


__all__ = ["AsioConfig", "load_config"]
