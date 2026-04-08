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
        return None  # already failed once, don't retry

    _init_attempted = True

    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        logger.warning("[firebase] FIREBASE_SERVICE_ACCOUNT not set — push disabled")
        return None

    logger.info("[firebase] FIREBASE_SERVICE_ACCOUNT found (%d chars)", len(raw))

    try:
        parsed = json.loads(raw)
        logger.info("[firebase] JSON parsed OK — project_id=%s", parsed.get("project_id", "???"))
        cred = credentials.Certificate(parsed)
        _app = firebase_admin.initialize_app(cred)
        logger.info("[firebase] Admin SDK initialised OK")
        return _app
    except json.JSONDecodeError as e:
        logger.error("[firebase] FIREBASE_SERVICE_ACCOUNT is not valid JSON: %s", e)
        return None
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
    logger.info("[firebase] send_push called — user=%s title=%r", user_id, title)

    if _init_firebase() is None:
        logger.warning("[firebase] send_push SKIPPED — SDK not initialised")
        return

    if db_engine is None:
        from app.db.engine import engine as _engine
        db_engine = _engine

    from sqlalchemy import text

    # Fetch all FCM tokens for this user
    try:
        async with db_engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT token FROM device_tokens WHERE user_id::text = :uid"),
                    {"uid": user_id},
                )
            ).scalars().all()
        logger.info("[firebase] Found %d token(s) for user %s", len(rows), user_id)
    except Exception:
        logger.exception("[firebase] Failed to fetch tokens for user %s", user_id)
        return

    if not rows:
        logger.warning("[firebase] No device tokens for user %s — push not sent", user_id)
        return

    # Build the data payload — FCM data values must be strings
    str_data = {k: str(v) for k, v in (data or {}).items()}

    for token in rows:
        try:
            logger.info("[firebase] Sending to token %s...", token[:20] + "...")
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
            resp = messaging.send(message)
            logger.info("[firebase] SUCCESS — message_id=%s", resp)
        except messaging.UnregisteredError:
            logger.warning("[firebase] Token STALE (UnregisteredError) — removing token")
            try:
                async with db_engine.begin() as conn:
                    await conn.execute(
                        text("DELETE FROM device_tokens WHERE token = :t"),
                        {"t": token},
                    )
            except Exception:
                pass
        except Exception:
            logger.exception("[firebase] FAILED to send to token %s", token[:20] + "...")
