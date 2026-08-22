from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalApiSettings:
    api_keys: dict[str, str]
    caller_id: str = 'server-a'
    require_https: bool = True
    rate_limit_per_minute: int = 120

    def __post_init__(self) -> None:
        if not self.caller_id.strip():
            raise ValueError('service caller ID must not be empty')
        if not self.api_keys:
            raise ValueError('at least one service API key is required')
        for key_id, secret in self.api_keys.items():
            if not key_id.strip() or len(secret) < 32:
                raise ValueError('service API key IDs must be non-empty and secrets at least 32 characters')
        if self.rate_limit_per_minute < 1:
            raise ValueError('rate limit must be positive')

    @classmethod
    def from_environment(cls) -> 'ExternalApiSettings':
        raw = os.getenv('MIA_SERVICE_API_KEYS_JSON', '').strip()
        if not raw:
            raise ValueError('MIA_SERVICE_API_KEYS_JSON is required')
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError('MIA_SERVICE_API_KEYS_JSON must be valid JSON') from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            raise ValueError('MIA_SERVICE_API_KEYS_JSON must be an object of key IDs to secrets')
        return cls(
            api_keys=dict(parsed),
            caller_id=os.getenv('MIA_SERVICE_CALLER_ID', 'server-a').strip(),
            require_https=_env_bool('MIA_EXTERNAL_API_REQUIRE_HTTPS', True),
            rate_limit_per_minute=int(
                os.getenv('MIA_EXTERNAL_API_RATE_LIMIT_PER_MINUTE', '120')
            ),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'{name} must be a boolean')
