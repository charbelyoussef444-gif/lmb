# app/api/routes/debug.py
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.core import log_buffer

router = APIRouter(tags=["debug"])

@router.get("/")
def root():
    return {"service": "LikeMinded API", "docs": "/docs", "health": "/healthz"}

@router.get("/healthz")
def healthz():
    return {"ok": True}

@router.get("/__debug/ping")
async def ping():
    try:
        async with engine.begin() as conn:
            v = (await conn.execute(text("select version()"))).scalar_one()
        return {"ok": True, "version": v}
    except Exception as e:
        raise HTTPException(500, f"ping failed: {e}")

@router.get("/__debug/whoami")
async def whoami(user_id: uuid.UUID = Depends(get_current_user_id)):
    return {"ok": True, "user_id": str(user_id)}

@router.get("/__debug/logs")
async def get_logs(
    limit: int = Query(200, ge=1, le=500),
    level: Optional[str] = Query(None),
    _: uuid.UUID = Depends(get_current_user_id),
):
    """Return recent backend log entries (auth required)."""
    return log_buffer.get_logs(limit=limit, level=level)

@router.delete("/__debug/logs")
async def clear_logs(_: uuid.UUID = Depends(get_current_user_id)):
    """Clear the in-memory log buffer."""
    log_buffer.clear()
    return {"ok": True}

@router.post("/__debug/test-push")
async def test_push(user_id: uuid.UUID = Depends(get_current_user_id)):
    """Send a test push notification to the current user."""
    import logging
    logger = logging.getLogger("uvicorn.error")
    logger.info("[debug] test-push called for user %s", str(user_id))
    try:
        from app.core.firebase import send_push
        logger.info("[debug] send_push imported OK")
        await send_push(
            user_id=str(user_id),
            title="Test Notification",
            body="If you see this, push notifications are working!",
            data={"type": "test", "channel_id": "messages"},
        )
        logger.info("[debug] send_push completed")
        return {"ok": True, "message": "Push sent — check your phone"}
    except Exception as e:
        logger.exception("[debug] test-push FAILED: %s", e)
        return {"ok": False, "error": str(e)}
