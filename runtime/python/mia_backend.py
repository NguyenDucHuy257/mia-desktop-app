"""Compatibility import for the source-of-truth crawl runtime.

Historical desktop builds implemented their own crawler pipeline, coverage policy,
job admission and progress state in this module.  That duplicated
mia-crawl-service and caused the UI/backend to diverge.  Production behavior now
lives in :mod:`mia_source_backend`, which hosts the vendored source classes
unchanged behind the Electron JSON-RPC transport.

Keep this alias temporarily because runtime/tests from earlier desktop phases
still import ``ProductionBackend``.  No business logic belongs here.
"""

from mia_source_backend import SourceBackend


ProductionBackend = SourceBackend

__all__ = ["ProductionBackend", "SourceBackend"]
