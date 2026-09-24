"""Bounded, credential-free wire diagnostics correlated with the active job."""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import logging
import re
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

_scope = ContextVar('crawl_diagnostic_scope', default={})
logger = logging.getLogger('app.crawl_diagnostics')


def clean_text(value, secrets=()):
    text = str(value)
    for secret in sorted((str(v) for v in secrets if v), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)bearer\s+\S+', 'Bearer [REDACTED]', text)
    text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'(?i)(password|token|secret|cookie|authorization|ckey|cvalue|username)["\s\x27]*[:=]\s*(?:"[^"]*"|\x27[^\x27]*\x27|[^\s,;}]+)', r'\1=[REDACTED]', text)
    text = re.sub(r'\b\d{10,14}(?:-\d{3})?\b', '[REDACTED-ID]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[REDACTED-EMAIL]', text)
    return text[:1500]


def emit(event, **fields):
    # Diagnostics must never change crawler control flow.
    try:
        logger.info('crawl_diagnostic %s', json.dumps({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'event': event, **_scope.get(), **fields,
        }, ensure_ascii=True))
    except Exception:
        pass


def job_diagnostics(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(self, job, *args, **kwargs):
            payload = args[0] if args and isinstance(args[0], dict) else kwargs.get('payload', {})
            context = {'job_id': str(job.job_id), 'stage': stage}
            for key in ('direction', 'query_type', 'date_from', 'date_to'):
                if key in payload:
                    context[key] = clean_text(payload[key])
            marker = _scope.set(context)
            emit('unit_started')
            try:
                result = function(self, job, *args, **kwargs)
                emit('unit_completed')
                return result
            except BaseException as error:
                chain, seen, current = [], set(), error
                while current is not None and id(current) not in seen and len(chain) < 12:
                    seen.add(id(current))
                    chain.append({'type': type(current).__name__, 'code': clean_text(getattr(current, 'code', ''))})
                    current = current.__cause__ or current.__context__
                frames = [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                          for frame in traceback.extract_tb(error.__traceback__)[-16:]]
                emit('unit_failed', exception_chain=chain, frames=frames)
                raise
            finally:
                _scope.reset(marker)
        return wrapped
    return decorate


def wire_request(session, method, url, **kwargs):
    started = time.monotonic()
    headers = kwargs.get('headers') or {}
    params = kwargs.get('params') or {}
    body = kwargs.get('json') or kwargs.get('data') or {}
    secrets = list(body.values()) if isinstance(body, dict) else []
    for name, value in headers.items():
        if name.lower() in {'authorization', 'cookie'}:
            secrets.extend([value, str(value).removeprefix('Bearer ')])
    safe_params = {key: clean_text(value, secrets) for key, value in params.items()
                   if key in {'sort', 'size', 'search', 'khhdon', 'shdon', 'khmshdon', 'type'}}
    for key in ('state', 'nbmst'):
        if params.get(key) is not None:
            safe_params[key + '_sha256'] = hashlib.sha256(str(params[key]).encode()).hexdigest()[:16]
    parts = urlsplit(url)
    fields = {
        'method': method.upper(), 'endpoint': parts.path,
        'host': parts.hostname, 'params': safe_params,
        'request_id': headers.get('request-id'), 'timeout': kwargs.get('timeout'),
        'route': 'proxy' if getattr(session, 'proxies', {}) else 'direct_or_environment',
        'request_headers': {k.lower(): clean_text(v, secrets) for k, v in headers.items()
                            if k.lower() in {'action', 'end-point', 'accept', 'accept-language', 'user-agent'}},
        'body_fields': sorted(body) if isinstance(body, dict) else [],
    }
    emit('http_started', **fields)
    try:
        response = getattr(session, method)(url, **kwargs)
    except Exception as error:
        emit('http_transport_error', **fields, elapsed_ms=round((time.monotonic()-started)*1000), error_type=type(error).__name__)
        raise
    extra = {'http_status': response.status_code,
             'elapsed_ms': round((time.monotonic()-started)*1000),
             'response_bytes': len(response.content),
             'response_headers': {k.lower(): clean_text(v, secrets) for k, v in response.headers.items()
                                  if k.lower() in {'content-type', 'retry-after', 'request-id', 'x-request-id', 'ratelimit-reset', 'x-ratelimit-reset'}}}
    if response.status_code < 400 and re.fullmatch(r'/api/(query|sco-query)/invoices/(purchase|sold)', parts.path):
        try:
            listing = response.json()
            if isinstance(listing, dict):
                extra['page_summary'] = {
                    'received': len(listing['datas']) if isinstance(listing.get('datas'), list) else None,
                    'total': listing.get('total') if isinstance(listing.get('total'), (int, float)) else None,
                    'has_next': listing.get('state') is not None,
                    'next_state_sha256': hashlib.sha256(str(listing['state']).encode()).hexdigest()[:16] if listing.get('state') is not None else None,
                }
        except (ValueError, TypeError):
            extra['error_type'] = 'invalid_list_json'
    if response.status_code >= 400:
        extra['response_sha256'] = hashlib.sha256(response.content).hexdigest()
        # Export only an upstream error message/code, never an arbitrary body,
        # successful login response, CAPTCHA or invoice JSON.
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            extra['upstream_error'] = {k: clean_text(payload[k], secrets) for k in ('message', 'code', 'errorCode')
                                       if isinstance(payload.get(k), (str, int))}
        else:
            extra['upstream_error'] = {'message': '[non-JSON body omitted]'}
    emit('http_finished', **fields, **extra)
    return response
