# app/api/routes/me_kyc.py
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy import text
from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.services.storage import (
    make_kyc_key,
    upload_file_to_key,
    delete_prefix,
)

router = APIRouter(prefix="/me/kyc", tags=["me-kyc"])


@router.get("/status")
async def get_status(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Return verification status + rejection reasons.
    Logic:
      - if row exists in kyc_verification -> pending
      - else if users.verified = true -> verified
      - else -> unverified + reasons from kyc_rejection_reasons
    """
    uid = str(user_id)
    async with engine.begin() as conn:
        # Check pending
        pending_row = (await conn.execute(
            text("select 1 from kyc_verification where user_id = :uid limit 1"),
            {"uid": uid},
        )).first()
        if pending_row:
            return {"status": "pending", "reasons": []}

        # Check verified flag
        user_row = (await conn.execute(
            text("select verified from users where id = :uid"),
            {"uid": uid},
        )).first()
        if not user_row:
            raise HTTPException(404, "User not found")

        verified = bool(user_row[0])
        if verified:
            return {"status": "verified", "reasons": []}

        # Otherwise return rejection reasons
        reasons = (await conn.execute(
            text("""
                select reason, created_at
                from kyc_rejection_reasons
                where user_id = :uid
                order by created_at desc
            """),
            {"uid": uid},
        )).mappings().all()

    return {"status": "unverified", "reasons": list(reasons or [])}


@router.post("/upload")
async def upload_kyc_images(
    files: list[UploadFile] = File(...),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Upload verification images and mark request as pending:
      - store each file under {user}/kyc/<uuid>.bin
      - insert into kyc_verification if not already present
      - clear any old rejection reasons for this user
    """
    if not files:
        raise HTTPException(400, "No files uploaded")

    uid = str(user_id)

    # Upload files to Supabase storage
    for f in files:
        key = make_kyc_key(uid)
        await upload_file_to_key(f, key)

    # Database updates
    async with engine.begin() as conn:
        # Upsert into kyc_verification
        await conn.execute(
            text("""
                insert into kyc_verification (user_id, created_at)
                values (:uid, now())
                on conflict (user_id) do update set created_at = excluded.created_at
            """),
            {"uid": uid},
        )
        # Clear any previous rejection reasons
        await conn.execute(
            text("delete from kyc_rejection_reasons where user_id = :uid"),
            {"uid": uid},
        )

    return {"ok": True, "count": len(files)}


@router.delete("/cancel")
async def cancel_kyc(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Cancel pending verification:
      - delete row from kyc_verification
      - delete all uploaded images under {user}/kyc
    """
    uid = str(user_id)
    async with engine.begin() as conn:
        await conn.execute(
            text("delete from kyc_verification where user_id = :uid"),
            {"uid": uid},
        )
        await conn.execute(
            text("delete from kyc_rejection_reasons where user_id = :uid"),
            {"uid": uid},
        )

    delete_prefix(f"{uid}/kyc")
    return {"ok": True}
