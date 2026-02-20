import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from app.deps.auth import get_current_user_id

router = APIRouter(prefix="/assessment", tags=["assessment"])


@router.post("/start")
async def start_assessment(user_id: uuid.UUID = Depends(get_current_user_id)):
    attempt_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "attempt_id": str(attempt_id), "started_at": now}
