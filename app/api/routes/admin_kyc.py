# app/api/routes/admin_kyc.py
import hashlib
import hmac
import re
import uuid
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel

from app.core.config import settings
from app.deps.admin import require_admin
from app.services.storage import list_prefix, signed_url, delete_paths
from app.services.supabase import sb  # service-role client
from app.services.mail import send_email
from app.core.firebase import send_push


def _sanitize_id_number(raw: str) -> str:
    """Strip spaces, dashes, dots and lowercase — keep only alphanumeric chars."""
    return re.sub(r"[^a-z0-9]", "", raw.strip().lower())


def _hash_id_number(id_number: str) -> str:
    """HMAC-SHA256 of the sanitised ID number using a server-side secret."""
    clean = _sanitize_id_number(id_number)
    if not clean:
        raise HTTPException(400, "ID number is empty after sanitisation.")
    key = (settings.KYC_ID_HMAC_KEY or "").encode("utf-8")
    return hmac.new(key, clean.encode("utf-8"), hashlib.sha256).hexdigest()

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


def _extract_auth_email(auth_response) -> str | None:
    # Handles common supabase-py response shapes safely
    if auth_response is None:
        return None

    user = getattr(auth_response, "user", None)
    if user is not None:
        email = getattr(user, "email", None)
        if email:
            return email

    if isinstance(auth_response, dict):
        user_dict = auth_response.get("user") or {}
        email = user_dict.get("email")
        if email:
            return email

    data = getattr(auth_response, "data", None)
    if data is not None:
        user = getattr(data, "user", None)
        if user is not None:
            email = getattr(user, "email", None)
            if email:
                return email

        if isinstance(data, dict):
            user_dict = data.get("user") or {}
            email = user_dict.get("email")
            if email:
                return email

    return None


def _get_user_profile_and_email(target_user_id: uuid.UUID) -> tuple[str | None, str | None]:
    db = sb.postgrest

    profile_row = (
        db.from_("users")
        .select("full_name")
        .eq("id", str(target_user_id))
        .single()
        .execute()
        .data
    )

    if not profile_row:
        raise HTTPException(404, "User not found")

    full_name = profile_row.get("full_name")

    try:
        auth_response = sb.auth.admin.get_user_by_id(str(target_user_id))
        email = _extract_auth_email(auth_response)
    except Exception as e:
        print(f"[admin_kyc] failed to fetch auth email for {target_user_id}: {e}")
        email = None

    return full_name, email


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


class ApproveIn(BaseModel):
    id_number: str


@router.post("/{target_user_id}/approve")
async def approve_user(
    target_user_id: uuid.UUID,
    payload: ApproveIn = Body(...),
    _: uuid.UUID = Depends(require_admin),
):
    id_number = (payload.id_number or "").strip()
    if not id_number:
        raise HTTPException(400, "ID number is required to approve.")

    id_hash = _hash_id_number(id_number)

    db = sb.postgrest

    full_name, email = _get_user_profile_and_email(target_user_id)

    prefix = f"{target_user_id}/kyc"
    items = list_prefix(prefix)
    keys = [f"{prefix}/{it['name']}" for it in items if it.get("name")]

    # Insert hash first — relies on UNIQUE constraint on id_hash to prevent
    # race conditions (two concurrent approvals for the same ID).
    try:
        db.from_("kyc_id_hashes").insert({"id_hash": id_hash, "user_id": str(target_user_id)}).execute()
    except Exception:
        raise HTTPException(
            409,
            "This ID number is already associated with another account. "
            "Possible duplicate account.",
        )

    # Hash inserted successfully — safe to approve
    try:
        db.from_("users").update({"verified": True}).eq("id", str(target_user_id)).execute()
        db.from_("kyc_verification").delete().eq("user_id", str(target_user_id)).execute()
    except Exception as exc:
        raise HTTPException(500, f"Failed to finalize KYC approval: {exc}")

    if keys:
        delete_paths(keys)

    if email:
        try:
            await send_email(
                to_email=email,
                subject="Your LikeMinded verification was approved",
                text=_approved_email_text(full_name),
            )
        except Exception as e:
            print(f"[admin_kyc] failed to send approval email to {email}: {e}")

    # --- FCM push: notify user of approval ---
    try:
        await send_push(
            user_id=str(target_user_id),
            title="Verification Approved ✅",
            body="Your identity has been verified. You're all set!",
            data={"type": "kyc_approved", "channel_id": "verification"},
        )
    except Exception:
        pass

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

    full_name, email = _get_user_profile_and_email(target_user_id)

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

    if email:
        try:
            await send_email(
                to_email=email,
                subject="Your LikeMinded verification was rejected",
                text=_rejected_email_text(full_name, reason),
            )
        except Exception as e:
            print(f"[admin_kyc] failed to send rejection email to {email}: {e}")

    # --- FCM push: notify user of rejection ---
    try:
        await send_push(
            user_id=str(target_user_id),
            title="Verification Update",
            body="Your verification needs attention. Please check and resubmit.",
            data={"type": "kyc_rejected", "channel_id": "verification"},
        )
    except Exception:
        pass

    return {"ok": True}
