# app/api/routes/match_feedback.py
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.realtime.manager import manager  # broadcast event
from app.core.firebase import send_push

router = APIRouter(prefix="/conversations", tags=["match-feedback"])


class FeedbackStatusOut(BaseModel):
    conversation_id: str
    is_24h_passed: bool
    already_answered: bool
    should_prompt: bool
    my_response: bool | None = None


class FeedbackIn(BaseModel):
    response: bool  # true=yes, false=no


class FeedbackOut(BaseModel):
    conversation_id: str
    saved: bool
    both_yes: bool
    photos_unlocked: bool


@router.get("/{cid}/match-feedback/status", response_model=FeedbackStatusOut)
async def match_feedback_status(
    cid: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    q = text("""
    WITH c AS (
      SELECT id, created_at
      FROM conversations
      WHERE id = :cid
        AND type = 'match'
        AND active = true
        AND user_b_id IS NOT NULL
        AND (:me = user_a_id OR :me = user_b_id)
    ),
    r AS (
      SELECT response
      FROM conversation_match_feedback
      WHERE conversation_id = :cid
        AND user_id = :me
    )
    SELECT
      c.id::text AS conversation_id,
      (now() >= c.created_at + interval '24 hours') AS is_24h_passed,
      (r.response IS NOT NULL) AS already_answered,
      (now() >= c.created_at + interval '24 hours' AND r.response IS NULL) AS should_prompt,
      r.response AS my_response
    FROM c
    LEFT JOIN r ON true;
    """)

    async with engine.begin() as conn:
        row = (await conn.execute(q, {"cid": cid, "me": str(user_id)})).mappings().first()

    if not row:
        raise HTTPException(404, "Conversation not found or not allowed")

    return FeedbackStatusOut(**row)


@router.post("/{cid}/match-feedback", response_model=FeedbackOut)
async def submit_match_feedback(
    cid: str,
    body: FeedbackIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    photos_unlocked = False
    both_yes = False

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

        too_early = (await conn.execute(text("""
            SELECT now() < (created_at + interval '24 hours') AS too_early
            FROM conversations
            WHERE id = :cid
        """), {"cid": cid})).scalar_one()

        if too_early:
            raise HTTPException(400, "Feedback available after 24 hours")

        # Save response
        await conn.execute(text("""
            INSERT INTO conversation_match_feedback (conversation_id, user_id, response, responded_at)
            VALUES (:cid, :me, :resp, now())
            ON CONFLICT (conversation_id, user_id)
            DO UPDATE SET response = EXCLUDED.response,
                          responded_at = now();
        """), {"cid": cid, "me": str(user_id), "resp": body.response})

        # Both yes?
        both_yes = (await conn.execute(text("""
            SELECT count(*) = 2 AS both_yes
            FROM conversation_match_feedback
            WHERE conversation_id = :cid
              AND response = true;
        """), {"cid": cid})).scalar_one()

        if both_yes:
            # Unlock ALL gallery photos for both users in this convo
            await conn.execute(text("""
                INSERT INTO conversation_photo_access (
                  conversation_id,
                  photo_id,
                  unlocked_at,
                  unlock_kind,
                  unlock_rule
                )
                SELECT
                  :cid,
                  p.id,
                  now(),
                  'mutual_yes',
                  '24h_match_satisfaction'
                FROM user_photos p
                JOIN conversations c
                  ON c.id = :cid
                WHERE p.user_id IN (c.user_a_id, c.user_b_id)
                  AND p.kind = 'gallery'
                ON CONFLICT DO NOTHING;
            """), {"cid": cid})

            photos_unlocked = True

    # Broadcast OUTSIDE txn
    if photos_unlocked:
        try:
            await manager.broadcast(
                cid,
                {"type": "photos-unlocked", "conversation_id": cid},
            )
        except Exception:
            pass

    # --- FCM pushes ---
    try:
        partner_id = str(conv["user_b_id"]) if str(user_id) == str(conv["user_a_id"]) else str(conv["user_a_id"])

        if photos_unlocked:
            # Both said yes — notify both users
            for uid in [str(conv["user_a_id"]), str(conv["user_b_id"])]:
                await send_push(
                    user_id=uid,
                    title="Photos Unlocked!",
                    body="You both liked this match! View each other's photos now.",
                    data={"type": "photos_unlocked", "conversationId": cid, "channel_id": "photos"},
                )
        elif body.response is True:
            # Current user said yes — notify partner (only if partner hasn't answered yet)
            await send_push(
                user_id=partner_id,
                title="Your match is satisfied!",
                body="Your match gave positive feedback. Share yours now!",
                data={"type": "partner_feedback", "conversationId": cid, "channel_id": "feedback"},
            )
    except Exception:
        pass

    return FeedbackOut(
        conversation_id=cid,
        saved=True,
        both_yes=bool(both_yes),
        photos_unlocked=bool(photos_unlocked),
    )
