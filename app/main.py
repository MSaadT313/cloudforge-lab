import logging
import os
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .database import Base, engine
from .routers import users

# Preserve your existing table initialization.
Base.metadata.create_all(bind=engine)

BACKEND_ID = os.getenv("BACKEND_ID", "backend-local")
logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="CloudForge Backend API",
    version="1.1.0",
)

app.include_router(users.router)


@app.middleware("http")
async def trace_request(request: Request, call_next):
    # Preserve a valid incoming UUID so the gateway and backend
    # can use the same request identifier.
    incoming_id = request.headers.get("X-Request-ID")

    try:
        request_id = str(UUID(incoming_id)) if incoming_id else str(uuid4())
    except ValueError:
        request_id = str(uuid4())

    request.state.request_id = request_id
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed backend=%s request_id=%s",
            BACKEND_ID,
            request_id,
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    elapsed_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Backend-ID"] = BACKEND_ID
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"

    logger.info(
        "backend=%s request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        BACKEND_ID,
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )

    return response


@app.get("/")
def root():
    return {
        "message": "CloudForge backend is running",
        "backend_id": BACKEND_ID,
    }


@app.get("/health/live", tags=["Health"])
def liveness():
    return {
        "status": "alive",
        "backend_id": BACKEND_ID,
    }


@app.get("/health/ready", tags=["Health"])
def readiness():
    try:
        with engine.connect() as connection:
            # Checks database connectivity and access to our workload table.
            # An empty users table is also a successful result.
            connection.execute(
                text("SELECT id FROM users LIMIT 1")
            ).first()

    except SQLAlchemyError:
        logger.warning("readiness_failed backend=%s", BACKEND_ID)

        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "backend_id": BACKEND_ID,
                "database": "unavailable",
            },
        )

    return {
        "status": "ready",
        "backend_id": BACKEND_ID,
        "database": "available",
    }