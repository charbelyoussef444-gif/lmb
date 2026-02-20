import uuid
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id

router = APIRouter(prefix="/reports", tags=["reports"])

ReportReason = Literal[
    "spam",
    "harassment",
    "scam",
    "hate",
    "sexual",
    "impersonation",
    "other",
]


class ReportIn(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    reason: ReportReason
    details: Optional[str] = Field(default=None, max_length=2000)


class ReportOut(BaseModel):
    id: str


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    body: ReportIn, user_id: uuid.UUID = Depends(get_current_user_id)
):
    q_conv = text(
        """
        select id, user_a_id, user_b_id
        from conversations
        where id = :cid
          and type = 'match'
        """
    )

    async with engine.begin() as conn:
        conv = (
            await conn.execute(q_conv, {"cid": body.conversation_id})
        ).mappings().first()

        if not conv:
            raise HTTPException(404, "Conversation not found")

        me = str(user_id)
        ua = str(conv["user_a_id"])
        ub = str(conv["user_b_id"])

        if me not in (ua, ub):
            raise HTTPException(403, "Not your conversation")

        reported = ub if me == ua else ua

        # basic dedupe while open/reviewing
        existing = (
            await conn.execute(
                text(
                    """
                    select id
                    from reports
                    where reporter_user_id = :reporter
                      and reported_user_id = :reported
                      and conversation_id = :cid
                      and reason = :reason
                      and status in ('open','reviewing')
                    limit 1
                    """
                ),
                {
                    "reporter": me,
                    "reported": reported,
                    "cid": body.conversation_id,
                    "reason": body.reason,
                },
            )
        ).mappings().first()

        if existing:
            return ReportOut(id=str(existing["id"]))

        row = (
            await conn.execute(
                text(
                    """
                    insert into reports (
                        id, reporter_user_id, reported_user_id,
                        conversation_id, reason, details, status,
                        created_at, updated_at
                    )
                    values (
                        gen_random_uuid(), :reporter, :reported,
                        :cid, :reason, :details, 'open',
                        now(), now()
                    )
                    returning id::text as id
                    """
                ),
                {
                    "reporter": me,
                    "reported": reported,
                    "cid": body.conversation_id,
                    "reason": body.reason,
                    "details": body.details,
                },
            )
        ).mappings().first()

    return ReportOut(id=row["id"])
