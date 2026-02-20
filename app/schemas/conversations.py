# app/schemas/conversations.py
from pydantic import BaseModel
from typing import Optional, Literal, List

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
    role: Literal["me", "them"]
    text: Optional[str] = None
    ts: int

class SendMessageIn(BaseModel):
    text: str
