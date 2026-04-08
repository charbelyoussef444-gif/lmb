# app/api/routes/ws.py
import json
import uuid
from logging import getLogger

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.db.engine import engine
from app.realtime.manager import manager
from app.deps.auth import get_ws_user_id
from app.core.firebase import send_push

log = getLogger("uvicorn.error")

ws_router = APIRouter()


@ws_router.websocket("/ws/conversations/{cid}")
async def ws_conversation(ws: WebSocket, cid: str):
    # 0) Validate CID
    try:
        cid_uuid = uuid.UUID(cid)
    except Exception:
        await ws.close(code=4400)  # Bad Request
        return

    # 1) Auth
    try:
        user_id = await get_ws_user_id(ws)
        user_uuid = uuid.UUID(str(user_id))
        log.info("WS connect ok: cid=%s user=%s", cid, user_id)
    except Exception:
        await ws.close(code=4401)  # Unauthorized
        return

    # 2) Authorization & load conversation (active + type=match)
    async with engine.begin() as conn:
        conv = (
            await conn.execute(
                text(
                    """
                    select id, user_a_id, user_b_id, active, type
                    from conversations
                    where id = :cid
                      and active = true
                      and type = 'match'
                    """
                ),
                {"cid": cid_uuid},
            )
        ).mappings().first()

    if not conv or user_uuid not in (conv["user_a_id"], conv["user_b_id"]):
        await ws.close(code=4403)  # Forbidden
        return

    other_uuid = conv["user_b_id"] if user_uuid == conv["user_a_id"] else conv["user_a_id"]

    # 3) Join the room
    await manager.join(cid, ws)
    log.info("WS joined room cid=%s user=%s", cid, user_id)

    await manager.broadcast(
        cid,
        {"type": "presence", "userId": str(user_uuid), "state": "joined"},
        exclude=ws,
    )

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            typ = msg.get("type")

            if typ == "send":
                text_body = (msg.get("text") or "").strip()
                if not text_body:
                    continue

                async with engine.begin() as conn:
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
                                  id::text as id,
                                  conversation_id::text as conversation_id,
                                  sender_user_id::text as sender_user_id,
                                  receiver_user_id::text as receiver_user_id,
                                  content_text,
                                  created_at
                                """
                            ),
                            {
                                "cid": cid_uuid,
                                "s": user_uuid,
                                "r": other_uuid,
                                "t": text_body,
                            },
                        )
                    ).mappings().first()

                if row:
                    await manager.broadcast(
                        cid,
                        {
                            "type": "message-new",
                            "data": {
                                "id": row["id"],
                                "conversation_id": row["conversation_id"],
                                "sender_user_id": row["sender_user_id"],
                                "receiver_user_id": row["receiver_user_id"],
                                "content_text": row["content_text"],
                                "created_at": row["created_at"].isoformat(),
                            },
                        },
                    )

                    # --- FCM push to receiver ---
                    try:
                        async with engine.begin() as conn2:
                            sender_row = (await conn2.execute(
                                text("SELECT full_name FROM users WHERE id = :uid"),
                                {"uid": user_uuid},
                            )).mappings().first()
                        sender_name = (sender_row or {}).get("full_name") or "Someone"
                        preview = (row["content_text"] or "")[:80]
                        await send_push(
                            user_id=str(other_uuid),
                            title=f"{sender_name}",
                            body=preview,
                            data={
                                "type": "new_message",
                                "conversationId": cid,
                                "partnerName": sender_name,
                                "channel_id": "messages",
                            },
                        )
                    except Exception as e:
                        log.exception("[ws] FCM push failed: %s", e)

            elif typ == "typing":
                await manager.broadcast(
                    cid,
                    {
                        "type": "typing",
                        "userId": str(user_uuid),
                        "isTyping": bool(msg.get("isTyping")),
                    },
                    exclude=ws,
                )

            elif typ == "read":
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            """
                            update messages
                            set read_at = now()
                            where conversation_id = :cid
                              and receiver_user_id = :me
                              and read_at is null
                            """
                        ),
                        {"cid": cid_uuid, "me": user_uuid},
                    )
                await manager.broadcast(cid, {"type": "read", "userId": str(user_uuid)})

            elif typ == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        log.info("WS disconnect: cid=%s user=%s", cid, user_id)
    finally:
        manager.leave(cid, ws)
        await manager.broadcast(
            cid,
            {"type": "presence", "userId": str(user_uuid), "state": "left"},
            exclude=ws,
        )
