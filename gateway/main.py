import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


BACKENDS = [
    (
        "backend-a",
        httpx.URL(
            os.getenv("BACKEND_A_URL", "http://127.0.0.1:8001")
        ),
    ),
    (
        "backend-b",
        httpx.URL(
            os.getenv("BACKEND_B_URL", "http://127.0.0.1:8002")
        ),
    ),
]

FORWARD_DEADLINE_SECONDS = 3.0

HEALTH_CHECK_INTERVAL_SECONDS = 2.0
HEALTH_CHECK_TIMEOUT_SECONDS = 1.0
FAILURE_THRESHOLD = 2
RECOVERY_THRESHOLD = 2

BALANCING_POLICY = os.getenv(
    "BALANCING_POLICY",
    "round_robin",
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


async def check_backend_health(
    app: FastAPI,
    backend_id: str,
    base_url: httpx.URL,
    initial: bool = False,
):
    """Probe a backend and update its health state."""

    healthy = False
    status_code = None

    try:
        timeout = httpx.Timeout(HEALTH_CHECK_TIMEOUT_SECONDS)

        response = await app.state.client.get(
            base_url.join("/health/ready"),
            timeout=timeout,
        )

        status_code = response.status_code
        healthy = response.status_code == 200

    except (httpx.RequestError, TimeoutError):
        healthy = False

    async with app.state.selection_lock:
        state = app.state.backend_health[backend_id]

        state["last_check"] = time.time()
        state["last_status_code"] = status_code

        if initial:
            state["healthy"] = healthy
            state["consecutive_failures"] = 0 if healthy else 1
            state["consecutive_successes"] = 1 if healthy else 0

        elif healthy:
            state["consecutive_failures"] = 0
            state["consecutive_successes"] += 1

            if (
                not state["healthy"]
                and state["consecutive_successes"] >= RECOVERY_THRESHOLD
            ):
                state["healthy"] = True

                logger.info(
                    "backend_recovered backend=%s",
                    backend_id,
                )

        else:
            state["consecutive_successes"] = 0
            state["consecutive_failures"] += 1

            if (
                state["healthy"]
                and state["consecutive_failures"] >= FAILURE_THRESHOLD
            ):
                state["healthy"] = False

                logger.warning(
                    "backend_marked_unhealthy backend=%s",
                    backend_id,
                )

        current_health = state["healthy"]

    logger.info(
        "health_check backend=%s healthy=%s status_code=%s "
        "consecutive_failures=%s consecutive_successes=%s",
        backend_id,
        current_health,
        status_code,
        state["consecutive_failures"],
        state["consecutive_successes"],
    )


async def health_monitor(app: FastAPI):
    """Periodically check every configured backend."""

    while True:
        await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)

        await asyncio.gather(
            *(
                check_backend_health(app, backend_id, base_url)
                for backend_id, base_url in BACKENDS
            )
        )


async def select_backend(app: FastAPI, excluded_backend_ids=None):
    """
    Select a healthy, non-excluded backend and register one outstanding request.

    Selection and counter updates happen under the same lock.
    """

    excluded_backend_ids = set(excluded_backend_ids or ())

    async with app.state.selection_lock:

        healthy_indices = [
            index
            for index, (backend_id, _) in enumerate(BACKENDS)
            if (
                app.state.backend_health[backend_id]["healthy"]
                and backend_id not in excluded_backend_ids
            )
        ]

        if not healthy_indices:
            return None

        if app.state.balancing_policy == "round_robin":

            start = app.state.next_backend

            index = next(
                (
                    (start + offset) % len(BACKENDS)
                    for offset in range(len(BACKENDS))
                    if (start + offset) % len(BACKENDS)
                    in healthy_indices
                )
            )

            app.state.next_backend = (index + 1) % len(BACKENDS)

        else:
            counters = app.state.outstanding_requests

            minimum = min(
                counters[BACKENDS[index][0]]
                for index in healthy_indices
            )

            candidates = [
                index
                for index in healthy_indices
                if counters[BACKENDS[index][0]] == minimum
            ]

            start = app.state.tie_cursor

            index = next(
                (
                    (start + offset) % len(BACKENDS)
                    for offset in range(len(BACKENDS))
                    if (start + offset) % len(BACKENDS)
                    in candidates
                )
            )

            app.state.tie_cursor = (index + 1) % len(BACKENDS)

        backend_id, base_url = BACKENDS[index]

        app.state.outstanding_requests[backend_id] += 1

        outstanding = app.state.outstanding_requests[backend_id]

        return backend_id, base_url, outstanding


async def release_backend(app: FastAPI, backend_id: str):
    """Release one outstanding-request slot."""

    async with app.state.selection_lock:
        app.state.outstanding_requests[backend_id] -= 1

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

    app.state.backend_health = {
        backend_id: {
            "healthy": False,
            "consecutive_failures": 0,
            "consecutive_successes": 0,
            "last_check": None,
            "last_status_code": None,
        }
        for backend_id, _ in BACKENDS
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(3.0, connect=1.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:

        app.state.client = client

        logger.info(
            "gateway_starting policy=%s backends=%s",
            app.state.balancing_policy,
            list(app.state.outstanding_requests.keys()),
        )

        # Perform initial probes before accepting traffic.
        await asyncio.gather(
            *(
                check_backend_health(
                    app,
                    backend_id,
                    base_url,
                    initial=True,
                )
                for backend_id, base_url in BACKENDS
            )
        )

        logger.info(
            "gateway_initial_health=%s",
            {
                backend_id: state["healthy"]
                for backend_id, state in app.state.backend_health.items()
            },
        )

        monitor_task = asyncio.create_task(
            health_monitor(app),
            name="cloudforge-health-monitor",
        )

        try:
            yield

        finally:
            monitor_task.cancel()

            with suppress(asyncio.CancelledError):
                await monitor_task

            logger.info("gateway_stopped")


app = FastAPI(
    title="CloudForge Gateway",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/gateway/status")
async def gateway_status(request: Request):

    async with request.app.state.selection_lock:

        return {
            "policy": request.app.state.balancing_policy,
            "backends": {
                backend_id: {
                    **state,
                    "outstanding_requests": (
                        request.app.state.outstanding_requests[backend_id]
                    ),
                }
                for backend_id, state
                in request.app.state.backend_health.items()
            },
        }


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

    method = request.method.upper()
    retry_allowed = method in {"GET", "HEAD"}
    max_attempts = 2 if retry_allowed else 1

    started = time.perf_counter()
    deadline = started + FORWARD_DEADLINE_SECONDS

    excluded_backend_ids = set()
    selected_backend_id = None
    successful_backend_id = None
    outstanding_at_dispatch = None
    response = None
    attempts = 0

    try:
        async with asyncio.timeout(FORWARD_DEADLINE_SECONDS):

            while attempts < max_attempts:

                selection = await select_backend(
                    request.app,
                    excluded_backend_ids=excluded_backend_ids,
                )

                if selection is None:

                    if attempts == 0:
                        response = JSONResponse(
                            status_code=503,
                            content={
                                "detail": "No healthy backends available"
                            },
                        )
                    else:
                        response = JSONResponse(
                            status_code=502,
                            content={
                                "detail": (
                                    "Backend connection failed; "
                                    "no alternate healthy backend available"
                                )
                            },
                        )

                    break

                backend_id, base_url, outstanding_at_dispatch = selection

                selected_backend_id = backend_id
                excluded_backend_ids.add(backend_id)
                attempts += 1

                try:
                    remaining = deadline - time.perf_counter()

                    if remaining <= 0:
                        raise TimeoutError

                    target_url = base_url.copy_with(raw_path=raw_path)

                    headers = filtered_headers(
                        request.headers.raw,
                        extra=(
                            b"host",
                            b"content-length",
                            b"x-request-id",
                        ),
                    )

                    headers.append(
                        (b"x-request-id", request_id.encode("ascii"))
                    )

                    timeout = httpx.Timeout(
                        remaining,
                        connect=min(1.0, remaining),
                    )

                    upstream_request = (
                        request.app.state.client.build_request(
                            method=method,
                            url=target_url,
                            headers=headers,
                            content=request_body,
                            timeout=timeout,
                        )
                    )

                    upstream = await request.app.state.client.send(
                        upstream_request
                    )

                    response = Response(
                        content=upstream.content,
                        status_code=upstream.status_code,
                    )

                    successful_backend_id = backend_id

                    response.raw_headers.extend(
                        filtered_headers(
                            upstream.headers.raw,
                            extra=(
                                b"content-length",
                                b"content-encoding",
                                b"x-request-id",
                                b"x-backend-id",
                                b"date",
                                b"server",
                            ),
                        )
                    )

                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:

                    should_retry = (
                        retry_allowed and attempts < max_attempts
                    )

                    logger.warning(
                        "backend_connection_failure request_id=%s "
                        "backend=%s attempt=%s retry=%s error=%s",
                        request_id,
                        backend_id,
                        attempts,
                        should_retry,
                        type(exc).__name__,
                    )

                    if not should_retry:
                        response = JSONResponse(
                            status_code=502,
                            content={
                                "detail": (
                                    "Could not establish connection "
                                    "to backend"
                                )
                            },
                        )

                except httpx.TimeoutException:

                    response = JSONResponse(
                        status_code=504,
                        content={
                            "detail": "Backend response timed out"
                        },
                    )

                except httpx.RequestError:

                    response = JSONResponse(
                        status_code=502,
                        content={
                            "detail": "Could not communicate with backend"
                        },
                    )

                finally:
                    await release_backend(
                        request.app,
                        backend_id,
                    )

                if response is not None:
                    break

                # A connection failure occurred on an eligible read.
                # The next iteration can select only another backend.

    except TimeoutError:

        response = JSONResponse(
            status_code=504,
            content={"detail": "Gateway forwarding deadline exceeded"},
        )

    response.headers["X-Request-ID"] = request_id

    if successful_backend_id is not None:
        response.headers["X-Backend-ID"] = successful_backend_id

    elapsed_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Gateway-Time-Ms"] = f"{elapsed_ms:.2f}"

    logger.info(
        "gateway request_id=%s policy=%s "
        "backend_attempted=%s backend_responded=%s "
        "outstanding_at_dispatch=%s attempts=%s "
        "method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.app.state.balancing_policy,
        selected_backend_id,
        successful_backend_id,
        outstanding_at_dispatch,
        attempts,
        method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )

    return response
