# app/api/routes/device_tokens.py
"""
Register / unregister FCM device tokens for push notifications.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id

router = APIRouter(prefix="/me", tags=["device-tokens"])


class DeviceTokenIn(BaseModel):
    token: str
    platform: str = "android"  # "android" | "ios"


@router.post("/device-token", status_code=201)
async def register_device_token(
    body: DeviceTokenIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Register (or refresh) an FCM token for push notifications."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(400, "Token is required")

    platform = (body.platform or "android").strip().lower()
    if platform not in ("android", "ios"):
        raise HTTPException(400, "Platform must be 'android' or 'ios'")

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO device_tokens (user_id, token, platform, updated_at)
                VALUES (:uid, :token, :platform, now())
                ON CONFLICT (user_id, token)
                DO UPDATE SET platform = EXCLUDED.platform, updated_at = now()
                """
            ),
            {"uid": str(user_id), "token": token, "platform": platform},
        )

    return {"ok": True}


@router.delete("/device-token", status_code=200)
async def unregister_device_token(
    body: DeviceTokenIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Remove an FCM token (on sign-out)."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(400, "Token is required")

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM device_tokens WHERE user_id::text = :uid AND token = :token"
            ),
            {"uid": str(user_id), "token": token},
        )

    return {"ok": True}
