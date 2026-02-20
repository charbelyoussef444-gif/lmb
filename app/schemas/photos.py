# app/schemas/photos.py
from pydantic import BaseModel

class UploadResult(BaseModel):
    ok: bool
    photo_id: str
    path: str
