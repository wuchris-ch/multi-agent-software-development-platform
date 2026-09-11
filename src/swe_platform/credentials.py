"""Trusted host-side Keychain access. Values never belong in artifacts or worker specs."""

import subprocess
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .models import StrictModel


class GatewayProfile(StrictModel):
    schema_version: Literal["gateway-profile/v1"] = "gateway-profile/v1"
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    keychain_service: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def safe_endpoint(self):
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
        result = subprocess.run(
            ["security", "find-generic-password", "-s", self.keychain_service, "-w"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode or not result.stdout.strip():
            raise ValueError("Configured Keychain credential is unavailable")
        return {
            "MODEL_GATEWAY_API_KEY": result.stdout.decode().strip(),
            "MODEL_GATEWAY_BASE_URL": self.base_url,
            "REVIEW_AGENT_MODEL": self.model,
        }
