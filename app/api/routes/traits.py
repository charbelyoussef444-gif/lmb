# app/api/routes/traits.py
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from pydantic import BaseModel

from app.db.engine import engine
from app.deps.auth import get_current_user_id

router = APIRouter(tags=["traits"])


class TraitOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    score: Optional[float] = None  # 👈 user's current self score for this trait, if any


@router.get("/traits", response_model=List[TraitOut])
async def list_traits(user_id: uuid.UUID = Depends(get_current_user_id)):
    """
    Return the full personality trait catalogue PLUS this user's current scores.

    - Definitions from personality_traits
    - Scores from user_personality (user-specific)
    """
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                """
                SELECT
                    t.id,
                    t.name,
                    t.description,
                    up.score
                FROM personality_traits AS t
                LEFT JOIN user_personality AS up
                  ON up.trait_id = t.id
                 AND up.user_id = :uid
                ORDER BY t.id ASC
                """
            ),
            {"uid": str(user_id)},
        )
        rows = res.mappings().all()

    return [TraitOut(**dict(r)) for r in rows]
