from __future__ import annotations


class PipelineInterruption(RuntimeError):
    """Control-flow interruption that must not be classified as a business error."""


class UserCancellationRequested(PipelineInterruption):
    """The API/user requested cancellation of the current job."""


class WorkerShutdownRequested(PipelineInterruption):
    """The worker process is stopping after SIGTERM/SIGINT or restart."""


class SourceRouteFailoverRequested(PipelineInterruption):
    """The assigned proxy exhausted source retries and must be replaced."""

    code = 'source_route_failover'

    def __init__(
        self, *, proxy_id: str, endpoint: str,
        source_error_code: str = 'source_timeout',
    ) -> None:
        if not proxy_id.startswith('proxy-'):
            raise ValueError('proxy_id is invalid')
        if not endpoint.strip():
            raise ValueError('endpoint is required')
        self.proxy_id = proxy_id
        self.endpoint = endpoint
        self.source_error_code = source_error_code
        super().__init__('source route timed out; failover requested')
