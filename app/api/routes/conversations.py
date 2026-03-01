# app/api/routes/conversations.py
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Literal

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.realtime.manager import manager

# ---- Supabase (signed URLs) ----
# pip install supabase
from supabase import create_client  # type: ignore

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_GALLERY_BUCKET = os.getenv("SUPABASE_GALLERY_BUCKET", "gallery").strip() or "gallery"
SUPABASE_SIGN_SECONDS = int(os.getenv("SUPABASE_SIGN_SECONDS", "3600"))

supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
  supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

router = APIRouter(prefix="/conversations", tags=["conversations"])

PHOTO_UNLOCK_HOURS = 24


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
  partnerLastActive: Optional[str] = None


class MessageOut(BaseModel):
  id: str
  role: Literal["me", "them", "system"]
  text: Optional[str] = None
  ts: int
  kind: Optional[str] = None  # text | match_intro


class SendMessageIn(BaseModel):
  text: str


class MatchFeedbackStatusOut(BaseModel):
  conversation_id: str
  is_24h_passed: bool
  already_answered: bool
  should_prompt: bool
  my_response: Optional[bool] = None


class MatchFeedbackIn(BaseModel):
  response: bool


class MatchFeedbackResultOut(BaseModel):
  conversation_id: str
  saved: bool
  both_yes: bool
  photos_unlocked: bool


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


async def _get_conversation_or_404(conn, cid: str):
  q_conv = text(
    """
    select id, user_a_id, user_b_id, created_at, active
    from conversations
    where id::text = :cid
      and type = 'match'
    """
  )
  conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
  if not conv:
    raise HTTPException(404, "Conversation not found")
  return conv


def _partner_id(conv, me: uuid.UUID) -> str:
  a = str(conv["user_a_id"])
  b = str(conv["user_b_id"])
  me_s = str(me)
  return b if me_s == a else a


def _is_member(conv, me: uuid.UUID) -> bool:
  me_s = str(me)
  return me_s in (str(conv["user_a_id"]), str(conv["user_b_id"]))


async def _get_my_feedback(conn, cid: str, me: uuid.UUID) -> Optional[bool]:
  row = (
    await conn.execute(
      text(
        """
        select response
        from match_feedback
        where conversation_id::text = :cid
          and user_id::text = :me
        """
      ),
      {"cid": cid, "me": str(me)},
    )
  ).mappings().first()
  if not row:
    return None
  return bool(row["response"])


async def _get_both_feedback(conn, cid: str, conv) -> dict:
  # returns {"a": Optional[bool], "b": Optional[bool]}
  a = str(conv["user_a_id"])
  b = str(conv["user_b_id"])

  rows = (
    await conn.execute(
      text(
        """
        select user_id::text as uid, response
        from match_feedback
        where conversation_id::text = :cid
          and (user_id::text = :a or user_id::text = :b)
        """
      ),
      {"cid": cid, "a": a, "b": b},
    )
  ).mappings().all()

  out = {"a": None, "b": None}
  for r in rows:
    if r["uid"] == a:
      out["a"] = bool(r["response"])
    elif r["uid"] == b:
      out["b"] = bool(r["response"])
  return out


def _is_24h_passed(conv_created_at: datetime) -> bool:
  # conv_created_at is usually tz-aware; comparing in UTC-safe way
  now = datetime.utcnow()
  created = conv_created_at.replace(tzinfo=None)
  return (now - created) >= timedelta(hours=PHOTO_UNLOCK_HOURS)


async def _get_user_photo_paths(conn, user_id: str) -> List[str]:
  """
  IMPORTANT: Change this query if your table/column differs.
  This expects: user_photos(user_id, storage_path, created_at)
  storage_path example: "0125a825-.../1-abc.jpg"
  """
  rows = (
    await conn.execute(
      text(
        """
        select storage_path
        from user_photos
        where user_id::text = :uid
        order by created_at asc
        """
      ),
      {"uid": user_id},
    )
  ).scalars().all()

  # filter null/empty
  paths = [p for p in rows if isinstance(p, str) and p.strip()]
  return paths


def _signed_urls(paths: List[str]) -> List[str]:
  if not paths:
    return []
  if supabase is None:
    # If you see this, you forgot SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY env vars.
    return []

  out: List[str] = []
  bucket = supabase.storage.from_(SUPABASE_GALLERY_BUCKET)

  for p in paths:
    try:
      signed = bucket.create_signed_url(p, SUPABASE_SIGN_SECONDS)
      # supabase-py returns dict with key "signedURL" in most versions
      url = (signed or {}).get("signedURL") or (signed or {}).get("signedUrl")
      if url:
        out.append(url)
    except Exception:
      continue

  return out


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
      coalesce(un.unread_count, 0) as unread,

      greatest(
        coalesce(u.last_login_at, to_timestamp(0)),
        coalesce((select max(ts) from events e where e.user_id = mc.partner_id), to_timestamp(0)),
        coalesce((
          select max(m2.created_at)
          from messages m2
          where m2.sender_user_id = mc.partner_id
             or m2.receiver_user_id = mc.partner_id
        ), to_timestamp(0))
      ) as partner_last_active_ts

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

    partner_last_active_ts = r.get("partner_last_active_ts")
    partnerLastActive = None
    if partner_last_active_ts is not None:
      try:
        if partner_last_active_ts != datetime.fromtimestamp(0):
          partnerLastActive = partner_last_active_ts.isoformat()
      except Exception:
        partnerLastActive = None

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
        partnerLastActive=partnerLastActive,
      )
    )

  return out


@router.get("/{cid}/messages", response_model=List[MessageOut])
async def get_thread(cid: str, user_id: uuid.UUID = Depends(get_current_user_id)):
  q_conv = text(
    """
    select id, user_a_id, user_b_id
    from conversations
    where id::text = :cid
      and type = 'match'
      and active = true
    """
  )

  q_msgs = text(
    """
    select
      id::text as id,
      sender_user_id,
      receiver_user_id,
      message_type,
      content_text,
      created_at
    from messages
    where conversation_id::text = :cid
    order by created_at asc
    """
  )

  async with engine.begin() as conn:
    conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
    if not conv:
      raise HTTPException(404, "Conversation not found")

    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    rows = (await conn.execute(q_msgs, {"cid": cid})).mappings().all()

  out: List[MessageOut] = []
  for r in rows:
    mt = (r["message_type"] or "").strip().lower()

    if mt == "match_intro":
      role: Literal["me", "them", "system"] = "system"
    else:
      role = "me" if str(r["sender_user_id"]) == str(user_id) else "them"

    out.append(
      MessageOut(
        id=r["id"],
        role=role,
        text=r["content_text"] or "",
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
  if not body.text or not body.text.strip():
    raise HTTPException(400, "Message is empty")

  q_conv = text(
    """
    select id, user_a_id, user_b_id
    from conversations
    where id::text = :cid
      and type = 'match'
      and active = true
    """
  )

  async with engine.begin() as conn:
    conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
    if not conv:
      raise HTTPException(404, "Conversation not found")

    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    other = conv["user_b_id"] if str(user_id) == str(conv["user_a_id"]) else conv["user_a_id"]

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
            :cid::uuid,
            :s::uuid,
            :r::uuid,
            'text',
            :t,
            now()
          )
          returning
            id::text as id,
            conversation_id,
            sender_user_id,
            receiver_user_id,
            content_text,
            created_at
          """
        ),
        {"cid": cid, "s": str(user_id), "r": str(other), "t": body.text.strip()},
      )
    ).mappings().first()

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
    where id::text = :cid
      and type = 'match'
    """
  )

  async with engine.begin() as conn:
    conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
    if not conv:
      raise HTTPException(404, "Conversation not found")

    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    await conn.execute(
      text(
        """
        update messages
        set read_at = now()
        where conversation_id::text = :cid
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


@router.post("/{cid}/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_conversation(cid: str, user_id: uuid.UUID = Depends(get_current_user_id)):
  q_conv = text(
    """
    select id, user_a_id, user_b_id
    from conversations
    where id::text = :cid
      and type = 'match'
      and active = true
    """
  )

  async with engine.begin() as conn:
    conv = (await conn.execute(q_conv, {"cid": cid})).mappings().first()
    if not conv:
      raise HTTPException(404, "Conversation not found")

    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    a = str(conv["user_a_id"])
    b = str(conv["user_b_id"])

    await conn.execute(
      text("update conversations set active = false, updated_at = now() where id::text = :cid"),
      {"cid": cid},
    )

    await conn.execute(
      text(
        """
        update users
        set active_chats = greatest(coalesce(active_chats, 0) - 1, 0),
            updated_at = now()
        where id::text = :a or id::text = :b
        """
      ),
      {"a": a, "b": b},
    )

  try:
    await manager.broadcast(cid, {"type": "ended", "conversation_id": cid})
  except Exception:
    pass


# ---------------------------
# ✅ Photos + Feedback (FIXED)
# ---------------------------

@router.get("/{cid}/match-feedback/status", response_model=MatchFeedbackStatusOut)
async def match_feedback_status(cid: str, user_id: uuid.UUID = Depends(get_current_user_id)):
  async with engine.begin() as conn:
    conv = await _get_conversation_or_404(conn, cid)
    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    passed = _is_24h_passed(conv["created_at"])
    my = await _get_my_feedback(conn, cid, user_id)

    already_answered = my is not None
    should_prompt = passed and not already_answered

    return MatchFeedbackStatusOut(
      conversation_id=cid,
      is_24h_passed=passed,
      already_answered=already_answered,
      should_prompt=should_prompt,
      my_response=my,
    )


@router.post("/{cid}/match-feedback", response_model=MatchFeedbackResultOut)
async def submit_match_feedback(cid: str, body: MatchFeedbackIn, user_id: uuid.UUID = Depends(get_current_user_id)):
  async with engine.begin() as conn:
    conv = await _get_conversation_or_404(conn, cid)
    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    # upsert my response
    await conn.execute(
      text(
        """
        insert into match_feedback(conversation_id, user_id, response, created_at)
        values (:cid::uuid, :uid::uuid, :resp, now())
        on conflict (conversation_id, user_id)
        do update set response = excluded.response, created_at = now()
        """
      ),
      {"cid": cid, "uid": str(user_id), "resp": bool(body.response)},
    )

    passed = _is_24h_passed(conv["created_at"])
    both = await _get_both_feedback(conn, cid, conv)

    a_yes = both["a"] is True
    b_yes = both["b"] is True
    both_yes = a_yes and b_yes

    photos_unlocked = bool(passed and both_yes)

  # notify clients when unlock happens
  if photos_unlocked:
    try:
      await manager.broadcast(cid, {"type": "photos-unlocked", "conversation_id": cid})
    except Exception:
      pass

  return MatchFeedbackResultOut(
    conversation_id=cid,
    saved=True,
    both_yes=both_yes,
    photos_unlocked=photos_unlocked,
  )


@router.get("/{cid}/photos", response_model=List[str])
async def conversation_photos(cid: str, user_id: uuid.UUID = Depends(get_current_user_id)):
  async with engine.begin() as conn:
    conv = await _get_conversation_or_404(conn, cid)
    if not _is_member(conv, user_id):
      raise HTTPException(403, "Not your conversation")

    passed = _is_24h_passed(conv["created_at"])
    both = await _get_both_feedback(conn, cid, conv)

    both_yes = (both["a"] is True) and (both["b"] is True)

    # 🔒 locked unless time passed + both yes
    if not (passed and both_yes):
      return []

    partner = _partner_id(conv, user_id)
    paths = await _get_user_photo_paths(conn, partner)

  # sign outside db tx
  return _signed_urls(paths)
