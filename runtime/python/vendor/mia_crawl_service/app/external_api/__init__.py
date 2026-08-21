"""Authenticated external control API for Server A.

The desktop runtime vendors the source service/models/results but replaces the
HTTP transport with local Electron JSON-RPC. Keep the HTTP app import lazy so
source business modules do not require FastAPI merely to run offline.
"""

__all__ = ['create_app']


def create_app(*args, **kwargs):
    from app.external_api.app import create_app as factory

    return factory(*args, **kwargs)
