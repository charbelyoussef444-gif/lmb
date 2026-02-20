# app/schemas/profile.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime

class ProfileOut(BaseModel):
    id: int
    full_name: str

    gender: Optional[str] = None
    gender_pref: Optional[str] = None  # <-- add this line
    age: Optional[int] = None
    location: Optional[str] = None

    verified: bool
    banned: bool
    admin: bool
    active_chats: int

    personality_weight: float
    interest_weight: float  # singular to match DB

    last_match_date: Optional[date] = None
    last_active: Optional[datetime] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



class WeightsIn(BaseModel):
    personality_weight: float = Field(ge=0.0, le=1.0)
    interest_weight: float = Field(ge=0.0, le=1.0)
    # IMPORTANT: frontend / backend must ensure personality_weight + interest_weight == 1
    # This is a business rule; you can enforce in route if you want.


class InterestIn(BaseModel):
    name: str


class InterestOut(BaseModel):
    id: int
    name: str
    category: Optional[str] = None
