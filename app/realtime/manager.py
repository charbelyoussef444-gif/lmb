# app/realtime/manager.py
from typing import Dict, Set
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, Set[WebSocket]] = {}

    async def join(self, room: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(room, set()).add(ws)

    def leave(self, room: str, ws: WebSocket):
        members = self.rooms.get(room)
        if members:
            members.discard(ws)
            if not members:
                self.rooms.pop(room, None)

    async def broadcast(self, room: str, data: dict, exclude: WebSocket | None = None):
        for conn in list(self.rooms.get(room, ())):
            if exclude is not None and conn is exclude:
                continue
            try:
                await conn.send_json(data)
            except Exception:
                # drop dead sockets
                self.leave(room, conn)

manager = ConnectionManager()
