# app/api/routes/me_kyc.py
import os
import uuid
import httpx
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.services.storage import (
    make_kyc_id_key,
    make_kyc_selfie_key,
    upload_file_to_key,
    delete_prefix,
)

router = APIRouter(prefix="/me/kyc", tags=["me-kyc"])

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()


async def _send_telegram(text_msg: str) -> None:
    """Best-effort Telegram notify (never breaks KYC flow)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ADMIN_CHAT_ID,
        "text": text_msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception:
        # don't crash verification if telegram is down
        return


def _sanitize_files(files: list[UploadFile]) -> list[UploadFile]:
    # remove None, keep order
    clean = [f for f in (files or []) if f is not None]
    return clean


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
        pending_row = (
            await conn.execute(
                text("select 1 from kyc_verification where user_id = :uid limit 1"),
                {"uid": uid},
            )
        ).first()

        if pending_row:
            return {"status": "pending", "reasons": []}

        user_row = (
            await conn.execute(
                text("select verified from users where id = :uid"),
                {"uid": uid},
            )
        ).first()

        if not user_row:
            raise HTTPException(404, "User not found")

        verified = bool(user_row[0])
        if verified:
            return {"status": "verified", "reasons": []}

        reasons = (
            await conn.execute(
                text(
                    """
                    select reason, created_at
                    from kyc_rejection_reasons
                    where user_id = :uid
                    order by created_at desc
                    """
                ),
                {"uid": uid},
            )
        ).mappings().all()

    return {"status": "unverified", "reasons": list(reasons or [])}


@router.post("/upload")
async def upload_kyc_images(
    files: list[UploadFile] = File(...),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Upload verification images and mark request as pending:
      - require exactly 2 images in strict order: [ID document, selfie]
      - store at deterministic paths {user}/kyc/id.bin and {user}/kyc/selfie.bin
        so admin can fetch the selfie later by path and approval can
        surgically delete only the ID document
      - upsert into kyc_verification
      - clear any old rejection reasons
      - send Telegram message: "verification pending" + name + id
    """
    files = _sanitize_files(files)
    if not files:
        raise HTTPException(400, "No files uploaded")

    if len(files) != 2:
        raise HTTPException(400, "Please upload exactly 2 verification images: ID and selfie.")

    uid = str(user_id)

    id_file, selfie_file = files[0], files[1]
    for f in (id_file, selfie_file):
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(400, "All files must be images.")

    # Order matters: index 0 = ID document, index 1 = selfie.
    # Both clients (mobile VerificationScreen.handleSubmit, web ProfilePage kycSlots)
    # upload in that order.
    await upload_file_to_key(id_file, make_kyc_id_key(uid))
    await upload_file_to_key(selfie_file, make_kyc_selfie_key(uid))

    async with engine.begin() as conn:
        u = (
            await conn.execute(
                text("select full_name from users where id = :uid"),
                {"uid": uid},
            )
        ).mappings().first()

        if not u:
            raise HTTPException(404, "User not found")

        full_name = (u.get("full_name") or "Unknown").strip()

        await conn.execute(
            text(
                """
                insert into kyc_verification (user_id, created_at)
                values (:uid, now())
                on conflict (user_id)
                do update set created_at = excluded.created_at
                """
            ),
            {"uid": uid},
        )

        await conn.execute(
            text("delete from kyc_rejection_reasons where user_id = :uid"),
            {"uid": uid},
        )

    await _send_telegram(
        "🆕 <b>Verification pending</b>\n\n"
        f"<b>Name:</b> {full_name}\n"
        f"<b>User ID:</b> {uid}\n\n"
        "User submitted 2 images. Review required."
    )

    return {"ok": True, "count": len(files)}

@router.delete("/cancel")
async def cancel_kyc(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Cancel pending verification:
      - delete row from kyc_verification
      - delete rejection reasons
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

    # storage cleanup (best-effort)
    try:
        delete_prefix(f"{uid}/kyc")
    except Exception:
        pass

    return {"ok": True}
