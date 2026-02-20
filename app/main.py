import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

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

# Stats
from app.api.routes.stats import router as stats_router


logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="LikeMinded API (MVP)")

# -------------------
# CORS CONFIG
# -------------------

# Exact allowed origins
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
    """
    Dynamic origin validation.

    Allows:
    ✔ production domains
    ✔ Vercel preview deployments
    ✔ local dev
    ✔ requests with no origin (health checks, server-to-server)
    """
    if origin is None:
        return True

    if origin in ALLOWED_ORIGINS:
        return True

    # allow Vercel preview deployments safely
    if origin.endswith(".vercel.app"):
        return True

    return False


class DynamicCORSMiddleware(CORSMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            origin = headers.get(b"origin")

            if origin:
                origin = origin.decode()

                if is_allowed_origin(origin):
                    self.allow_origins = [origin]

        await super().__call__(scope, receive, send)


app.add_middleware(
    DynamicCORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# Stats
app.include_router(stats_router)

# -------------------
# STARTUP PROBE
# -------------------

@app.on_event("startup")
async def _startup_probe():
    logger.info("[startup] API starting…")

    # Log CORS origins
    logger.info("[startup] Allowed CORS origins: %s", list(ALLOWED_ORIGINS))

    # DB ping (non-fatal)
    try:
        logger.info("[startup] DB driver: %s", engine.url.drivername)
        async with engine.begin() as conn:
            v = (await conn.execute(text("select 1"))).scalar_one()
            logger.info("[startup] DB ping OK: %s", v)
    except Exception:
        logger.exception("[startup] DB ping FAILED (service will still run)")

    # List loaded routes (helps debug deployment issues)
    try:
        for r in app.routes:
            logger.info(
                "Route loaded: %s %s",
                getattr(r, "methods", None),
                getattr(r, "path", None),
            )
    except Exception:
        logger.exception("[startup] Failed listing routes")
