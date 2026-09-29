"""Request id and one access record per HTTP request (Phase 9, D55).

Every request gets a fresh uuid4 at HTTP entry. It is stored in
`request.state.request_id` (the /chat route passes it to the agent, which
passes it to every LLM call), in `request_id_var` (so every log line during
the request carries it) and in the `X-Request-ID` response header.

A client-supplied `X-Request-ID` is ignored: an id from outside could be forged
or reused to mix one request's log lines with another's, and nothing upstream
of this service needs to correlate with it (D55).

The access record has method, path (never the query string or the body),
status and duration. Liveness probes on /health get none, so Render's probe
does not flood the log.

Spring comparison: a OncePerRequestFilter that fills the MDC.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.observability.logs import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
QUIET_PATHS = frozenset({"/health"})

logger = logging.getLogger("app.http")


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    status = 500  # stays 500 if the route raises
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        if request.url.path not in QUIET_PATHS:
            logger.info(
                "http.request",
                extra={
                    "http.request.method": request.method,
                    "url.path": request.url.path,
                    "http.response.status_code": status,
                    "http.duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        request_id_var.reset(token)
