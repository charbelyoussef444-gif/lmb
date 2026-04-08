import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.types import ASGIApp

from app.db.engine import engine

# Core routes
from app.api.routes import debug, profile, conversations, photos
from app.api.routes.ws import ws_router
from app.api.routes import matching

# Me / KYC / Admin
from app.api.routes.me import router as me_router
from app.api.routes.me_kyc import router as me_kyc_router
from app.api.routes.admin_kyc import router as admin_kyc_router
from app.api.routes.admin_users import router as admin_users_router
from app.api.routes.admin_user_journey import router as admin_user_journey_router

# Chatbot / Traits / Events
from app.api.routes.chatbot import router as chatbot_router
from app.api.routes.traits import router as traits_router
from app.api.routes.events import router as events_router

# Assessment / Feedback / Reports
from app.api.routes.assesment import router as assessment_router
from app.api.routes import match_feedback
from app.api.routes import reports

# Device tokens (push notifications)
from app.api.routes.device_tokens import router as device_tokens_router

# Stats
from app.api.routes.stats import router as stats_router


from app.core import log_buffer
log_buffer.install()

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="LikeMinded API (MVP)")

# -------------------
# CORS CONFIG
# -------------------

ALLOWED_ORIGINS = {
    # Local dev
    "http://localhost:5173",
    "http://127.0.0.1:5173",

    # Production domains
    "https://likeminded.chat",
    "https://www.likeminded.chat",

    # Vercel production deployment
    "https://lmf-2tsh.vercel.app",
}


def is_allowed_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    if origin in ALLOWED_ORIGINS:
        return True
    if origin.endswith(".vercel.app"):
        return True
    return False


def _cors_headers_for_origin(origin: str | None) -> dict:
    """Return CORS headers if the origin is allowed, else empty dict."""
    if not origin or not is_allowed_origin(origin):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }


# -------------------
# THREAD-SAFE CORS MIDDLEWARE
# Avoids mutating shared state (self.allow_origins) on every request.
# Creates a fresh CORSMiddleware per-request for allowed origins.
# -------------------

class DynamicCORSMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            origin = (headers.get(b"origin") or b"").decode() or None

            if is_allowed_origin(origin):
                cors_app = CORSMiddleware(
                    self.app,
                    allow_origins=[origin] if origin else list(ALLOWED_ORIGINS),
                    allow_credentials=True,
                    allow_methods=["*"],
                    allow_headers=["*"],
                )
                await cors_app(scope, receive, send)
                return

        await self.app(scope, receive, send)


app.add_middleware(DynamicCORSMiddleware)


# -------------------
# GLOBAL EXCEPTION HANDLER
# Starlette's CORSMiddleware does NOT inject headers on 500 responses,
# so we add them manually here to prevent the browser seeing a CORS error
# when the real problem is a server crash.
# -------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin")
    logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=_cors_headers_for_origin(origin),
    )


# -------------------
# ROUTES
# -------------------

# Core
app.include_router(debug.router)
app.include_router(profile.router)
app.include_router(conversations.router)
app.include_router(photos.router)
app.include_router(ws_router)
app.include_router(matching.router)

# Me / KYC / Admin
app.include_router(me_router)
app.include_router(me_kyc_router)
app.include_router(admin_kyc_router)
app.include_router(admin_users_router)
app.include_router(admin_user_journey_router)

# Chatbot / Traits / Events
app.include_router(chatbot_router)
app.include_router(traits_router)
app.include_router(events_router)

# Assessment / Feedback / Reports
app.include_router(assessment_router)
app.include_router(match_feedback.router)
app.include_router(reports.router)

# Device tokens (push)
app.include_router(device_tokens_router)

# Stats
app.include_router(stats_router)


# -------------------
# STARTUP PROBE
# -------------------

@app.on_event("startup")
async def _startup_probe():
    logger.info("[startup] API starting…")
    logger.info("[startup] Allowed CORS origins: %s", list(ALLOWED_ORIGINS))

    try:
        logger.info("[startup] DB driver: %s", engine.url.drivername)
        async with engine.begin() as conn:
            v = (await conn.execute(text("select 1"))).scalar_one()
            logger.info("[startup] DB ping OK: %s", v)
    except Exception:
        logger.exception("[startup] DB ping FAILED (service will still run)")

    try:
        for r in app.routes:
            logger.info(
                "Route loaded: %s %s",
                getattr(r, "methods", None),
                getattr(r, "path", None),
            )
    except Exception:
        logger.exception("[startup] Failed listing routes")
