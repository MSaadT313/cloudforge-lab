import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


BACKENDS = [
    ("backend-a", httpx.URL("http://127.0.0.1:8001")),
    ("backend-b", httpx.URL("http://127.0.0.1:8002")),
]

FORWARD_DEADLINE_SECONDS = 3.0

BALANCING_POLICY = os.getenv(
    "BALANCING_POLICY",
    "round_robin"
).strip().lower()

if BALANCING_POLICY not in {"round_robin", "least_outstanding"}:
    raise ValueError(
        "BALANCING_POLICY must be 'round_robin' or 'least_outstanding'"
    )


HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"proxy-connection",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}

logger = logging.getLogger("uvicorn.error")


def filtered_headers(raw_headers, extra=()):
    blocked = HOP_HEADERS | set(extra)

    for name, value in raw_headers:
        if name.lower() == b"connection":
            blocked.update(
                token.strip().lower()
                for token in value.split(b",")
            )

    return [
        (name, value)
        for name, value in raw_headers
        if name.lower() not in blocked
    ]


def get_request_id(request: Request):
    incoming = request.headers.get("X-Request-ID")

    try:
        return str(UUID(incoming)) if incoming else str(uuid4())
    except ValueError:
        return str(uuid4())


async def select_backend(app: FastAPI):
    """
    Select a backend and register one outstanding request.

    The lock protects both selection and counter updates so
    concurrent requests cannot select using stale counter values.
    """
    async with app.state.selection_lock:

        if app.state.balancing_policy == "round_robin":
            index = app.state.next_backend
            app.state.next_backend = (index + 1) % len(BACKENDS)

        else:
            counters = app.state.outstanding_requests

            minimum = min(counters.values())

            # Find all backends with the lowest outstanding count.
            candidates = [
                index
                for index, (backend_id, _) in enumerate(BACKENDS)
                if counters[backend_id] == minimum
            ]

            # Rotate the tie-break starting position.
            start = app.state.tie_cursor

            index = next(
                candidate
                for offset in range(len(BACKENDS))
                for candidate in [
                    (start + offset) % len(BACKENDS)
                ]
                if candidate in candidates
            )

            app.state.tie_cursor = (index + 1) % len(BACKENDS)

        backend_id, base_url = BACKENDS[index]

        # Register the request before releasing the lock.
        app.state.outstanding_requests[backend_id] += 1

        outstanding = app.state.outstanding_requests[backend_id]

        return backend_id, base_url, outstanding


async def release_backend(app: FastAPI, backend_id: str):
    """Release one outstanding-request slot."""
    async with app.state.selection_lock:
        app.state.outstanding_requests[backend_id] -= 1

        # Defensive protection against negative counters.
        if app.state.outstanding_requests[backend_id] < 0:
            app.state.outstanding_requests[backend_id] = 0
            logger.error(
                "negative_outstanding_counter backend=%s",
                backend_id,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.balancing_policy = BALANCING_POLICY

    app.state.next_backend = 0
    app.state.tie_cursor = 0

    app.state.selection_lock = asyncio.Lock()

    app.state.outstanding_requests = {
        backend_id: 0
        for backend_id, _ in BACKENDS
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(3.0, connect=1.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        app.state.client = client

        logger.info(
            "gateway_started policy=%s backends=%s",
            app.state.balancing_policy,
            list(app.state.outstanding_requests.keys()),
        )

        yield


app = FastAPI(
    title="CloudForge Gateway",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def forward(request: Request):
    request_id = get_request_id(request)

    raw_path = request.scope["raw_path"]

    if request.scope["query_string"]:
        raw_path += b"?" + request.scope["query_string"]

    request_body = await request.body()

    backend_id, base_url, outstanding_at_dispatch = (
        await select_backend(request.app)
    )

    target_url = base_url.copy_with(raw_path=raw_path)

    headers = filtered_headers(
        request.headers.raw,
        extra=(b"host", b"content-length", b"x-request-id"),
    )

    headers.append(
        (b"x-request-id", request_id.encode("ascii"))
    )

    upstream_request = httpx.Request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=request_body,
    )

    started = time.perf_counter()

    try:
        async with asyncio.timeout(FORWARD_DEADLINE_SECONDS):
            upstream = await request.app.state.client.send(
                upstream_request
            )

        response = Response(
            content=upstream.content,
            status_code=upstream.status_code,
        )

        response.raw_headers.extend(
            filtered_headers(
                upstream.headers.raw,
                extra=(
                    b"content-length",
                    b"content-encoding",
                    b"x-request-id",
                    b"x-backend-id",
                ),
            )
        )

    except (TimeoutError, httpx.TimeoutException):
        response = JSONResponse(
            status_code=504,
            content={"detail": "Backend response timed out"},
        )

    except httpx.RequestError:
        response = JSONResponse(
            status_code=502,
            content={"detail": "Could not communicate with backend"},
        )

    finally:
        await release_backend(request.app, backend_id)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Backend-ID"] = backend_id

    elapsed_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Gateway-Time-Ms"] = f"{elapsed_ms:.2f}"

    logger.info(
        "gateway request_id=%s policy=%s backend=%s "
        "outstanding_at_dispatch=%s method=%s path=%s "
        "status=%s duration_ms=%.2f",
        request_id,
        request.app.state.balancing_policy,
        backend_id,
        outstanding_at_dispatch,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )

    return response
