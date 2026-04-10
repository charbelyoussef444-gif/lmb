# app/api/routes/admin_gallery.py
"""
Admin routes for reviewing user gallery photos.

Workflow:
- Users upload gallery photos to positions 1..3. Each uploaded (or re-uploaded)
  photo enters `user_photos` with status = 'pending'.
- Admins see a list of users with any pending gallery photo at
  GET /admin/gallery/pending.
- For each user, admin opens the review page which returns:
    * The user's full_name
    * The verified selfie signed URL (kept from KYC)
    * An array of pending photo slots with signed URLs
- Admin can approve or reject individual photos (with a reason), or
  approve/reject all pending photos at once.
- Rejected photos stay in the DB so the client can show the rejection
  reason to the user and prompt them to re-upload. Approving a photo
  just flips status to 'approved'.
- Only rejections trigger a push notification to the user.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.deps.admin import require_admin
from app.db.engine import engine
from app.services.storage import signed_url, make_kyc_selfie_key
from app.services.supabase import sb  # service-role client (for storage only)
from app.core.firebase import send_push


router = APIRouter(prefix="/admin/gallery", tags=["admin-gallery"])


# ---------- helpers ----------

def _selfie_signed_url(user_id: str) -> str | None:
    """
    Return a signed URL for the user's verified selfie, or None if missing.
    The selfie lives at the deterministic KYC path written during upload
    and kept around after KYC approval specifically so admins can see it
    next to gallery photos during review.
    """
    try:
        return signed_url(make_kyc_selfie_key(user_id), expires_in=900)
    except Exception:
        return None


# ---------- GET /admin/gallery/pending ----------

@router.get("/pending")
async def list_pending_gallery(_: uuid.UUID = Depends(require_admin)):
    """
    Return all users who currently have at least one gallery photo in
    'pending' status, most-recent submission first.
    Each item: { user_id, full_name, age, pending_count, oldest_pending_at }
    """
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT
                        p.user_id::text              AS user_id,
                        u.full_name                  AS full_name,
                        u.age                        AS age,
                        COUNT(*)::int                AS pending_count,
                        MIN(p.created_at)            AS oldest_pending_at
                    FROM user_photos p
                    LEFT JOIN users u ON u.id = p.user_id
                    WHERE p.kind = 'gallery'
                      AND p.status = 'pending'
                    GROUP BY p.user_id, u.full_name, u.age
                    ORDER BY MIN(p.created_at) ASC
                    """
                )
            )
        ).mappings().all()

    return [
        {
            "user_id": r["user_id"],
            "full_name": r["full_name"],
            "age": r["age"],
            "pending_count": r["pending_count"],
            "oldest_pending_at": r["oldest_pending_at"],
        }
        for r in rows
    ]


# ---------- GET /admin/gallery/{target_user_id} ----------

@router.get("/{target_user_id}")
async def get_user_gallery_for_review(
    target_user_id: uuid.UUID,
    _: uuid.UUID = Depends(require_admin),
):
    """
    Return all gallery photos for a user (any status) along with the
    user's verified selfie URL, so admin can visually compare.

    Shape:
      {
        "user_id": "...",
        "full_name": "...",
        "age": 27,
        "selfie_url": "<signed url or null>",
        "photos": [
           { "position": 1, "url": "...", "status": "pending", "rejection_reason": null },
           ...
        ]
      }
    """
    uid = str(target_user_id)

    async with engine.begin() as conn:
        user_row = (
            await conn.execute(
                text("SELECT full_name, age FROM users WHERE id = :uid"),
                {"uid": uid},
            )
        ).mappings().first()

        if not user_row:
            raise HTTPException(404, "User not found")

        photo_rows = (
            await conn.execute(
                text(
                    """
                    SELECT position, photo_url, status, rejection_reason, created_at
                    FROM user_photos
                    WHERE user_id = :uid
                      AND kind = 'gallery'
                      AND position IN (1,2,3)
                    ORDER BY position ASC
                    """
                ),
                {"uid": uid},
            )
        ).mappings().all()

    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    expires_in = 60 * 60  # 1h is plenty for review

    photos: list[dict] = []
    for r in photo_rows:
        pos = int(r["position"] or 0)
        if pos not in (1, 2, 3):
            continue
        try:
            s = bucket.create_signed_url(path=r["photo_url"], expires_in=expires_in)
            url = s["signedURL"]
        except Exception:
            url = ""
        photos.append(
            {
                "position": pos,
                "url": url,
                "status": r["status"] or "approved",
                "rejection_reason": r["rejection_reason"],
            }
        )

    return {
        "user_id": uid,
        "full_name": user_row["full_name"],
        "age": user_row["age"],
        "selfie_url": _selfie_signed_url(uid),
        "photos": photos,
    }


# ---------- POST /admin/gallery/{user_id}/photos/{position}/approve ----------

@router.post("/{target_user_id}/photos/{position}/approve")
async def approve_photo(
    target_user_id: uuid.UUID,
    position: int,
    _: uuid.UUID = Depends(require_admin),
):
    if position not in (1, 2, 3):
        raise HTTPException(400, "position must be 1, 2, or 3")

    uid = str(target_user_id)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE user_photos
                SET status = 'approved',
                    rejection_reason = NULL,
                    reviewed_at = now()
                WHERE user_id = :uid
                  AND kind = 'gallery'
                  AND position = :pos
                  AND status = 'pending'
                """
            ),
            {"uid": uid, "pos": position},
        )

    if (result.rowcount or 0) == 0:
        raise HTTPException(404, "No pending photo at that position")

    return {"ok": True}


# ---------- POST /admin/gallery/{user_id}/photos/{position}/reject ----------

class RejectPhotoIn(BaseModel):
    reason: str


@router.post("/{target_user_id}/photos/{position}/reject")
async def reject_photo(
    target_user_id: uuid.UUID,
    position: int,
    payload: RejectPhotoIn = Body(...),
    _: uuid.UUID = Depends(require_admin),
):
    if position not in (1, 2, 3):
        raise HTTPException(400, "position must be 1, 2, or 3")

    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Rejection reason is required")

    uid = str(target_user_id)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE user_photos
                SET status = 'rejected',
                    rejection_reason = :reason,
                    reviewed_at = now()
                WHERE user_id = :uid
                  AND kind = 'gallery'
                  AND position = :pos
                  AND status = 'pending'
                """
            ),
            {"uid": uid, "pos": position, "reason": reason},
        )

    if (result.rowcount or 0) == 0:
        raise HTTPException(404, "No pending photo at that position")

    # Push only on reject, per product decision.
    try:
        await send_push(
            user_id=uid,
            title="Photo Rejected",
            body="One of your gallery photos was rejected. Tap to see why and re-upload.",
            data={"type": "gallery_rejected", "channel_id": "verification"},
        )
    except Exception:
        pass

    return {"ok": True}


# ---------- POST /admin/gallery/{user_id}/approve-all ----------

@router.post("/{target_user_id}/approve-all")
async def approve_all_photos(
    target_user_id: uuid.UUID,
    _: uuid.UUID = Depends(require_admin),
):
    uid = str(target_user_id)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE user_photos
                SET status = 'approved',
                    rejection_reason = NULL,
                    reviewed_at = now()
                WHERE user_id = :uid
                  AND kind = 'gallery'
                  AND status = 'pending'
                """
            ),
            {"uid": uid},
        )

    return {"ok": True, "updated": result.rowcount or 0}


# ---------- POST /admin/gallery/{user_id}/reject-all ----------

class RejectAllIn(BaseModel):
    reason: str


@router.post("/{target_user_id}/reject-all")
async def reject_all_photos(
    target_user_id: uuid.UUID,
    payload: RejectAllIn = Body(...),
    _: uuid.UUID = Depends(require_admin),
):
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Rejection reason is required")

    uid = str(target_user_id)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE user_photos
                SET status = 'rejected',
                    rejection_reason = :reason,
                    reviewed_at = now()
                WHERE user_id = :uid
                  AND kind = 'gallery'
                  AND status = 'pending'
                """
            ),
            {"uid": uid, "reason": reason},
        )

    updated = result.rowcount or 0

    if updated > 0:
        try:
            await send_push(
                user_id=uid,
                title="Photos Rejected",
                body="Your gallery photos were rejected. Tap to see why and re-upload.",
                data={"type": "gallery_rejected", "channel_id": "verification"},
            )
        except Exception:
            pass

    return {"ok": True, "updated": updated}
