from pydantic import BaseModel
from typing import Optional, Literal


class MatchResultIneligible(BaseModel):
    status: Literal["ineligible"]
    user_id: int
    reason: str


class MatchResultNoMatch(BaseModel):
    status: Literal["no_match"]
    user_id: int


class MatchResultSuccess(BaseModel):
    status: Literal["matched"]
    user_a_id: int
    user_b_id: int
    score: float
    conversation_id: str  # assuming conv.id is UUID/text in your DB


# You can export a Union type to use in FastAPI response_model
MatchResult = MatchResultIneligible | MatchResultNoMatch | MatchResultSuccess
