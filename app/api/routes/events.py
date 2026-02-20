import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from postgrest.exceptions import APIError

from app.deps.auth import get_current_user_id
from app.services.supabase import sb  # service-role client

router = APIRouter(prefix="/events", tags=["events"])

ALLOWED_EVENTS = {
    "signup_started",
    "signup_completed",
    "assessment_started",
    "assessment_completed",
    "match_shown",
    "match_opened",
    "message_sent",
    "message_received",
    "paywall_viewed",
    "subscribe_clicked",
    "subscription_active",
    "app_opened",  # optional, if you want it
}

MAX_PROPERTIES_BYTES = 16_000


class EventIn(BaseModel):
    # client-generated unique id for dedupe
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)

    session_id: uuid.UUID
    event_name: str = Field(..., min_length=1, max_length=80)
    ts: Optional[datetime] = None

    # promoted foreign keys (optional depending on event)
    conversation_id: Optional[uuid.UUID] = None
    message_id: Optional[uuid.UUID] = None
    other_user_id: Optional[uuid.UUID] = None

    properties: Dict[str, Any] = Field(default_factory=dict)


def _json_size(d: Dict[str, Any]) -> int:
    try:
        import json

        return len(json.dumps(d, ensure_ascii=False))
    except Exception:
        return 0


@router.post("")
async def ingest_event(
    payload: EventIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    name = (payload.event_name or "").strip()

    print(f"[events] ingest hit name={name} user_id={user_id} session_id={payload.session_id}")

    if name not in ALLOWED_EVENTS:
        raise HTTPException(400, f"Unknown event_name: {name}")

    props = payload.properties or {}
    if _json_size(props) > MAX_PROPERTIES_BYTES:
        raise HTTPException(413, "properties too large")

    row = {
        "event_id": str(payload.event_id),
        "user_id": str(user_id),  # always from JWT
        "session_id": str(payload.session_id),
        "event_name": name,
        "properties": props,
        "conversation_id": str(payload.conversation_id) if payload.conversation_id else None,
        "message_id": str(payload.message_id) if payload.message_id else None,
        "other_user_id": str(payload.other_user_id) if payload.other_user_id else None,
    }

    if payload.ts:
        row["ts"] = payload.ts.isoformat()

    # remove nulls so DB defaults & cleaner rows
    row = {k: v for k, v in row.items() if v is not None}

    # IMPORTANT:
    # With your versions (supabase==2.6.0, postgrest==0.16.11), maybe_single() can lead to execute()->None on 0 rows.
    # Use select + limit(1) instead.
    try:
        resp = (
            sb.postgrest
            .from_("events")
            .select("id")
            .eq("event_id", str(payload.event_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
    except APIError as e:
        raise HTTPException(502, f"Supabase select failed: {e}")

    if resp is None:
        raise HTTPException(502, "Supabase returned no response for select()")

    existing = resp.data or []
    if len(existing) > 0:
        print(f"[events] deduped event_id={payload.event_id} user_id={user_id}")
        return {"ok": True, "deduped": True}

    try:
        ins = sb.postgrest.from_("events").insert(row).execute()
    except APIError as e:
        raise HTTPException(502, f"Supabase insert failed: {e}")

    if ins is None:
        raise HTTPException(502, "Supabase returned no response for insert()")

    print(f"[events] inserted event_name={name} event_id={payload.event_id} user_id={user_id}")
    return {"ok": True, "deduped": False}
