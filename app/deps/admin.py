# app/deps/admin.py
import uuid
from fastapi import Depends, HTTPException
from app.deps.auth import get_current_user_id
from app.services.supabase import sb  # service-role client

def require_admin(user_id: uuid.UUID = Depends(get_current_user_id)) -> uuid.UUID:
    user = (
        sb.postgrest
        .from_("users")
        .select("admin")
        .eq("id", str(user_id))
        .single()
        .execute()
        .data
    )
    if not user or not user.get("admin"):
        raise HTTPException(403, "Admins only")
    return user_id
