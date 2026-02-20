# app/services/storage.py
import uuid
from typing import Iterable, List, Dict
from fastapi import UploadFile, HTTPException
from app.core.config import settings
from app.services.supabase import sb

__all__ = [
    "upload_file",
    "make_photo_key",
    "make_kyc_key",
    "list_prefix",
    "create_signed_url",
    "signed_url",            # <-- add this
    "delete_paths",
    "delete_prefix",
    "upload_file_to_key",
]

# simple wrapper so routes can call the same name we used in examples
async def upload_file_to_key(file: UploadFile, key: str) -> str:
    return await upload_file(file, key)

async def upload_file(file: UploadFile, key: str) -> str:
    data = await file.read()
    content_type = file.content_type or "application/octet-stream"
    res = sb.storage.from_(settings.STORAGE_BUCKET).upload(
        path=key,
        file=data,
        file_options={
            "contentType": content_type,
            "upsert": "true",
        },
    )
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(500, f"Storage upload error: {res['error']['message']}")
    return key

def make_photo_key(user_id: str, kind: str, position: int | None = None) -> str:
    pid = uuid.uuid4()
    if kind == "gallery":
        return f"{user_id}/gallery/{position}-{pid}.bin"
    if kind == "verification_id":
        # backward-compatible single-file verification path
        return f"{user_id}/kyc/id-{pid}.bin"
    return f"{user_id}/{kind}/{pid}.bin"

# ---------- NEW: KYC helpers (non-breaking) ----------
def make_kyc_key(user_id: str) -> str:
    return f"{user_id}/kyc/{uuid.uuid4()}.bin"

def list_prefix(prefix: str) -> List[Dict]:
    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    items = bucket.list(path=prefix) or []
    return [it for it in items if it and it.get("name")]

def create_signed_url(key: str, expires_in: int = 600) -> str:
    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    out = bucket.create_signed_url(path=key, expires_in=expires_in)
    return out["signedURL"]

# --- alias expected by admin_kyc.py ---
def signed_url(key: str, expires_in: int = 600) -> str:
    return create_signed_url(key, expires_in)

def delete_paths(paths: Iterable[str]) -> None:
    clean = [p for p in paths if p]
    if not clean:
        return
    bucket = sb.storage.from_(settings.STORAGE_BUCKET)
    bucket.remove(paths=clean)

def delete_prefix(prefix: str) -> int:
    items = list_prefix(prefix)
    keys = [f"{prefix.rstrip('/')}/{it['name']}" for it in items]
    delete_paths(keys)
    return len(keys)
