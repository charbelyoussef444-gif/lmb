# app/api/routes/matching.py
import uuid
import math
import logging
import re
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal, Union, List, Dict, Set, Tuple, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from pydantic import BaseModel, Field

from openai import AsyncOpenAI

from app.db.engine import engine
from app.deps.auth import get_current_user_id

router = APIRouter(prefix="/matching", tags=["matching"])
logger = logging.getLogger(__name__)

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
openai_client: Optional[AsyncOpenAI] = AsyncOpenAI(api_key=_OPENAI_API_KEY) if _OPENAI_API_KEY else None

TRAIT_COUNT_EXPECTED = 10
MIN_PREF_COVERAGE = 0.7
COOLDOWN_HOURS = 24
MAX_ACTIVE_CHATS = 3
MIN_AGE = 18

MATCH_COOLDOWN_HOURS = 24
PHOTO_TABLE = "user_photos"

DEFAULT_MAX_DISTANCE_KM = 25
EARTH_RADIUS_KM = 6371.0

SYSTEM_BOT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MatchResultIneligible(BaseModel):
    status: Literal["ineligible"]
    user_id: str
    reason: str


class MatchResultNoMatch(BaseModel):
    status: Literal["no_match"]
    user_id: str


class MatchResultSuccess(BaseModel):
    status: Literal["matched"]
    user_a_id: str
    user_b_id: str
    score: float = Field(..., description="final match score 0..1-ish")
    conversation_id: str


MatchResult = Union[MatchResultIneligible, MatchResultNoMatch, MatchResultSuccess]


def _parse_iso_utc(ts: str) -> Optional[datetime]:
    """
    Accepts:
      - '2026-02-20T12:34:56Z'
      - '2026-02-20T12:34:56+00:00'
    Returns an aware datetime in UTC.
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


async def _get_checks(conn, user_id: uuid.UUID) -> Dict[str, Any]:
    u = (
        await conn.execute(
            text(
                """
                select id, verified, banned, last_match_date
                from users
                where id = :uid
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()

    if not u:
        return {
            "exists": False,
            "verified": False,
            "assessment_completed": False,
            "not_banned": False,
            "has_interest": False,
            "has_photos": False,
            "photos_count": 0,
            "interests_count": 0,
            "last_match_date": None,
        }

    assessment_row = (
        await conn.execute(
            text(
                """
                select 1
                from events
                where user_id = :uid
                  and event_name = 'assessment_completed'
                limit 1
                """
            ),
            {"uid": str(user_id)},
        )
    ).first()
    assessment_completed = bool(assessment_row)

    interests_row = (
        await conn.execute(
            text(
                """
                select count(*)::int as n
                from user_interests
                where user_id = :uid
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()
    interests_count = int(interests_row["n"]) if interests_row else 0
    has_interest = interests_count >= 1

    photos_count_row = (
        await conn.execute(
            text(
                f"""
                select
                  case
                    when to_regclass('public.{PHOTO_TABLE}') is null then 0
                    else (
                      select count(*)::int
                      from {PHOTO_TABLE}
                      where user_id = :uid
                        and kind = 'gallery'
                        and photo_url is not null
                        and btrim(photo_url) <> ''
                    )
                  end as n
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()

    photos_count = int(photos_count_row["n"]) if photos_count_row else 0
    has_photos = photos_count >= 3

    verified = bool(u.get("verified"))
    banned = bool(u.get("banned"))
    not_banned = not banned
    last_match_date = u.get("last_match_date")

    return {
        "exists": True,
        "verified": verified,
        "assessment_completed": assessment_completed,
        "not_banned": not_banned,
        "has_interest": has_interest,
        "has_photos": has_photos,
        "photos_count": photos_count,
        "interests_count": interests_count,
        "last_match_date": last_match_date,
    }


def _cooldown_next_eligible_iso(last_match_date) -> Optional[str]:
    if not last_match_date:
        return None
    try:
        nxt = last_match_date + timedelta(hours=MATCH_COOLDOWN_HOURS)
        if getattr(nxt, "tzinfo", None) is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return nxt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _build_missing_reasons(checks: Dict[str, Any]) -> Dict[str, Any]:
    missing = []
    if not checks.get("not_banned", False):
        missing.append("Your account is banned.")
    if checks.get("verified") is False:
        missing.append("Your account is not verified.")
    if checks.get("assessment_completed") is False:
        missing.append("Your compatibility assessment is not complete.")
    if checks.get("has_interest") is False:
        missing.append("Add at least 1 interest to your profile.")
    if checks.get("has_photos") is False:
        missing.append("Upload at least 3 gallery photos.")

    reason = missing[0] if missing else None
    return {"missing": missing, "reason": reason}


async def _fetch_user(conn, user_id: str):
    row = (
        await conn.execute(
            text(
                """
                select
                  id::text as id,
                  full_name,
                  gender,
                  gender_pref,
                  age,
                  verified,
                  banned,
                  active_chats,
                  personality_weight,
                  interests_weight,
                  last_match_date,
                  last_login_at,
                  created_at,
                  updated_at
                from users
                where id = :uid
                """
            ),
            {"uid": user_id},
        )
    ).mappings().first()
    return row


async def _fetch_user_bio(conn, user_id: str) -> str:
    row = (
        await conn.execute(
            text("select personal_bio from users where id = :uid"),
            {"uid": user_id},
        )
    ).mappings().first()
    return (row.get("personal_bio") or "").strip() if row else ""


async def _fetch_user_geo(conn, user_id: str) -> Optional[Dict[str, float]]:
    row = (
        await conn.execute(
            text(
                """
                select
                  l.lat as lat,
                  l.lng as lng,
                  coalesce(u.preferred_max_distance_km, :def_km)::float as max_km
                from users u
                join locations l on l.id = u.location_id
                where u.id = :uid
                limit 1
                """
            ),
            {"uid": user_id, "def_km": DEFAULT_MAX_DISTANCE_KM},
        )
    ).mappings().first()

    if not row:
        return None

    return {"lat": float(row["lat"]), "lng": float(row["lng"]), "max_km": float(row["max_km"])}


async def _personality_map(conn, user_id: str) -> Dict[int, float]:
    rows = (
        await conn.execute(
            text("select trait_id, score from user_personality where user_id = :uid"),
            {"uid": user_id},
        )
    ).mappings().all()
    return {r["trait_id"]: float(r["score"]) for r in rows}


async def _pref_map(conn, user_id: str) -> Dict[int, float]:
    rows = (
        await conn.execute(
            text("select trait_id, score from user_personality_pref where user_id = :uid"),
            {"uid": user_id},
        )
    ).mappings().all()
    return {r["trait_id"]: float(r["score"]) for r in rows}


async def _interests_set(conn, user_id: str) -> Set[str]:
    rows = (
        await conn.execute(
            text(
                """
                select i.name
                from user_interests ui
                join interests i on i.id = ui.interest_id
                where ui.user_id = :uid
                """
            ),
            {"uid": user_id},
        )
    ).mappings().all()
    return {r["name"] for r in rows}


async def _count_personality_rows(conn, user_id: str) -> int:
    r = (
        await conn.execute(
            text("select count(*) as c from user_personality where user_id = :uid"),
            {"uid": user_id},
        )
    ).first()
    return int(r[0]) if r else 0


async def _count_pref_rows(conn, user_id: str) -> int:
    r = (
        await conn.execute(
            text("select count(*) as c from user_personality_pref where user_id = :uid"),
            {"uid": user_id},
        )
    ).first()
    return int(r[0]) if r else 0


async def _already_matched(conn, a_id: str, b_id: str) -> bool:
    # IMPORTANT: bind UUID as uuid.UUID and do NOT use :a::uuid casts inside text()
    a_uuid = uuid.UUID(a_id)
    b_uuid = uuid.UUID(b_id)

    row = (
        await conn.execute(
            text(
                """
                select id
                from conversations
                where type = 'match'
                  and (
                        (user_a_id = :a and user_b_id = :b)
                     or (user_a_id = :b and user_b_id = :a)
                  )
                limit 1
                """
            ),
            {"a": a_uuid, "b": b_uuid},
        )
    ).first()
    return row is not None


def _directional_personality_score(prefs_from_A: Dict[int, float], traits_of_B: Dict[int, float]) -> Optional[float]:
    sims: List[float] = []
    for tid, score_val in prefs_from_A.items():
        if tid not in traits_of_B:
            continue
        b_val = traits_of_B[tid]
        diff = abs(score_val - b_val)
        sim = 1.0 - diff
        if sim < 0:
            sim = 0.0
        sims.append(sim)
    return (sum(sims) / len(sims)) if sims else None


def _personality_match_score(
    a_traits: Dict[int, float],
    b_traits: Dict[int, float],
    a_prefs: Dict[int, float],
    b_prefs: Dict[int, float],
) -> Optional[float]:
    pa = _directional_personality_score(a_prefs, b_traits)
    pb = _directional_personality_score(b_prefs, a_traits)
    if pa is None or pb is None:
        return None
    if pa <= 0 or pb <= 0:
        return 0.0
    return math.sqrt(pa * pb)


def _interest_score(a_set: Set[str], b_set: Set[str]) -> Optional[float]:
    if not a_set and not b_set:
        return None
    shared = a_set.intersection(b_set)

    def safe_div(n: int, d: int) -> float:
        return (n / d) if d > 0 else 0.0

    a_ratio = safe_div(len(shared), len(a_set))
    b_ratio = safe_div(len(shared), len(b_set))
    return (a_ratio + b_ratio) / 2.0


def _final_score(a_user_row, b_user_row, personality_score: Optional[float], interest_score: Optional[float]) -> float:
    pw_a = float(a_user_row["personality_weight"])
    pw_b = float(b_user_row["personality_weight"])
    pw = (pw_a + pw_b) / 2.0
    pw = max(0.0, min(1.0, pw))
    iw = 1.0 - pw

    p = personality_score if personality_score is not None else 0.0
    i = interest_score if interest_score is not None else 0.0
    return pw * p + iw * i


def _pref_allows_gender(pref: str, other_gender: str) -> bool:
    pref = (pref or "").strip().lower()
    other_gender = (other_gender or "").strip().lower()

    if not pref or pref == "everyone":
        return True
    if pref == "men":
        return other_gender == "man"
    if pref == "women":
        return other_gender == "woman"
    return True


def _gender_match_ok(a_row, b_row) -> bool:
    a_pref = (a_row.get("gender_pref") or "").strip().lower()
    b_pref = (b_row.get("gender_pref") or "").strip().lower()
    a_gender = (a_row.get("gender") or "").strip().lower()
    b_gender = (b_row.get("gender") or "").strip().lower()

    return _pref_allows_gender(a_pref, b_gender) and _pref_allows_gender(b_pref, a_gender)


async def _is_user_eligible_old_rules(conn, user_row) -> Tuple[bool, Optional[str]]:
    if not user_row:
        return False, "User not found"

    if user_row["age"] is None or user_row["age"] < MIN_AGE:
        return False, "Age restriction"

    if not user_row["verified"]:
        return False, "User not verified"

    active_chats = user_row["active_chats"] or 0
    if active_chats >= MAX_ACTIVE_CHATS:
        return False, "Active chats limit reached"

    last_match_ts = user_row["last_match_date"]
    if last_match_ts:
        if getattr(last_match_ts, "tzinfo", None) is not None:
            lm_utc = last_match_ts.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            lm_utc = last_match_ts
        cutoff_dt = datetime.utcnow() - timedelta(hours=COOLDOWN_HOURS)
        if lm_utc > cutoff_dt:
            return False, "Cooldown not finished"

    trait_count = await _count_personality_rows(conn, user_row["id"])
    if trait_count < TRAIT_COUNT_EXPECTED:
        return False, "Personality incomplete"

    pref_count = await _count_pref_rows(conn, user_row["id"])
    min_required = int(TRAIT_COUNT_EXPECTED * MIN_PREF_COVERAGE)
    if pref_count < min_required:
        return False, "Preferences incomplete"

    return True, None


async def _is_user_eligible_new_checks(conn, user_id: uuid.UUID) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    checks = await _get_checks(conn, user_id)

    if not checks.get("exists"):
        return False, "User not found.", checks

    req_ok = (
        checks["verified"]
        and checks["assessment_completed"]
        and checks["not_banned"]
        and checks["has_interest"]
        and checks["has_photos"]
    )

    if not req_ok:
        built = _build_missing_reasons(checks)
        return False, built["reason"] or "Not eligible", checks

    next_iso = _cooldown_next_eligible_iso(checks.get("last_match_date"))
    if next_iso:
        nxt_dt = _parse_iso_utc(next_iso)
        if nxt_dt is not None:
            now_utc = datetime.now(timezone.utc)
            if nxt_dt > now_utc:
                return False, "Please wait before requesting another match.", checks
        else:
            # If parsing fails, be conservative (avoid letting people bypass cooldown)
            return False, "Please wait before requesting another match.", checks

    return True, None, checks


async def _fetch_candidate_pool(conn, a_user_id: str, a_lat: float, a_lng: float, a_max_km: float):
    min_required_prefs = int(TRAIT_COUNT_EXPECTED * MIN_PREF_COVERAGE)

    # IMPORTANT: bind UUID as uuid.UUID and do NOT use :me::uuid
    me_uuid = uuid.UUID(a_user_id)

    rows = (
        await conn.execute(
            text(
                """
                with trait_counts as (
                    select user_id, count(*) as trait_count
                    from user_personality
                    group by user_id
                ),
                pref_counts as (
                    select user_id, count(*) as pref_count
                    from user_personality_pref
                    group by user_id
                ),
                c as (
                    select
                      u.id::text as id,
                      u.full_name,
                      u.gender,
                      u.gender_pref,
                      u.age,
                      u.verified,
                      u.banned,
                      u.active_chats,
                      u.personality_weight,
                      u.interests_weight,
                      u.last_match_date,
                      u.last_login_at,
                      u.created_at,
                      u.updated_at,
                      tc.trait_count,
                      pc.pref_count,
                      (
                        :R * acos(
                          least(
                            1.0,
                            greatest(
                              -1.0,
                              cos(radians(:a_lat)) * cos(radians(lb.lat)) *
                              cos(radians(lb.lng) - radians(:a_lng)) +
                              sin(radians(:a_lat)) * sin(radians(lb.lat))
                            )
                          )
                        )
                      ) as distance_km,
                      coalesce(u.preferred_max_distance_km, :def_km)::float as b_max_km
                    from users u
                    join trait_counts tc on tc.user_id = u.id
                    join pref_counts  pc on pc.user_id = u.id
                    join locations lb on lb.id = u.location_id
                    where u.id <> :me
                      and u.location_id is not null
                      and u.verified = true
                      and (u.active_chats < :max_chats)
                      and tc.trait_count >= :trait_expected
                      and pc.pref_count  >= :pref_required
                      and (u.banned is null or u.banned = false)
                )
                select
                  id,
                  full_name,
                  gender,
                  gender_pref,
                  age,
                  verified,
                  banned,
                  active_chats,
                  personality_weight,
                  interests_weight,
                  last_match_date,
                  last_login_at,
                  created_at,
                  updated_at,
                  trait_count,
                  pref_count,
                  distance_km
                from c
                where distance_km <= :a_max_km
                  and distance_km <= b_max_km
                """
            ),
            {
                "me": me_uuid,
                "max_chats": MAX_ACTIVE_CHATS,
                "trait_expected": TRAIT_COUNT_EXPECTED,
                "pref_required": min_required_prefs,
                "a_lat": float(a_lat),
                "a_lng": float(a_lng),
                "a_max_km": float(a_max_km),
                "def_km": DEFAULT_MAX_DISTANCE_KM,
                "R": EARTH_RADIUS_KM,
            },
        )
    ).mappings().all()

    return rows


async def _create_conversation_and_stamp(conn, a_id: str, b_id: str, match_score: float, match_distance_km: Optional[float]) -> str:
    # IMPORTANT: bind UUID as uuid.UUID and do NOT use :x::uuid in text()
    new_cid_uuid = uuid.uuid4()
    a_uuid = uuid.UUID(a_id)
    b_uuid = uuid.UUID(b_id)

    await conn.execute(
        text(
            """
            insert into conversations(
                id, type, user_a_id, user_b_id, active,
                match_score, match_distance_km,
                created_at, updated_at
            )
            values (
                :cid, 'match', :a, :b, true,
                :ms, :md,
                now(), now()
            )
            """
        ),
        {
            "cid": new_cid_uuid,
            "a": a_uuid,
            "b": b_uuid,
            "ms": float(match_score),
            "md": float(match_distance_km) if match_distance_km is not None else None,
        },
    )

    await conn.execute(
        text(
            """
            update users
            set last_match_date = now(),
                updated_at = now()
            where id = :a or id = :b
            """
        ),
        {"a": a_uuid, "b": b_uuid},
    )

    await conn.execute(
        text(
            """
            update users
            set active_chats = active_chats + 1,
                updated_at = now()
            where id = :a or id = :b
            """
        ),
        {"a": a_uuid, "b": b_uuid},
    )

    chk = (
        await conn.execute(
            text(
                """
                select id
                from conversations
                where id = :cid
                  and type = 'match'
                  and active = true
                """
            ),
            {"cid": new_cid_uuid},
        )
    ).first()

    if not chk:
        raise RuntimeError("Conversation insert sanity check failed.")

    return str(new_cid_uuid)


GPT_MATCH_INTRO_SYSTEM_PROMPT = """
You write ONE shared conversation-starter message for a dating app.

Rules:
- Output MUST be valid JSON only. No markdown. No extra keys.
- Max 90 words.
- Tone: friendly, confident, playful-with-edge, never cringe.
- Use only what appears in the bios + interests. Do NOT invent facts.
- The final line MUST be ONE clear, fun, inviting question.
- End with EXACTLY ONE question mark (only one in the entire message).

JSON:
{"intro": "..."}
"""


def _sanitize_bio_for_gpt(text_: str, limit: int = 800) -> str:
    t = (text_ or "").strip()
    if not t:
        return ""
    t = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "", t)
    t = re.sub(r"\+?\d[\d\s().-]{7,}\d", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


async def _generate_shared_gpt_intro(
    a_name: str,
    b_name: str,
    a_bio: str,
    b_bio: str,
    shared_interests: List[str],
    a_unique_interests: List[str],
    b_unique_interests: List[str],
) -> Optional[str]:
    if openai_client is None:
        return None

    a_first = (a_name or "User A").strip().split()[0] if (a_name or "").strip() else "User A"
    b_first = (b_name or "User B").strip().split()[0] if (b_name or "").strip() else "User B"

    payload = {
        "user_a": {
            "name": a_first,
            "personal_bio": _sanitize_bio_for_gpt(a_bio),
            "shared_interests": shared_interests[:8],
            "unique_interests": a_unique_interests[:8],
        },
        "user_b": {
            "name": b_first,
            "personal_bio": _sanitize_bio_for_gpt(b_bio),
            "shared_interests": shared_interests[:8],
            "unique_interests": b_unique_interests[:8],
        },
    }

    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.85,
            messages=[
                {"role": "system", "content": GPT_MATCH_INTRO_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )

        content = (resp.choices[0].message.content or "").strip()
        data = json.loads(content)
        intro = data.get("intro")

        if not intro or not isinstance(intro, str):
            return None

        intro = intro.strip()

        if intro.count("?") != 1:
            parts = intro.split("?")
            if len(parts) >= 2:
                intro = "?".join(parts[:-1]).replace("?", "") + "?" + parts[-1]
                intro = re.sub(r"\s+", " ", intro).strip()

        words = intro.split()
        if len(words) > 95:
            intro = " ".join(words[:95]).strip()

        return intro

    except Exception:
        logger.exception("GPT shared intro generation failed")
        return None


async def _insert_match_intro_messages(
    conn,
    conversation_id: str,
    user_a_id: str,
    user_b_id: str,
    a_name: str,
    b_name: str,
    a_interests: Set[str],
    b_interests: Set[str],
) -> None:
    a_bio = await _fetch_user_bio(conn, user_a_id)
    b_bio = await _fetch_user_bio(conn, user_b_id)

    shared = sorted(list(a_interests.intersection(b_interests)))
    a_unique = sorted(list(a_interests - b_interests))
    b_unique = sorted(list(b_interests - a_interests))

    intro_text = await _generate_shared_gpt_intro(
        a_name=a_name or "User A",
        b_name=b_name or "User B",
        a_bio=a_bio,
        b_bio=b_bio,
        shared_interests=shared,
        a_unique_interests=a_unique,
        b_unique_interests=b_unique,
    )

    if not intro_text:
        intro_text = "You two might actually have a conversation that isn’t small talk. What’s something you’ve been genuinely excited about lately?"

    cid_uuid = uuid.UUID(conversation_id)
    ra_uuid = uuid.UUID(user_a_id)
    rb_uuid = uuid.UUID(user_b_id)

    # IMPORTANT: bind UUIDs as uuid.UUID and do NOT use :x::uuid casts
    await conn.execute(
        text(
            """
            insert into messages(
              id,
              conversation_id,
              sender_bot_id,
              receiver_user_id,
              message_type,
              content_text,
              created_at,
              delivered_at,
              read_at
            )
            values
              (gen_random_uuid(), :cid, :bot, :ra, 'match_intro', :txt, now(), now(), now()),
              (gen_random_uuid(), :cid, :bot, :rb, 'match_intro', :txt, now(), now(), now())
            """
        ),
        {
            "cid": cid_uuid,
            "bot": SYSTEM_BOT_ID,
            "ra": ra_uuid,
            "rb": rb_uuid,
            "txt": intro_text,
        },
    )


@router.get("/status")
async def get_matching_status(user_id: uuid.UUID = Depends(get_current_user_id)):
    async with engine.begin() as conn:
        checks = await _get_checks(conn, user_id)

        if not checks.get("exists"):
            return {
                "eligible": False,
                "reason": "User not found.",
                "missing": ["User not found."],
                "checks": {
                    "verified": False,
                    "assessment_completed": False,
                    "not_banned": False,
                    "has_interest": False,
                    "has_photos": False,
                    "photos_count": 0,
                    "interests_count": 0,
                },
                "next_eligible_ts_utc": None,
            }

        next_iso = _cooldown_next_eligible_iso(checks.get("last_match_date"))

        req_ok = (
            checks["verified"]
            and checks["assessment_completed"]
            and checks["not_banned"]
            and checks["has_interest"]
            and checks["has_photos"]
        )

        eligible_now = req_ok
        if req_ok and next_iso:
            nxt_dt = _parse_iso_utc(next_iso)
            if nxt_dt is not None:
                eligible_now = nxt_dt <= datetime.now(timezone.utc)
            else:
                eligible_now = False

        built = _build_missing_reasons(checks)
        reason = built["reason"]

        if req_ok and not eligible_now and next_iso:
            reason = "Please wait before requesting another match."

        return {
            "eligible": eligible_now,
            "reason": reason,
            "missing": built["missing"] if not req_ok else ([] if eligible_now else ["Please wait before requesting another match."]),
            "checks": {
                "verified": checks["verified"],
                "assessment_completed": checks["assessment_completed"],
                "not_banned": checks["not_banned"],
                "has_interest": checks["has_interest"],
                "has_photos": checks["has_photos"],
                "photos_count": checks["photos_count"],
                "interests_count": checks["interests_count"],
            },
            "next_eligible_ts_utc": next_iso,
        }


@router.post("/generate", response_model=MatchResult)
async def generate_match(user_id: uuid.UUID = Depends(get_current_user_id)):
    convo_id: Optional[str] = None
    a_id: Optional[str] = None
    b_id: Optional[str] = None
    final_score_for_db: float = 0.0
    a_interests: Set[str] = set()
    b_interests_for_intro: Set[str] = set()
    a_name: str = "You"
    b_name: str = "They"

    # TX #1: compute + create conversation
    async with engine.begin() as conn:
        ok_new, reason_new, _checks = await _is_user_eligible_new_checks(conn, user_id)
        if not ok_new:
            return MatchResultIneligible(status="ineligible", user_id=str(user_id), reason=reason_new or "not_eligible")

        a_row = await _fetch_user(conn, str(user_id))

        ok_old, reason_old = await _is_user_eligible_old_rules(conn, a_row)
        if not ok_old:
            return MatchResultIneligible(status="ineligible", user_id=str(user_id), reason=reason_old or "not_eligible")

        a_geo = await _fetch_user_geo(conn, a_row["id"])
        if not a_geo:
            return MatchResultIneligible(status="ineligible", user_id=str(user_id), reason="Set your location before requesting a match.")

        a_traits = await _personality_map(conn, a_row["id"])
        a_prefs = await _pref_map(conn, a_row["id"])
        a_interests = await _interests_set(conn, a_row["id"])
        a_name = a_row.get("full_name") or "You"

        candidates = await _fetch_candidate_pool(
            conn=conn,
            a_user_id=a_row["id"],
            a_lat=a_geo["lat"],
            a_lng=a_geo["lng"],
            a_max_km=a_geo["max_km"],
        )

        best_candidate = None
        best_score_val: Optional[float] = None
        best_distance_km: Optional[float] = None

        for b_row in candidates:
            if not _gender_match_ok(a_row, b_row):
                continue

            b_ok_new, _, __ = await _is_user_eligible_new_checks(conn, uuid.UUID(b_row["id"]))
            if not b_ok_new:
                continue

            b_ok_old, _ = await _is_user_eligible_old_rules(conn, b_row)
            if not b_ok_old:
                continue

            if await _already_matched(conn, a_row["id"], b_row["id"]):
                continue

            b_traits = await _personality_map(conn, b_row["id"])
            b_prefs = await _pref_map(conn, b_row["id"])
            b_interests = await _interests_set(conn, b_row["id"])

            p_score = _personality_match_score(a_traits=a_traits, b_traits=b_traits, a_prefs=a_prefs, b_prefs=b_prefs)
            i_score = _interest_score(a_interests, b_interests)

            if p_score is None and i_score is None:
                continue

            fs = _final_score(a_user_row=a_row, b_user_row=b_row, personality_score=p_score, interest_score=i_score)

            if best_score_val is None or fs > best_score_val:
                best_score_val = fs
                best_candidate = b_row
                best_distance_km = float(b_row.get("distance_km")) if b_row.get("distance_km") is not None else None
                b_interests_for_intro = b_interests

        if best_candidate is None:
            return MatchResultNoMatch(status="no_match", user_id=str(user_id))

        final_score_for_db = float(best_score_val or 0.0)

        convo_id = await _create_conversation_and_stamp(
            conn,
            a_row["id"],
            best_candidate["id"],
            final_score_for_db,
            best_distance_km,
        )

        a_id = a_row["id"]
        b_id = best_candidate["id"]
        b_name = best_candidate.get("full_name") or "They"

    # TX #2: insert intro message (non-fatal)
    if convo_id and a_id and b_id:
        try:
            async with engine.begin() as conn2:
                await _insert_match_intro_messages(
                    conn=conn2,
                    conversation_id=convo_id,
                    user_a_id=a_id,
                    user_b_id=b_id,
                    a_name=a_name,
                    b_name=b_name,
                    a_interests=a_interests,
                    b_interests=b_interests_for_intro,
                )
        except Exception as e:
            logger.exception("Failed to insert match intro (non-fatal): %s", e)

    return MatchResultSuccess(
        status="matched",
        user_a_id=a_id or str(user_id),
        user_b_id=b_id or "",
        score=round(final_score_for_db, 4),
        conversation_id=convo_id or "",
    )
