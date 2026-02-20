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

    key = make_photo_key(str(user_id), "gallery", position)
    path = await upload_file(file, key)

    async with engine.begin() as conn:
        row = (await conn.execute(text("""
            INSERT INTO user_photos (id, user_id, kind, photo_url, position, created_at)
            VALUES (gen_random_uuid(), :uid, 'gallery', :url, :pos, now())
            ON CONFLICT (user_id, kind, position)
            DO UPDATE SET photo_url = EXCLUDED.photo_url
            RETURNING id::text;
        """), {"uid": str(user_id), "url": path, "pos": position})).mappings().first()

    return {"ok": True, "photo_id": row["id"], "path": path}


@me_photos_router.post("/verification", response_model=UploadResult)
async def upload_verification_id(
    file: UploadFile = File(...),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    key = make_photo_key(str(user_id), "verification_id")
    path = await upload_file(file, key)

    async with engine.begin() as conn:
        row = (await conn.execute(text("""
            INSERT INTO user_photos (id, user_id, kind, photo_url, position, created_at)
            VALUES (gen_random_uuid(), :uid, 'verification', :url, NULL, now())
            RETURNING id::text;
        """), {"uid": str(user_id), "url": path})).mappings().first()

    return {"ok": True, "photo_id": row["id"], "path": path}


@me_photos_router.get("")
async def list_my_photos(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Return signed URLs for MY gallery photos based on user_photos.photo_url.
    """
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT photo_url
            FROM user_photos
            WHERE user_id = :uid
              AND kind = 'gallery'
            ORDER BY position NULLS LAST, created_at ASC;
        """), {"uid": str(user_id)})).mappings().all()

    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    urls: list[str] = []

    # 7 days (good balance: stable UX, still not “forever”)
    expires_in = 60 * 60 * 24 * 7

    for r in rows:
        signed = bucket.create_signed_url(path=r["photo_url"], expires_in=expires_in)
        urls.append(signed["signedURL"])
    return urls


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
        conv = (await conn.execute(text("""
            SELECT id, user_a_id, user_b_id
            FROM conversations
            WHERE id = :cid
              AND type = 'match'
              AND active = true
              AND user_b_id IS NOT NULL
        """), {"cid": cid})).mappings().first()

        if not conv:
            raise HTTPException(404, "Conversation not found")

        if str(user_id) not in (str(conv["user_a_id"]), str(conv["user_b_id"])):
            raise HTTPException(403, "Not your conversation")

        rows = (await conn.execute(text("""
            SELECT p.photo_url
            FROM conversation_photo_access a
            JOIN user_photos p ON p.id = a.photo_id
            WHERE a.conversation_id = :cid
              AND p.kind = 'gallery'
            ORDER BY p.position NULLS LAST, p.created_at ASC;
        """), {"cid": cid})).mappings().all()

    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    urls: list[str] = []
    expires_in = 60 * 60 * 24 * 7

    for r in rows:
        signed = bucket.create_signed_url(path=r["photo_url"], expires_in=expires_in)
        urls.append(signed["signedURL"])
    return urls


router.include_router(me_photos_router)
router.include_router(convo_photos_router)
