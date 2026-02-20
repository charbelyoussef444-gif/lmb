# app/api/routes/ws.py
import json
from logging import getLogger

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.db.engine import engine
from app.realtime.manager import manager
from app.deps.auth import get_ws_user_id

log = getLogger("uvicorn.error")

ws_router = APIRouter()


@ws_router.websocket("/ws/conversations/{cid}")
async def ws_conversation(ws: WebSocket, cid: str):
    # 1) Auth
    try:
        user_id = await get_ws_user_id(ws)
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
                    where id::text = :cid
                      and active = true
                      and type = 'match'
                    """
                ),
                {"cid": cid},
            )
        ).mappings().first()

    if not conv or str(user_id) not in (str(conv["user_a_id"]), str(conv["user_b_id"])):
        await ws.close(code=4403)  # Forbidden
        return

    other = str(conv["user_b_id"]) if str(user_id) == str(conv["user_a_id"]) else str(conv["user_a_id"])

    # 3) Join the room
    await manager.join(cid, ws)
    log.info("WS joined room cid=%s user=%s", cid, user_id)

    await manager.broadcast(
        cid,
        {"type": "presence", "userId": str(user_id), "state": "joined"},
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
                            {
                                "cid": cid,
                                "s": str(user_id),
                                "r": other,
                                "t": text_body,
                            },
                        )
                    ).mappings().first()

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

            elif typ == "typing":
                await manager.broadcast(
                    cid,
                    {
                        "type": "typing",
                        "userId": str(user_id),
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
                            where conversation_id::text = :cid
                              and receiver_user_id = :me
                              and read_at is null
                            """
                        ),
                        {"cid": cid, "me": str(user_id)},
                    )
                await manager.broadcast(cid, {"type": "read", "userId": str(user_id)})

            elif typ == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        log.info("WS disconnect: cid=%s user=%s", cid, user_id)
    finally:
        manager.leave(cid, ws)
        await manager.broadcast(
            cid,
            {"type": "presence", "userId": str(user_id), "state": "left"},
            exclude=ws,
        )
