# app/api/routes/admin_user_journey.py

import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from app.db.engine import engine
from app.deps.auth import get_current_user_id

router = APIRouter(prefix="/admin/users", tags=["admin"])
logger = logging.getLogger(__name__)

FLAG_EVENTS = {
    "signup_completed",
    "assessment_completed",
    "match_opened",
    "message_sent",
    "subscription_active",
}

# ✅ Only events that mean "the user did something"
# (prevents "someone matched with him" from counting as his activity if you log those to his user_id)
ACTIVITY_EVENTS = [
    "signup_completed",
    "assessment_started",
    "assessment_completed",
    "match_opened",
    "message_sent",
]

INACTIVITY_BAN_DAYS = 7


async def _require_admin(conn, user_id: uuid.UUID):
    row = (
        await conn.execute(
            text("select admin from users where id = :uid"),
            {"uid": str(user_id)},
        )
    ).mappings().first()

    if not row:
        raise HTTPException(401, "Unauthorized")
    if not row.get("admin"):
        raise HTTPException(403, "Forbidden")


@router.get("/{target_user_id}/summary")
async def admin_user_summary(
    target_user_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    async with engine.begin() as conn:
        await _require_admin(conn, user_id)

        u = (
            await conn.execute(
                text(
                    """
                    select
                        id::text as id,
                        full_name,
                        verified,
                        banned,
                        admin,
                        created_at,
                        gender,
                        age,
                        location,
                        gender_pref,
                        preferred_min_age,
                        preferred_max_age,
                        last_login_at
                    from users
                    where id = :tid
                    """
                ),
                {"tid": str(target_user_id)},
            )
        ).mappings().first()

        if not u:
            raise HTTPException(404, "User not found")

        u = dict(u)

        # ✅ Latest user-driven event (this is what you want to show)
        latest_activity_stmt = text(
            """
            select event_name, ts
            from events
            where user_id = :tid
              and event_name = any(:activity_events)
            order by ts desc
            limit 1
            """
        ).bindparams(
            bindparam("activity_events", type_=ARRAY(TEXT))
        )

        latest_activity_row = (
            await conn.execute(
                latest_activity_stmt,
                {"tid": str(target_user_id), "activity_events": ACTIVITY_EVENTS},
            )
        ).mappings().first()

        # We'll keep the existing "last_active" field for your UI,
        # but now it means: last user-driven event timestamp.
        last_active = None
        last_active_event = None

        if latest_activity_row and latest_activity_row.get("ts") is not None:
            last_active = latest_activity_row["ts"].isoformat()
            last_active_event = latest_activity_row.get("event_name")

        # If user never had any activity event, fall back to created_at
        if last_active is None and u.get("created_at") is not None:
            last_active = u["created_at"].isoformat()
            last_active_event = "created_at_fallback"

        # ✅ Optional: keep your inactivity auto-ban, but base it on last user-driven event
        # (fallback to created_at)
        ban_stmt = text(
            f"""
            with computed as (
              select
                id,
                admin,
                banned,
                created_at,
                coalesce(
                  (
                    select max(ts)
                    from events
                    where user_id = :tid
                      and event_name = any(:activity_events)
                  ),
                  created_at
                ) as last_active_ts
              from users
              where id = :tid
            )
            update users u
            set banned = true
            from computed c
            where u.id = c.id
              and c.admin is not true
              and u.banned is not true
              and c.last_active_ts is not null
              and (now() - c.last_active_ts) >= interval '{INACTIVITY_BAN_DAYS} days'
            returning u.id
            """
        ).bindparams(
            bindparam("activity_events", type_=ARRAY(TEXT))
        )

        await conn.execute(
            ban_stmt,
            {"tid": str(target_user_id), "activity_events": ACTIVITY_EVENTS},
        )

        # refresh banned (may have changed)
        banned_row = (
            await conn.execute(
                text("select banned from users where id = :tid"),
                {"tid": str(target_user_id)},
            )
        ).mappings().first()
        if banned_row is not None:
            u["banned"] = banned_row.get("banned")

        active_chats_row = (
            await conn.execute(
                text(
                    """
                    select count(*)::int as active_chats
                    from conversations
                    where active = true
                      and (user_a_id = :tid or user_b_id = :tid)
                      and type != 'onboarding_bot'
                    """
                ),
                {"tid": str(target_user_id)},
            )
        ).mappings().first()
        active_chats = int(active_chats_row["active_chats"]) if active_chats_row else 0

        flags = {}
        for ev in FLAG_EVENTS:
            r = (
                await conn.execute(
                    text(
                        """
                        select 1
                        from events
                        where user_id = :tid
                          and event_name = :ev
                        limit 1
                        """
                    ),
                    {"tid": str(target_user_id), "ev": ev},
                )
            ).first()
            flags[ev] = bool(r)

        latest_rows = (
            await conn.execute(
                text(
                    """
                    select event_name, ts
                    from events
                    where user_id = :tid
                    order by ts desc
                    limit 8
                    """
                ),
                {"tid": str(target_user_id)},
            )
        ).mappings().all()

        latest_events = [
            {"event_name": r["event_name"], "ts": r["ts"].isoformat()}
            for r in latest_rows
            if r.get("ts") is not None
        ]

    return {
        "user": {
            "id": u["id"],
            "full_name": u.get("full_name"),
            "verified": u.get("verified"),
            "banned": u.get("banned"),
            "admin": u.get("admin"),
            "created_at": u["created_at"].isoformat() if u.get("created_at") else None,
            # ✅ This is now "latest user-driven event time"
            "last_active": last_active,
            # ✅ Helpful to display/debug what counted as last_active
            "last_active_event": last_active_event,
            "gender": u.get("gender"),
            "age": u.get("age"),
            "location": u.get("location"),
            "gender_pref": u.get("gender_pref"),
            "preferred_min_age": u.get("preferred_min_age"),
            "preferred_max_age": u.get("preferred_max_age"),
            "active_chats": active_chats,
        },
        "flags": {
            "signup_completed": flags.get("signup_completed", False),
            "assessment_completed": flags.get("assessment_completed", False),
            "match_opened": flags.get("match_opened", False),
            "message_sent": flags.get("message_sent", False),
            "subscription_active": flags.get("subscription_active", False),
        },
        "latest_events": latest_events,
    }


@router.get("/{target_user_id}/events")
async def admin_user_events(
    target_user_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event: Optional[str] = Query(None),
    since: Optional[str] = Query(None),  # ISO
    until: Optional[str] = Query(None),  # ISO
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    async with engine.begin() as conn:
        await _require_admin(conn, user_id)

        where = ["user_id = :tid"]
        params = {"tid": str(target_user_id), "limit": limit, "offset": offset}

        if event:
            where.append("event_name = :event_name")
            params["event_name"] = event

        if since:
            where.append("ts >= :since_ts")
            params["since_ts"] = since

        if until:
            where.append("ts <= :until_ts")
            params["until_ts"] = until

        where_sql = " and ".join(where)

        rows = (
            await conn.execute(
                text(
                    f"""
                    select id, ts, event_name, session_id::text as session_id, properties
                    from events
                    where {where_sql}
                    order by ts desc
                    limit :limit
                    offset :offset
                    """
                ),
                params,
            )
        ).mappings().all()

    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r.get("ts") else None,
            "event_name": r["event_name"],
            "session_id": r.get("session_id"),
            "properties": r.get("properties") or {},
        }
        for r in rows
    ]
