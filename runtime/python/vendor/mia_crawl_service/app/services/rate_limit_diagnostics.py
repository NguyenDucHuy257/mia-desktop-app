"""Collect HTTP 429 evidence without leaking credentials.

The portal's throttle key is still unknown. Earlier experiments showed it
survives both IP rotation and re-login, so the remaining candidates are the
account identity, a session cookie, or a client fingerprint. This module
records the facts needed to tell those apart, and nothing else.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.config.crawl_config import mask_secret

logger = logging.getLogger(__name__)

# Response headers whose values must never reach a log file verbatim.
SENSITIVE_HEADER_NAMES = frozenset({
    'authorization', 'set-cookie', 'cookie', 'proxy-authorization',
    'x-auth-token', 'x-api-key',
})
# Header names that usually carry throttle policy. Logged for every 429 so a
# window/bucket can be reconstructed afterwards from the evidence file alone.
THROTTLE_HEADER_HINTS = (
    'retry-after', 'x-ratelimit-limit', 'x-ratelimit-remaining',
    'x-ratelimit-reset', 'ratelimit-limit', 'ratelimit-remaining',
    'ratelimit-reset', 'x-rate-limit-limit', 'x-rate-limit-remaining',
    'x-rate-limit-reset', 'server', 'via', 'x-cache', 'cf-ray',
    'x-served-by', 'x-request-id', 'x-correlation-id', 'x-envoy-ratelimited',
)
# JWT claims worth reading in clear: they reveal token lifetime and whether the
# server binds the session to the account. Every other claim value is masked.
NON_SECRET_JWT_CLAIMS = frozenset({
    'exp', 'iat', 'nbf', 'auth_time', 'typ', 'alg', 'token_type', 'scope',
})


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return response headers with credential-bearing values masked."""
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SENSITIVE_HEADER_NAMES:
            redacted[name] = f'<redacted len={len(value)}>'
        else:
            redacted[name] = value
    return redacted


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload locally, masking anything identity-like.

    No signature verification and no secret is needed: this only answers
    "which claims does the portal put in the token?". Long string values are
    masked because they may contain the tax code or account name.
    """
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('token is not a three-part JWT')
    payload_segment = parts[1]
    padding = '=' * (-len(payload_segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_segment + padding)
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ValueError('cannot decode the JWT payload segment') from error
    if not isinstance(payload, dict):
        raise ValueError('JWT payload is not an object')

    claims: dict[str, Any] = {}
    for name, value in payload.items():
        if name in NON_SECRET_JWT_CLAIMS or isinstance(value, (int, float, bool)):
            claims[name] = value
        elif isinstance(value, str):
            claims[name] = mask_secret(value)
        else:
            claims[name] = f'<{type(value).__name__}>'
    return claims


def describe_rate_limit_response(
    response: Any,
    *,
    endpoint: str,
    attempt: int,
    max_attempts: int,
    route_label: str,
    crawl_profile: str,
    account_label: str | None = None,
) -> dict[str, Any]:
    """Build one credential-free evidence record for a single 429 response."""
    headers: Mapping[str, str] = getattr(response, 'headers', {}) or {}
    lowered = {name.lower(): value for name, value in headers.items()}
    record: dict[str, Any] = {
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'endpoint': endpoint,
        'status_code': getattr(response, 'status_code', None),
        'attempt': attempt,
        'max_attempts': max_attempts,
        'route_label': route_label,
        'crawl_profile': crawl_profile,
        'throttle_headers': {
            name: lowered[name] for name in THROTTLE_HEADER_HINTS if name in lowered
        },
        'all_headers': redact_headers(headers),
        'set_cookie_present': 'set-cookie' in lowered,
    }
    if account_label is not None:
        record['account_label'] = account_label
    body = getattr(response, 'content', None)
    if not isinstance(body, bytes):
        text = getattr(response, 'text', None)
        body = text.encode('utf-8', errors='replace') if isinstance(text, str) else b''
    if body:
        record['body_byte_count'] = len(body)
        record['body_sha256'] = hashlib.sha256(body).hexdigest()
    return record


class RateLimitEvidenceLog:
    """Append 429 evidence as JSONL so it survives per-run log truncation."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def record(self, event: Mapping[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as stream:
                json.dump(event, stream, ensure_ascii=False)
                stream.write('\n')
        except OSError:
            # Diagnostics must never break a crawl that is otherwise working.
            logger.exception('Could not append rate-limit evidence path=%s', self.path)
