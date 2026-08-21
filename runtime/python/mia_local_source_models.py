"""Local dataclass DTOs for the source service boundary.

The upstream ``app.external_api.models`` module is a Pydantic HTTP/API schema.
MIA Desktop does not expose that HTTP API, so the local JSON-RPC host supplies
only the small value-object surface consumed by ``ExternalApiService``.
Business behavior remains in the vendored source service/repositories/pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class SecretValue:
    """Minimal in-process secret wrapper matching SecretStr's consumed method."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value is required")
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('**********')"


def _required_text(value: str, name: str, *, min_length: int = 1, max_length: int = 256) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not min_length <= len(normalized) <= max_length:
        raise ValueError(f"{name} is invalid")
    return normalized


def _secret(value: str | SecretValue) -> SecretValue:
    return value if isinstance(value, SecretValue) else SecretValue(value)


@dataclass(slots=True)
class CreateAccountConnectionBody:
    username: str
    password: str | SecretValue

    def __post_init__(self) -> None:
        self.username = _required_text(self.username, "username")
        self.password = _secret(self.password)


class ReconnectAccountConnectionBody(CreateAccountConnectionBody):
    pass


@dataclass(slots=True)
class CreateSessionBody:
    account_key: str
    username: str
    password: str | SecretValue
    proxy_url: str | SecretValue | None = None
    ttl_seconds: int = 28800

    def __post_init__(self) -> None:
        self.account_key = _required_text(self.account_key, "account_key")
        self.username = _required_text(self.username, "username")
        self.password = _secret(self.password)
        if self.proxy_url is not None:
            self.proxy_url = _secret(self.proxy_url)
        if isinstance(self.ttl_seconds, bool) or not isinstance(self.ttl_seconds, int):
            raise TypeError("ttl_seconds must be an integer")
        if not 60 <= self.ttl_seconds <= 86400:
            raise ValueError("ttl_seconds is invalid")


@dataclass(slots=True)
class CreateJobBody:
    connection_id: str
    date_from: date
    date_to: date
    directions: list[str]
    query_types: list[str]
    force_refresh: bool = False
    refresh_latest_month: bool = False
    result_scope: str = "detail"
    include_xml: bool = False
    include_mvt: bool = False

    def __post_init__(self) -> None:
        self.connection_id = _required_text(
            self.connection_id, "connection_id", min_length=6, max_length=128
        )
        if not self.connection_id.startswith("conn_"):
            raise ValueError("connection_id is invalid")
        if not isinstance(self.date_from, date) or not isinstance(self.date_to, date):
            raise TypeError("date range must use date values")
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        if not 1 <= len(self.directions) <= 2 or len(self.directions) != len(set(self.directions)):
            raise ValueError("directions are invalid")
        if any(value not in {"purchase", "sold"} for value in self.directions):
            raise ValueError("directions are invalid")
        if not 1 <= len(self.query_types) <= 2 or len(self.query_types) != len(set(self.query_types)):
            raise ValueError("query_types are invalid")
        if any(value not in {"query", "sco-query"} for value in self.query_types):
            raise ValueError("query_types are invalid")
        for name in ("force_refresh", "refresh_latest_month", "include_xml", "include_mvt"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be boolean")
        if self.result_scope not in {"overview", "detail"}:
            raise ValueError("result_scope is invalid")
        if self.result_scope == "overview" and (self.include_xml or self.include_mvt):
            raise ValueError("include_xml and include_mvt require result_scope=detail")


def install_source_model_shim() -> None:
    """Install this module under the source import name before service import."""
    import sys

    sys.modules["app.external_api.models"] = sys.modules[__name__]


__all__ = [
    "CreateAccountConnectionBody",
    "CreateJobBody",
    "CreateSessionBody",
    "ReconnectAccountConnectionBody",
    "SecretValue",
    "install_source_model_shim",
]
