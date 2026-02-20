# app/main.py
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

# ✅ PRODUCTION CORS: explicit origins only
# Add/remove as needed.
ALLOWED_ORIGINS = [
    # Local dev
    "http://localhost:5173",
    "http://127.0.0.1:5173",

    # Your Vercel production domain (the stable one)
    "https://lmf-2tsh.vercel.app",

    # Your custom domain
    "https://likeminded.chat",
    "https://www.likeminded.chat",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
    # Log CORS
    logger.info("[startup] CORS origins: %s", ALLOWED_ORIGINS)

    # DB ping: should NOT kill the service if DB is down/misconfigured
    try:
        logger.info("[startup] DB driver: %s", engine.url.drivername)
        async with engine.begin() as conn:
            v = (await conn.execute(text("select 1"))).scalar_one()
            logger.info("[startup] DB ping OK: %s", v)
    except Exception:
        logger.exception("[startup] DB ping FAILED (service will still run)")

    # Log routes
    try:
        for r in app.routes:
            logger.info(
                "Route loaded: %s %s",
                getattr(r, "methods", None),
                getattr(r, "path", None),
            )
    except Exception:
        logger.exception("[startup] Failed listing routes")
