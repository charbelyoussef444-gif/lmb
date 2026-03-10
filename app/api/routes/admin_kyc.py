# app/api/routes/admin_kyc.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel

from app.deps.admin import require_admin
from app.services.storage import list_prefix, signed_url, delete_paths
from app.services.supabase import sb  # service-role client
from app.services.mail import send_email

router = APIRouter(prefix="/admin/kyc", tags=["admin-kyc"])


def _approved_email_text(full_name: str | None) -> str:
    name = (full_name or "there").strip()
    return f"""Hello {name},

Your LikeMinded verification has been approved.

Your account is now verified and ready to use.

— LikeMinded
"""


def _rejected_email_text(full_name: str | None, reason: str) -> str:
    name = (full_name or "there").strip()
    return f"""Hello {name},

Your LikeMinded verification was rejected.

Reason:
{reason}

Please go back to the app and upload new verification images.

— LikeMinded
"""


@router.get("/pending")
async def list_pending(_: uuid.UUID = Depends(require_admin)):
    db = sb.postgrest
    rows = (
        db.from_("kyc_verification")
        .select("user_id,created_at,users(full_name,age)")
        .order("created_at", desc=True)
        .execute()
        .data
    )

    result = []
    for r in rows:
        user_info = r.get("users") or {}
        result.append(
            {
                "user_id": r["user_id"],
                "created_at": r["created_at"],
                "full_name": user_info.get("full_name"),
                "age": user_info.get("age"),
            }
        )
    return result


@router.get("/{target_user_id}/images")
async def get_images_for_review(
    target_user_id: uuid.UUID, _: uuid.UUID = Depends(require_admin)
):
    prefix = f"{target_user_id}/kyc"
    items = list_prefix(prefix)

    urls = []
    for it in items:
        name = it.get("name")
        if not name:
            continue
        key = f"{prefix}/{name}"
        urls.append({"name": name, "url": signed_url(key, expires_in=900)})

    db = sb.postgrest
    user_row = (
        db.from_("users")
        .select("full_name,age")
        .eq("id", str(target_user_id))
        .single()
        .execute()
        .data
        or {}
    )

    return {
        "user_id": str(target_user_id),
        "full_name": user_row.get("full_name"),
        "age": user_row.get("age"),
        "images": urls,
    }


@router.post("/{target_user_id}/approve")
async def approve_user(
    target_user_id: uuid.UUID, _: uuid.UUID = Depends(require_admin)
):
    db = sb.postgrest

    user_row = (
        db.from_("users")
        .select("full_name,email")
        .eq("id", str(target_user_id))
        .single()
        .execute()
        .data
    )

    if not user_row:
        raise HTTPException(404, "User not found")

    prefix = f"{target_user_id}/kyc"
    items = list_prefix(prefix)
    keys = [f"{prefix}/{it['name']}" for it in items if it.get("name")]

    db.from_("users").update({"verified": True}).eq("id", str(target_user_id)).execute()
    db.from_("kyc_verification").delete().eq("user_id", str(target_user_id)).execute()

    if keys:
        delete_paths(keys)

    email = (user_row.get("email") or "").strip()
    if email:
        await send_email(
            to_email=email,
            subject="Your LikeMinded verification was approved",
            text=_approved_email_text(user_row.get("full_name")),
        )

    return {"ok": True}


class RejectIn(BaseModel):
    reason: str


@router.post("/{target_user_id}/reject")
async def reject_user(
    target_user_id: uuid.UUID,
    payload: RejectIn = Body(...),
    _: uuid.UUID = Depends(require_admin),
):
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Reason required")

    db = sb.postgrest

    user_row = (
        db.from_("users")
        .select("full_name,email")
        .eq("id", str(target_user_id))
        .single()
        .execute()
        .data
    )

    if not user_row:
        raise HTTPException(404, "User not found")

    prefix = f"{target_user_id}/kyc"
    items = list_prefix(prefix)
    keys = [f"{prefix}/{it['name']}" for it in items if it.get("name")]

    db.from_("users").update({"verified": False}).eq("id", str(target_user_id)).execute()
    db.from_("kyc_verification").delete().eq("user_id", str(target_user_id)).execute()
    db.from_("kyc_rejection_reasons").insert(
        {"user_id": str(target_user_id), "reason": reason}
    ).execute()

    if keys:
        delete_paths(keys)

    email = (user_row.get("email") or "").strip()
    if email:
        await send_email(
            to_email=email,
            subject="Your LikeMinded verification was rejected",
            text=_rejected_email_text(user_row.get("full_name"), reason),
        )

    return {"ok": True}
