"""Trusted credential resolution. Values never belong in artifacts or worker specs."""

import os
import subprocess
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .models import StrictModel


class GatewayProfile(StrictModel):
    schema_version: Literal["gateway-profile/v1"] = "gateway-profile/v1"
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    keychain_service: str | None = Field(default=None, min_length=1, max_length=200)
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,199}$")

    @model_validator(mode="after")
    def safe_endpoint(self):
        if bool(self.keychain_service) == bool(self.api_key_env):
            raise ValueError("Select exactly one credential source: environment or Keychain")
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Gateway profile requires HTTPS without URL credentials")
        return self

    def environment(self):
        if self.api_key_env:
            key = os.environ.get(self.api_key_env, "").strip()
        else:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", self.keychain_service, "-w"],
                capture_output=True,
                timeout=10,
            )
            key = result.stdout.decode().strip() if result.returncode == 0 else ""
        if not key:
            raise ValueError("Configured model credential is unavailable")
        return {
            "MODEL_GATEWAY_API_KEY": key,
            "MODEL_GATEWAY_BASE_URL": self.base_url,
            "REVIEW_AGENT_MODEL": self.model,
        }
