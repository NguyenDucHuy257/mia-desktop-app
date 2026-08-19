"""Authenticated external control API for Server A.

The desktop runtime only consumes the result reader.  Keep the HTTP app import
lazy so importing that reader does not require the server-only FastAPI stack.
"""

__all__ = ['create_app']


def create_app(*args, **kwargs):
    from app.external_api.app import create_app as factory

    return factory(*args, **kwargs)
