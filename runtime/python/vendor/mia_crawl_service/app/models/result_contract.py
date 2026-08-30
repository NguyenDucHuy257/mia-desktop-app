from __future__ import annotations

from typing import Any, Mapping


BLOCKED_PUBLIC_FIELDS = frozenset({
    'password', 'token', 'authorization', 'proxy', 'proxy_url',
    'internal_session_id', 'session_hash', 'raw_json_path',
    'raw_detail_path', 'xml_path', 'database_path', 'db_path',
})


def public_source_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return source business fields allowed by the existing Result API contract."""
    return {
        str(key): item for key, item in value.items()
        if str(key).casefold() not in BLOCKED_PUBLIC_FIELDS
        and not str(key).casefold().endswith('_path')
    }
