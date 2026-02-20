import uuid
from datetime import datetime
from typing import List, Optional, Literal

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.realtime.manager import manager

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ---------------------------
# Models
# ---------------------------

class ConversationOut(BaseModel):
    id: str
    partnerName: str
    partnerAge: Optional[int] = None
    partnerCity: Optional[str] = None
    lastMessage: Optional[str] = None
    lastAt: Optional[str] = None
    compatibility: Optional[float] = None
    unread: int = 0


class MessageOut(BaseModel):
    id: str
    role: Literal["me", "them", "system"]
    text: Optional[str] = None
    ts: int
    kind: Optional[str] = None  # text | match_intro


class SendMessageIn(BaseModel):
    text: str


# ---------------------------
# Helpers
# ---------------------------

def _humanize_relative(dt: datetime) -> str:
    delta = (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds()
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return "Yesterday" if delta < 172800 else f"{int(delta // 86400)}d"


# ---------------------------
# Routes
# ---------------------------

@router.get("", response_model=List[ConversationOut])
async def list_conversations(user_id: uuid.UUID = Depends(get_current_user_id)):
    q = text(
        """
        with my_convos as (
          select
            c.id,
            c.match_score,
            case
              when c.user_a_id = :me then c.user_b_id
              else c.user_a_id
            end as partner_id
          from conversations c
          where c.type = 'match'
            and c.active = true
            and (c.user_a_id = :me or c.user_b_id = :me)
        ),
        last_msg as (
          select distinct on (m.conversation_id)
            m.conversation_id,
            m.content_text,
            m.created_at
          from messages m
          join my_convos mc on mc.id = m.conversation_id
          where m.message_type <> 'match_intro'
          order by m.conversation_id, m.created_at desc
        ),
        unread as (
          select
            conversation_id,
            count(*) as unread_count
          from messages
          where receiver_user_id = :me
            and read_at is null
            and message_type <> 'match_intro'
          group by conversation_id
        )
        select
          mc.id::text as id,
          u.full_name as partner_name,
          u.age as partner_age,
          u.location as partner_city,
          lm.content_text as last_message,
          lm.created_at as last_at_ts,
          mc.match_score as compatibility,
          coalesce(un.unread_count, 0) as unread
        from my_convos mc
        join users u on u.id = mc.partner_id
        left join last_msg lm on lm.conversation_id = mc.id
        left join unread un on un.conversation_id = mc.id
        order by coalesce(lm.created_at, 'epoch') desc;
        """
    )

    async with engine.begin() as conn:
        rows = (await conn.execute(q, {"me": str(user_id)})).mappings().all()

    out: List[ConversationOut] = []
    for r in rows:
        lastAt = _humanize_relative(r["last_at_ts"]) if r["last_at_ts"] else None
        out.append(
            ConversationOut(
                id=r["id"],
                partnerName=r["partner_name"],
                partnerAge=r["partner_age"],
                partnerCity=r["partner_city"],
                lastMessage=r["last_message"],
                lastAt=lastAt,
                compatibility=r["compatibility"],
                unread=r["unread"],
            )
        )

    return out


@router.get("/{cid}/messages", response_model=List[MessageOut])
async def get_thread(cid: str, user_id: uuid.UUID = Depends(get_current_user_id)):
    q_conv = text(
        """
        select id, user_a_id, user_b_id
        from conversations
        where id = :cid and type='match' and active = true
        """
    )

    q_msgs = text(
        """
        select
          id::text,
          sender_user_id,
          sender_bot_id,
          receiver_user_id,
          message_type,
          content_text,
          created_at
        from messages
        where conversation_id = :cid
        order by created_at asc
        """
    )

    async with engine.begin() as conn:
        conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
        if not conv:
            raise HTTPException(404, "Conversation not found")

        if str(user_id) not in (str(conv["user_a_id"]), str(conv["user_b_id"])):
            raise HTTPException(403, "Not your conversation")

        rows = (await conn.execute(q_msgs, {"cid": cid})).mappings().all()

    out: List[MessageOut] = []

    for r in rows:
        if r["message_type"] == "match_intro":
            role = "system"
        else:
            role = (
                "me"
                if str(r["sender_user_id"]) == str(user_id)
                else "them"
            )

        out.append(
            MessageOut(
                id=r["id"],
                role=role,
                text=r["content_text"],
                ts=int(r["created_at"].timestamp()),
                kind=r["message_type"],
            )
        )

    return out


@router.post("/{cid}/messages", response_model=MessageOut)
async def send_message(
    cid: str,
    body: SendMessageIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    q_conv = text(
        """
        select id, user_a_id, user_b_id
        from conversations
        where id = :cid and type='match' and active = true
        """
    )

    async with engine.begin() as conn:
        conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
        if not conv:
            raise HTTPException(404, "Conversation not found")

        if str(user_id) not in (str(conv["user_a_id"]), str(conv["user_b_id"])):
            raise HTTPException(403, "Not your conversation")

        # ✅ FIXED TERNARY (THIS WAS YOUR ERROR)
        other = (
            conv["user_b_id"]
            if str(user_id) == str(conv["user_a_id"])
            else conv["user_a_id"]
        )

        row = (
            await conn.execute(
                text(
                    """
                    insert into messages(
                        id,
                        conversation_id,
                        sender_user_id,
                        receiver_user_id,
                        message_type,
                        content_text,
                        created_at
                    )
                    values (
                        gen_random_uuid(),
                        :cid,
                        :s,
                        :r,
                        'text',
                        :t,
                        now()
                    )
                    returning
                        id::text,
                        conversation_id,
                        sender_user_id,
                        receiver_user_id,
                        content_text,
                        created_at
                    """
                ),
                {
                    "cid": cid,
                    "s": str(user_id),
                    "r": str(other),
                    "t": body.text,
                },
            )
        ).mappings().first()

    # WebSocket broadcast
    try:
        await manager.broadcast(
            cid,
            {
                "type": "message-new",
                "data": {
                    "id": row["id"],
                    "conversation_id": str(row["conversation_id"]),
                    "sender_user_id": str(row["sender_user_id"]),
                    "receiver_user_id": str(row["receiver_user_id"]),
                    "content_text": row["content_text"],
                    "created_at": row["created_at"].isoformat(),
                },
            },
        )
    except Exception:
        pass

    return MessageOut(
        id=row["id"],
        role="me",
        text=row["content_text"],
        ts=int(row["created_at"].timestamp()),
        kind="text",
    )


@router.post("/{cid}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(cid: str, user_id: uuid.UUID = Depends(get_current_user_id)):
    q_conv = text(
        """
        select id, user_a_id, user_b_id
        from conversations
        where id = :cid and type='match' and active = true
        """
    )

    async with engine.begin() as conn:
        conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
        if not conv:
            raise HTTPException(404, "Conversation not found")

        if str(user_id) not in (str(conv["user_a_id"]), str(conv["user_b_id"])):
            raise HTTPException(403, "Not your conversation")

        await conn.execute(
            text(
                """
                update messages
                set read_at = now()
                where conversation_id = :cid
                  and receiver_user_id = :me
                  and read_at is null
                  and message_type <> 'match_intro'
                """
            ),
            {"cid": cid, "me": str(user_id)},
        )

    try:
        await manager.broadcast(cid, {"type": "read", "userId": str(user_id)})
    except Exception:
        pass
