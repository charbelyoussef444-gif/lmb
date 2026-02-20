import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from app.deps.auth import get_current_user_id
from app.services.supabase import sb

router = APIRouter(prefix="/me", tags=["me"])


@router.post("/ping")
async def ping_me(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Called once per tab/session when the app opens.
    Updates users.last_login_at.
    """
    now = datetime.now(timezone.utc).isoformat()

    try:
        resp = (
            sb.postgrest
            .from_("users")          # ✅ CORRECT TABLE
            .update({"last_login_at": now})
            .eq("id", str(user_id))  # ✅ users.id
            .execute()
        )
    except APIError as e:
        raise HTTPException(502, f"Supabase update failed: {e}")

    if resp is None:
        raise HTTPException(502, "Supabase returned no response for update()")

    return {"ok": True, "last_login_at": now}
