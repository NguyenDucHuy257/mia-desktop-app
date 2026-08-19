from __future__ import annotations


def find_http_status(error: BaseException) -> int | None:
    """Return the first HTTP status found in an exception cause/context chain."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, 'response', None)
        status_code = getattr(response, 'status_code', None)
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            return status_code
        current = current.__cause__ or current.__context__
    return None
