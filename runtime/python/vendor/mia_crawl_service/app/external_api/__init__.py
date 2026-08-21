"""Source control-service package used in-process by MIA Desktop.

The desktop runtime intentionally does not expose the upstream FastAPI HTTP
application.  Individual source modules such as ``models``, ``service`` and
``results`` are imported directly by the local JSON-RPC host.
"""

__all__: list[str] = []
