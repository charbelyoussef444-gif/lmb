# app/deps/ws_auth.py
import uuid
from fastapi import WebSocket, WebSocketException, status
from app.deps.auth import decode_token_or_header_user_id  # write this to mirror get_current_user_id

async def get_ws_user_id(ws: WebSocket) -> uuid.UUID:
    # Option A: Bearer token in query string: /ws/conversations/{cid}?token=...
    token = ws.query_params.get("token")
    # Option B: custom header sent during upgrade (some clients support it)
    if not token:
        token = ws.headers.get("Authorization", "").replace("Bearer ", "").strip()
    try:
        user_id = decode_token_or_header_user_id(token, ws.headers)  # implement like your HTTP auth
        return uuid.UUID(str(user_id))
    except Exception:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
