# app/api/routes/profile.py
import uuid
from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id

# ✅ NEW (for deleting user files)
from app.services.storage import delete_prefix

router = APIRouter(prefix="/me", tags=["me"])


# ---------- Models ----------

class ProfileOut(BaseModel):
    id: str
    full_name: str
    gender: Optional[str] = None
    age: Optional[int] = None

    # UPDATED: location comes from locations table
    location_id: Optional[int] = None
    location_label: Optional[str] = None  # "City, Admin, Country"

    personality_weight: float
    interests_weight: float
    verified: bool
    admin: bool = False

    # match preferences
    preferred_min_age: Optional[int] = None
    preferred_max_age: Optional[int] = None
    preferred_gender: Optional[str] = None  # from gender_pref

    # NEW: max distance preference (km stored in DB)
    preferred_max_distance_km: Optional[int] = None

    # NEW: last login (used for last_active computation)
    last_login_at: Optional[str] = None


class OnboardingIn(BaseModel):
    age: int = Field(..., ge=18, le=99)

    # UPDATED: store selected city id
    location_id: Optional[int] = None

    gender: Optional[str] = None
    preferred_min_age: Optional[int] = None
    preferred_max_age: Optional[int] = None
    preferred_gender: Optional[str] = None

    # NEW
    preferred_max_distance_km: Optional[int] = None


class WeightsIn(BaseModel):
    personality_weight: float = Field(ge=0.0, le=1.0)
    interests_weight: float = Field(ge=0.0, le=1.0)


class InterestIn(BaseModel):
    name: str


class InterestOut(BaseModel):
    id: int
    name: str
    category: Optional[str] = None


class MatchPreferencesIn(BaseModel):
    preferred_min_age: Optional[int] = None
    preferred_max_age: Optional[int] = None
    preferred_gender: Optional[str] = None  # "women" | "men" | "everyone"

    # NEW
    preferred_max_distance_km: Optional[int] = None


class CountryOut(BaseModel):
    country: str


class CityOut(BaseModel):
    id: int
    city: str
    admin_name: Optional[str] = None


# ---------- Helpers ----------

def _validate_gender_pref(g: Optional[str]) -> None:
    if g is not None and g not in ("women", "men", "everyone"):
        raise HTTPException(400, "preferred_gender must be one of: women, men, everyone")


def _validate_age_range(pmin: Optional[int], pmax: Optional[int]) -> None:
    if pmin is not None and pmax is not None and pmin > pmax:
        raise HTTPException(400, "preferred_min_age cannot be greater than preferred_max_age")


def _validate_distance_km(km: Optional[int]) -> None:
    if km is None:
        return
    if km < 1 or km > 500:
        raise HTTPException(400, "preferred_max_distance_km must be between 1 and 500")


def _validate_location_id(location_id: Optional[int]) -> None:
    if location_id is None:
        return
    if location_id <= 0:
        raise HTTPException(400, "location_id must be a positive integer")


async def _try_exec(conn, sql: str, params: dict) -> None:
    """
    Best-effort delete helper:
    - prevents 500s when a table/column doesn't exist across environments
    - keeps the account deletion endpoint resilient
    """
    try:
        await conn.execute(text(sql), params)
    except Exception:
        # Intentionally swallow to avoid 500 during delete-me.
        # You can add logging here if you want.
        pass


# ---------- Routes ----------

@router.post("/ping")
async def ping_me(user_id: uuid.UUID = Depends(get_current_user_id)):
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    update users
                    set last_login_at = now(),
                        updated_at = now()
                    where id = :uid
                    returning last_login_at
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().first()

    if not row:
        raise HTTPException(404, "User not found")

    return {"ok": True, "last_login_at": row["last_login_at"].isoformat()}


@router.get("/locations/countries", response_model=List[CountryOut])
async def list_countries():
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    select distinct country
                    from locations
                    where country is not null and country <> ''
                    order by country
                    """
                )
            )
        ).mappings().all()
    return rows


@router.get("/locations/cities", response_model=List[CityOut])
async def list_cities(country: str = Query(..., min_length=1, max_length=128)):
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    select id, city, admin_name
                    from locations
                    where country = :c
                    order by population desc nulls last, city asc
                    """
                )
            ,
                {"c": country},
            )
        ).mappings().all()
    return rows


@router.get("", response_model=ProfileOut)
async def get_me(user_id: uuid.UUID = Depends(get_current_user_id)):
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    select u.id::text,
                           u.full_name,
                           u.gender,
                           u.age,
                           u.location_id,
                           case
                             when l.id is null then null
                             else concat_ws(', ', l.city, l.admin_name, l.country)
                           end as location_label,
                           u.personality_weight,
                           u.interests_weight,
                           u.verified,
                           u.admin,
                           u.preferred_min_age,
                           u.preferred_max_age,
                           u.gender_pref as preferred_gender,
                           u.preferred_max_distance_km,
                           u.last_login_at
                    from users u
                    left join locations l on l.id = u.location_id
                    where u.id = :uid
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().first()

        if not row:
            row = (
                await conn.execute(
                    text(
                        """
                        insert into users (
                            id,
                            full_name,
                            verified,
                            personality_weight,
                            interests_weight,
                            created_at,
                            updated_at
                        )
                        values (:uid, 'New User', false, 0.5, 0.5, now(), now())
                        returning id::text,
                                  full_name,
                                  gender,
                                  age,
                                  location_id,
                                  null::text as location_label,
                                  personality_weight,
                                  interests_weight,
                                  verified,
                                  admin,
                                  preferred_min_age,
                                  preferred_max_age,
                                  gender_pref as preferred_gender,
                                  preferred_max_distance_km,
                                  last_login_at
                        """
                    ),
                    {"uid": str(user_id)},
                )
            ).mappings().first()

    out = dict(row)
    if out.get("last_login_at") is not None:
        out["last_login_at"] = out["last_login_at"].isoformat()

    return out


@router.get("/interests", response_model=List[InterestOut])
async def my_interests(user_id: uuid.UUID = Depends(get_current_user_id)):
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    select i.id, i.name, i.category
                    from user_interests ui
                    join interests i on i.id = ui.interest_id
                    where ui.user_id = :uid
                    order by i.name
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().all()
    return rows


@router.get("/interests/all", response_model=List[InterestOut])
async def all_interests():
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    select id, name, category
                    from interests
                    order by name
                    """
                )
            )
        ).mappings().all()
    return rows


@router.put("/interests", response_model=List[InterestOut])
async def put_my_interests(
    items: List[InterestIn] = Body(...),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    names = [i.name.strip() for i in items if i.name.strip()]
    async with engine.begin() as conn:
        ids: List[int] = []
        for name in names:
            r = (
                await conn.execute(
                    text(
                        """
                        insert into interests(name)
                        values (:name)
                        on conflict (name) do update set name = excluded.name
                        returning id
                        """
                    ),
                    {"name": name},
                )
            ).first()
            ids.append(r[0])

        await conn.execute(
            text("delete from user_interests where user_id = :uid"),
            {"uid": str(user_id)},
        )
        for iid in ids:
            await conn.execute(
                text("insert into user_interests(user_id, interest_id) values (:u,:i)"),
                {"u": str(user_id), "i": iid},
            )

        rows = (
            await conn.execute(
                text(
                    """
                    select i.id, i.name, i.category
                    from user_interests ui
                    join interests i on i.id = ui.interest_id
                    where ui.user_id = :uid
                    order by i.name
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().all()
    return rows


@router.put("/weights", response_model=ProfileOut)
async def update_weights(
    body: WeightsIn, user_id: uuid.UUID = Depends(get_current_user_id)
):
    if abs((body.personality_weight + body.interests_weight) - 1.0) > 0.01:
        raise HTTPException(400, "weights must sum to ≈ 1.0")

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    update users
                    set personality_weight = :pw,
                        interests_weight   = :iw,
                        updated_at         = now()
                    where id = :uid
                    returning id::text,
                              full_name,
                              gender,
                              age,
                              location_id,
                              null::text as location_label,
                              personality_weight,
                              interests_weight,
                              verified,
                              admin,
                              preferred_min_age,
                              preferred_max_age,
                              gender_pref as preferred_gender,
                              preferred_max_distance_km,
                              last_login_at
                    """
                ),
                {
                    "pw": body.personality_weight,
                    "iw": body.interests_weight,
                    "uid": str(user_id),
                },
            )
        ).mappings().first()

        if not row:
            raise HTTPException(404, "User not found")

    async with engine.begin() as conn:
        row2 = (
            await conn.execute(
                text(
                    """
                    select u.id::text,
                           u.full_name,
                           u.gender,
                           u.age,
                           u.location_id,
                           case
                             when l.id is null then null
                             else concat_ws(', ', l.city, l.admin_name, l.country)
                           end as location_label,
                           u.personality_weight,
                           u.interests_weight,
                           u.verified,
                           u.admin,
                           u.preferred_min_age,
                           u.preferred_max_age,
                           u.gender_pref as preferred_gender,
                           u.preferred_max_distance_km,
                           u.last_login_at
                    from users u
                    left join locations l on l.id = u.location_id
                    where u.id = :uid
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().first()

    out = dict(row2)
    if out.get("last_login_at") is not None:
        out["last_login_at"] = out["last_login_at"].isoformat()
    return out


@router.put("/preferences", response_model=ProfileOut)
async def update_match_preferences(
    body: MatchPreferencesIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    _validate_age_range(body.preferred_min_age, body.preferred_max_age)
    _validate_gender_pref(body.preferred_gender)
    _validate_distance_km(body.preferred_max_distance_km)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                update users
                set preferred_min_age           = coalesce(:pmin, preferred_min_age),
                    preferred_max_age           = coalesce(:pmax, preferred_max_age),
                    gender_pref                 = coalesce(:pgender, gender_pref),
                    preferred_max_distance_km   = coalesce(:pdist, preferred_max_distance_km),
                    updated_at                  = now()
                where id = :uid
                """
            ),
            {
                "pmin": body.preferred_min_age,
                "pmax": body.preferred_max_age,
                "pgender": body.preferred_gender,
                "pdist": body.preferred_max_distance_km,
                "uid": str(user_id),
            },
        )

        row = (
            await conn.execute(
                text(
                    """
                    select u.id::text,
                           u.full_name,
                           u.gender,
                           u.age,
                           u.location_id,
                           case
                             when l.id is null then null
                             else concat_ws(', ', l.city, l.admin_name, l.country)
                           end as location_label,
                           u.personality_weight,
                           u.interests_weight,
                           u.verified,
                           u.admin,
                           u.preferred_min_age,
                           u.preferred_max_age,
                           u.gender_pref as preferred_gender,
                           u.preferred_max_distance_km,
                           u.last_login_at
                    from users u
                    left join locations l on l.id = u.location_id
                    where u.id = :uid
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().first()

        if not row:
            raise HTTPException(404, "User not found")

    out = dict(row)
    if out.get("last_login_at") is not None:
        out["last_login_at"] = out["last_login_at"].isoformat()
    return out


@router.put("/onboarding", response_model=ProfileOut)
async def complete_onboarding(
    body: OnboardingIn,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    _validate_age_range(body.preferred_min_age, body.preferred_max_age)
    _validate_gender_pref(body.preferred_gender)
    _validate_distance_km(body.preferred_max_distance_km)
    _validate_location_id(body.location_id)

    if body.location_id is not None:
        async with engine.begin() as conn:
            exists_row = (
                await conn.execute(
                    text("select 1 from locations where id = :lid"),
                    {"lid": int(body.location_id)},
                )
            ).first()
        if not exists_row:
            raise HTTPException(400, "Invalid location_id (not found)")

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                update users
                set age                       = :age,
                    gender                    = :gender,
                    location_id               = :locid,
                    preferred_min_age         = :pmin,
                    preferred_max_age         = :pmax,
                    gender_pref               = :pgender,
                    preferred_max_distance_km = :pdist,
                    updated_at                = now()
                where id = :uid
                """
            ),
            {
                "age": body.age,
                "gender": body.gender,
                "locid": body.location_id,
                "pmin": body.preferred_min_age,
                "pmax": body.preferred_max_age,
                "pgender": body.preferred_gender,
                "pdist": body.preferred_max_distance_km,
                "uid": str(user_id),
            },
        )

        row = (
            await conn.execute(
                text(
                    """
                    select u.id::text,
                           u.full_name,
                           u.gender,
                           u.age,
                           u.location_id,
                           case
                             when l.id is null then null
                             else concat_ws(', ', l.city, l.admin_name, l.country)
                           end as location_label,
                           u.personality_weight,
                           u.interests_weight,
                           u.verified,
                           u.admin,
                           u.preferred_min_age,
                           u.preferred_max_age,
                           u.gender_pref as preferred_gender,
                           u.preferred_max_distance_km,
                           u.last_login_at
                    from users u
                    left join locations l on l.id = u.location_id
                    where u.id = :uid
                    """
                ),
                {"uid": str(user_id)},
            )
        ).mappings().first()

        if not row:
            raise HTTPException(404, "User not found")

    out = dict(row)
    if out.get("last_login_at") is not None:
        out["last_login_at"] = out["last_login_at"].isoformat()
    return out


# ✅ Delete my account (DB + storage)
@router.delete("", status_code=204)
async def delete_me(user_id: uuid.UUID = Depends(get_current_user_id)):
    uid = str(user_id)

    # Best-effort storage cleanup.
    for prefix in (f"gallery/{uid}/", f"kyc/{uid}/"):
        try:
            await delete_prefix(prefix)
        except Exception:
            pass

    async with engine.begin() as conn:
        # User-linked tables (safe)
        await _try_exec(conn, "delete from user_interests where user_id = :uid", {"uid": uid})
        await _try_exec(conn, "delete from kyc_rejection_reasons where user_id = :uid", {"uid": uid})
        await _try_exec(conn, "delete from kyc_verification where user_id = :uid", {"uid": uid})

        # Messages tied to user's conversations (FIXED: user_a_id/user_b_id)
        await _try_exec(
            conn,
            """
            delete from messages
            where conversation_id in (
              select id from conversations
              where user_a_id = :uid or user_b_id = :uid
            )
            """,
            {"uid": uid},
        )

        # Conversations (optional, because FK to users is ON DELETE CASCADE, but safe to do explicitly)
        await _try_exec(
            conn,
            """
            delete from conversations
            where user_a_id = :uid or user_b_id = :uid
            """,
            {"uid": uid},
        )

        # Matches: schema may vary between environments. Try both common conventions.
        await _try_exec(
            conn,
            """
            delete from matches
            where user_a_id = :uid or user_b_id = :uid
            """,
            {"uid": uid},
        )
        await _try_exec(
            conn,
            """
            delete from matches
            where user1_id = :uid or user2_id = :uid
            """,
            {"uid": uid},
        )

        res = await conn.execute(
            text("delete from users where id = :uid"),
            {"uid": uid},
        )

    # sanity check (driver may not always provide rowcount reliably)
    try:
        if res.rowcount == 0:
            raise HTTPException(404, "User not found")
    except Exception:
        pass

    return None
