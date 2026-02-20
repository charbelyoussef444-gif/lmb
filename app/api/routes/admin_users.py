# app/api/routes/admin_users.py
import uuid
from fastapi import APIRouter, Depends
from app.deps.admin import require_admin
from app.services.supabase import sb  # service-role client

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("")
async def list_users(
    _: uuid.UUID = Depends(require_admin),
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    db = sb.postgrest
    req = (
        db.from_("users")
        .select("id,full_name,verified,banned,admin,created_at")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if q:
        req = req.ilike("full_name", f"%{q}%")
    return req.execute().data


@router.post("/{target_user_id}/ban")
async def ban_user(target_user_id: uuid.UUID, _: uuid.UUID = Depends(require_admin)):
    sb.postgrest.from_("users").update({"banned": True}).eq("id", str(target_user_id)).execute()
    return {"ok": True}


@router.post("/{target_user_id}/unban")
async def unban_user(target_user_id: uuid.UUID, _: uuid.UUID = Depends(require_admin)):
    sb.postgrest.from_("users").update({"banned": False}).eq("id", str(target_user_id)).execute()
    return {"ok": True}


@router.post("/{target_user_id}/promote")
async def make_admin(target_user_id: uuid.UUID, _: uuid.UUID = Depends(require_admin)):
    sb.postgrest.from_("users").update({"admin": True}).eq("id", str(target_user_id)).execute()
    return {"ok": True}


@router.post("/{target_user_id}/demote")
async def revoke_admin(target_user_id: uuid.UUID, _: uuid.UUID = Depends(require_admin)):
    sb.postgrest.from_("users").update({"admin": False}).eq("id", str(target_user_id)).execute()
    return {"ok": True}
