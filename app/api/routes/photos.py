# app/api/routes/photos.py
import uuid
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy import text
from pydantic import BaseModel

from app.deps.auth import get_current_user_id
from app.db.engine import engine
from app.services.storage import upload_file, make_photo_key
from app.services.supabase import sb
from app.core.config import settings
from app.core.telegram import send_telegram

router = APIRouter(tags=["photos"])

# ----------------------------
# /me/photos
# ----------------------------
me_photos_router = APIRouter(prefix="/me/photos", tags=["photos"])


class UploadResult(BaseModel):
    ok: bool
    photo_id: str
    path: str


@me_photos_router.post("/gallery", response_model=UploadResult)
async def upload_gallery_photo(
    file: UploadFile = File(...),
    position: int = Form(1),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    if position not in (1, 2, 3):
        raise HTTPException(400, "position must be 1, 2, or 3")

    # Gallery uploads are gated on KYC verification. Until the user is
    # verified, they cannot submit photos for review.
    async with engine.begin() as conn:
        user_row = (
            await conn.execute(
                text("SELECT verified FROM users WHERE id = :uid"),
                {"uid": str(user_id)},
            )
        ).first()

    if not user_row:
        raise HTTPException(404, "User not found")
    if not bool(user_row[0]):
        raise HTTPException(
            403,
            "You must complete identity verification before uploading gallery photos.",
        )

    key = make_photo_key(str(user_id), "gallery", position)
    path = await upload_file(file, key)

    # New upload / re-upload always enters the review queue as 'pending'.
    # On conflict (same user + position), we also clear any previous
    # rejection reason and reviewed_at, because this is a fresh submission.
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
            INSERT INTO user_photos (
                id, user_id, kind, photo_url, position,
                status, rejection_reason, reviewed_at, created_at
            )
            VALUES (
                gen_random_uuid(), :uid, 'gallery', :url, :pos,
                'pending', NULL, NULL, now()
            )
            ON CONFLICT (user_id, kind, position)
            DO UPDATE SET
                photo_url        = EXCLUDED.photo_url,
                status           = 'pending',
                rejection_reason = NULL,
                reviewed_at      = NULL
            RETURNING id::text;
        """
                ),
                {"uid": str(user_id), "url": path, "pos": position},
            )
        ).mappings().first()

    # Best-effort Telegram notification for admin review
    try:
        async with engine.begin() as conn:
            name_row = (
                await conn.execute(
                    text("SELECT full_name FROM users WHERE id = :uid"),
                    {"uid": str(user_id)},
                )
            ).first()
        full_name = (name_row[0] if name_row else None) or "Unknown"
        await send_telegram(
            f"\U0001f5bc <b>Gallery photo pending</b>\n\n"
            f"<b>Name:</b> {full_name}\n"
            f"<b>User ID:</b> {user_id}\n"
            f"<b>Position:</b> {position}\n\n"
            f"New gallery photo uploaded. Review required."
        )
    except Exception:
        pass

    return {"ok": True, "photo_id": row["id"], "path": path}


@me_photos_router.delete("/gallery/{position}", status_code=204)
async def delete_gallery_photo(
    position: int,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    if position not in (1, 2, 3):
        raise HTTPException(400, "position must be 1, 2, or 3")

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT photo_url
                    FROM user_photos
                    WHERE user_id = :uid
                      AND kind = 'gallery'
                      AND position = :pos
                    LIMIT 1;
                    """
                ),
                {"uid": str(user_id), "pos": position},
            )
        ).mappings().first()

        # if it's already empty, treat as success
        if not row:
            return

        photo_url = row["photo_url"]

        await conn.execute(
            text(
                """
                DELETE FROM user_photos
                WHERE user_id = :uid
                  AND kind = 'gallery'
                  AND position = :pos;
                """
            ),
            {"uid": str(user_id), "pos": position},
        )

    # best-effort delete from storage (don't fail request if storage fails)
    try:
        bucket = sb.storage.from_(settings.STORAGE_BUCKET)
        # supabase-py expects a list of paths
        bucket.remove([photo_url])
    except Exception:
        pass

    return


@me_photos_router.post("/verification", response_model=UploadResult)
async def upload_verification_id(
    file: UploadFile = File(...),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    key = make_photo_key(str(user_id), "verification_id")
    path = await upload_file(file, key)

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
            INSERT INTO user_photos (id, user_id, kind, photo_url, position, created_at)
            VALUES (gen_random_uuid(), :uid, 'verification', :url, NULL, now())
            RETURNING id::text;
        """
                ),
                {"uid": str(user_id), "url": path},
            )
        ).mappings().first()

    return {"ok": True, "photo_id": row["id"], "path": path}


@me_photos_router.get("")
async def list_my_photos(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Return MY gallery photos with review status.

    IMPORTANT: returns a fixed 3-slot array aligned to positions 1..3.
    Empty slots are `null`. Populated slots are objects:
        {
          "position": 1..3,
          "url": "<signed url>",
          "status": "pending" | "approved" | "rejected",
          "rejection_reason": "<string>" | null
        }
    """
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
            SELECT position, photo_url, status, rejection_reason
            FROM user_photos
            WHERE user_id = :uid
              AND kind = 'gallery'
              AND position IN (1,2,3)
            ORDER BY position ASC;
        """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().all()

    bucket = sb.storage.from_(settings.STORAGE_BUCKET)

    expires_in = 60 * 60 * 24 * 7  # 7 days

    # fixed slots — None means "empty slot"
    out: list[dict | None] = [None, None, None]  # position 1..3

    for r in rows:
        pos = int(r["position"] or 0)
        if pos not in (1, 2, 3):
            continue
        signed = bucket.create_signed_url(path=r["photo_url"], expires_in=expires_in)
        out[pos - 1] = {
            "position": pos,
            "url": signed["signedURL"],
            "status": r["status"] or "approved",
            "rejection_reason": r["rejection_reason"],
        }

    return out


# ----------------------------
# /conversations/{cid}/photos
# ----------------------------
convo_photos_router = APIRouter(prefix="/conversations", tags=["photos"])


@convo_photos_router.get("/{cid}/photos")
async def list_conversation_photos(
    cid: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Return signed URLs for unlocked photos only.
    If no unlock rows exist yet, this returns [].
    """
    async with engine.begin() as conn:
        conv = (
            await conn.execute(
                text(
                    """
            SELECT id, user_a_id, user_b_id
            FROM conversations
            WHERE id = :cid
              AND type = 'match'
              AND active = true
              AND user_b_id IS NOT NULL
        """
                ),
                {"cid": cid},
            )
        ).mappings().first()

        if not conv:
            raise HTTPException(404, "Conversation not found")

        if str(user_id) not in (str(conv["user_a_id"]), str(conv["user_b_id"])):
            raise HTTPException(403, "Not your conversation")

        rows = (
            await conn.execute(
                text(
                    """
            SELECT p.photo_url
            FROM conversation_photo_access a
            JOIN user_photos p ON p.id = a.photo_id
            WHERE a.conversation_id = :cid
              AND p.kind = 'gallery'
              AND p.status = 'approved'
            ORDER BY p.position NULLS LAST, p.created_at ASC;
        """
                ),
                {"cid": cid},
            )
        ).mappings().all()

    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    urls: list[str] = []
    expires_in = 60 * 60 * 24 * 7

    for r in rows:
        signed = bucket.create_signed_url(path=r["photo_url"], expires_in=expires_in)
        urls.append(signed["signedURL"])
    return urls


router.include_router(me_photos_router)
router.include_router(convo_photos_router)
