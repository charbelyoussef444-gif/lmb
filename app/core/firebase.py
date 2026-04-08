# app/core/firebase.py
"""
Firebase Admin SDK initialisation + push-notification helper.

The service-account JSON is read from the env var FIREBASE_SERVICE_ACCOUNT.
If missing, FCM calls are silently skipped (dev-mode fallback).
"""
import json
import logging
import os
from typing import Optional

import firebase_admin                       # type: ignore
from firebase_admin import credentials, messaging  # type: ignore

logger = logging.getLogger("uvicorn.error")

_app: Optional[firebase_admin.App] = None
_init_attempted = False


def _init_firebase() -> Optional[firebase_admin.App]:
    """Lazy-init: called once on first send."""
    global _app, _init_attempted

    if _app is not None:
        return _app

    if _init_attempted:
        return None

    _init_attempted = True

    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        logger.warning("[firebase] FIREBASE_SERVICE_ACCOUNT not set — push disabled")
        return None

    try:
        cred = credentials.Certificate(json.loads(raw))
        _app = firebase_admin.initialize_app(cred)
        logger.info("[firebase] Admin SDK initialised OK")
        return _app
    except Exception:
        logger.exception("[firebase] Failed to initialise Admin SDK")
        return None


async def send_push(
    user_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    *,
    db_engine=None,
) -> None:
    """
    Send an FCM push notification to all registered devices of a user.
    """
    if _init_firebase() is None:
        return

    if db_engine is None:
        from app.db.engine import engine as _engine
        db_engine = _engine

    from sqlalchemy import text

    try:
        async with db_engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT token FROM device_tokens WHERE user_id::text = :uid"),
                    {"uid": user_id},
                )
            ).scalars().all()
    except Exception:
        logger.exception("[firebase] Failed to fetch tokens for user %s", user_id)
        return

    if not rows:
        return

    str_data = {k: str(v) for k, v in (data or {}).items()}

    for token in rows:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=str_data,
                token=token,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id=str_data.get("channel_id", "messages"),
                    ),
                ),
            )
            messaging.send(message)
        except messaging.UnregisteredError:
            try:
                async with db_engine.begin() as conn:
                    await conn.execute(
                        text("DELETE FROM device_tokens WHERE token = :t"),
                        {"t": token},
                    )
            except Exception:
                pass
        except Exception:
            logger.exception("[firebase] Failed to send to token for user %s", user_id)
