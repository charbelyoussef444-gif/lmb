# app/api/routes/chatbot.py

import os
import uuid
import json
import logging
import re
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.db.engine import engine
from app.deps.auth import get_current_user_id
from app.api.routes import chatbot_phase1, chatbot_phase2
from app.api.routes.mcq_config import PHASE1_ORDER, PHASE2_ORDER, TRAIT_IDS
from app.api.routes import mcq_dynamic

router = APIRouter(prefix="/chatbot", tags=["chatbot"])
logger = logging.getLogger(__name__)

CONF_THRESHOLD = 0.2

# Phase-boundary confidence target
CONF_TARGET = 0.6
MAX_BOOSTERS_PER_PHASE = 6

# Bio generation knobs
BIO_HISTORY_LIMIT = 250
BIO_MIN_CONFIDENCE = 0.2
BIO_MODEL = os.getenv("BIO_MODEL", "gpt-4o")
BIO_TEMPERATURE = float(os.getenv("BIO_TEMPERATURE", "0.5"))
BIO_MAX_TOKENS = int(os.getenv("BIO_MAX_TOKENS", "220"))
BIO_MAX_RETRIES = int(os.getenv("BIO_MAX_RETRIES", "1"))

BIO_BANNED_SUBSTRINGS = [
    "meaningful",
    "open communication",
    "journey",
    "growth",
    "honesty",
    "emotionally available",
    "nurturing",
    "bond",
    "trust",
]

# One-time freeform at the start of pref phase
PREF_FREEFORM_QID = "PREF_FREE_1"


def _pref_intro_prompt() -> str:
    return (
        "Optional (one-time): describe your ideal match.\n"
        "Write anything you want. Or tap Skip.\n\n"
        "[[freeform:ideal_match]]\n\n"
        f"[[mcq:{PREF_FREEFORM_QID}]]"
    )


def _maybe_load_json(x: Any) -> Any:
    if isinstance(x, str):
        s = x.strip()
        if s and (s.startswith("{") or s.startswith("[")):
            try:
                return json.loads(s)
            except Exception:
                return x
    return x


def _parse_user_answer(user_text: str) -> Dict[str, Any]:
    t = (user_text or "").strip()
    if not t:
        return {"opt": None}

    opt = t[:1].upper()
    if opt in ("A", "B", "C"):
        return {"opt": opt}

    if opt == "D":
        rest = t[1:].lstrip(" :.-").strip()
        return {"opt": "D", "text": rest}

    # treat longer free text as D
    if len(t) >= 3:
        return {"opt": "D", "text": t}

    return {"opt": None}


async def get_or_create_chatbot_conversation(conn, user_id: uuid.UUID) -> uuid.UUID:
    res = await conn.execute(
        text(
            """
            SELECT id
            FROM conversations
            WHERE user_a_id = :uid
              AND user_b_id IS NULL
              AND type = 'onboarding_bot'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ),
        {"uid": str(user_id)},
    )
    row = res.mappings().first()
    if row:
        return uuid.UUID(str(row["id"]))

    conv_id = uuid.uuid4()
    await conn.execute(
        text(
            """
            INSERT INTO conversations (id, type, user_a_id, user_b_id, active)
            VALUES (:cid, 'onboarding_bot', :uid, NULL, TRUE)
            """
        ),
        {"cid": str(conv_id), "uid": str(user_id)},
    )
    return conv_id


async def log_user_message(conn, conversation_id: uuid.UUID, user_id: uuid.UUID, content: str):
    await conn.execute(
        text(
            """
            INSERT INTO messages (
                conversation_id, sender_user_id, receiver_bot_id, message_type, content_text
            )
            VALUES (:cid, :uid, gen_random_uuid(), 'text', :content)
            """
        ),
        {"cid": str(conversation_id), "uid": str(user_id), "content": content},
    )


async def log_bot_message(conn, conversation_id: uuid.UUID, user_id: uuid.UUID, content: str):
    await conn.execute(
        text(
            """
            INSERT INTO messages (
                conversation_id, sender_bot_id, receiver_user_id, message_type, content_text
            )
            VALUES (:cid, gen_random_uuid(), :uid, 'text', :content)
            """
        ),
        {"cid": str(conversation_id), "uid": str(user_id), "content": content},
    )


async def fetch_conversation_messages(conn, conversation_id: uuid.UUID, limit: int = 200) -> list[dict]:
    res = await conn.execute(
        text(
            """
            SELECT sender_user_id, sender_bot_id, content_text, created_at
            FROM messages
            WHERE conversation_id = :cid AND content_text IS NOT NULL
            ORDER BY created_at ASC
            LIMIT :lim
            """
        ),
        {"cid": str(conversation_id), "lim": limit},
    )
    rows = res.mappings().all()
    out: list[dict] = []
    for r in rows:
        role = "assistant" if r["sender_bot_id"] is not None else "user"
        out.append({"role": role, "content": r["content_text"], "ts": r["created_at"].isoformat()})
    return out


async def _fetch_trait_progress(conn, user_id: uuid.UUID, threshold: float = CONF_THRESHOLD) -> Dict[str, Any]:
    uid = str(user_id)

    self_total_res = await conn.execute(
        text("SELECT COUNT(*) AS n FROM user_personality WHERE user_id = :uid"),
        {"uid": uid},
    )
    self_total = int(self_total_res.mappings().first()["n"] or 0)

    pref_total_res = await conn.execute(
        text("SELECT COUNT(*) AS n FROM user_personality_pref WHERE user_id = :uid"),
        {"uid": uid},
    )
    pref_total = int(pref_total_res.mappings().first()["n"] or 0)

    if self_total <= 0:
        self_total = len(TRAIT_IDS)
    if pref_total <= 0:
        pref_total = len(TRAIT_IDS)

    self_ans_res = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS n
            FROM user_personality
            WHERE user_id = :uid AND COALESCE(confidence, 0) > :thr
            """
        ),
        {"uid": uid, "thr": float(threshold)},
    )
    self_answered = int(self_ans_res.mappings().first()["n"] or 0)

    pref_ans_res = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS n
            FROM user_personality_pref
            WHERE user_id = :uid AND COALESCE(confidence, 0) > :thr
            """
        ),
        {"uid": uid, "thr": float(threshold)},
    )
    pref_answered = int(pref_ans_res.mappings().first()["n"] or 0)

    return {
        "threshold": float(threshold),
        "total": int(self_total + pref_total),
        "answered": int(self_answered + pref_answered),
        "self_total": int(self_total),
        "pref_total": int(pref_total),
        "self_answered": int(self_answered),
        "pref_answered": int(pref_answered),
    }


# ----------------------------
# MCQ RUN STATE
# ----------------------------

async def _get_or_create_run(conn, user_id: uuid.UUID, convo_id: uuid.UUID) -> dict:
    res = await conn.execute(
        text(
            """
            SELECT *
            FROM onboarding_mcq_runs
            WHERE user_id = :uid AND conversation_id = :cid
            LIMIT 1
            """
        ),
        {"uid": str(user_id), "cid": str(convo_id)},
    )
    row = res.mappings().first()
    if row:
        return dict(row)

    rid = uuid.uuid4()
    await conn.execute(
        text(
            """
            INSERT INTO onboarding_mcq_runs (id, user_id, conversation_id, phase, current_qid, base_index)
            VALUES (:rid, :uid, :cid, 'self', NULL, 0)
            """
        ),
        {"rid": str(rid), "uid": str(user_id), "cid": str(convo_id)},
    )
    res2 = await conn.execute(text("SELECT * FROM onboarding_mcq_runs WHERE id = :rid"), {"rid": str(rid)})
    return dict(res2.mappings().first())


async def _update_run(conn, run_id: uuid.UUID, **fields):
    sets = []
    params = {"rid": str(run_id)}
    for k, v in fields.items():
        if k == "eval_state":
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = v
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    sets.append("updated_at = now()")
    sql = "UPDATE onboarding_mcq_runs SET " + ", ".join(sets) + " WHERE id = :rid"
    await conn.execute(text(sql), params)


async def _upsert_answer(
    conn,
    run_id: uuid.UUID,
    phase: str,
    qid: str,
    opt: str,
    free_text: Optional[str],
):
    await conn.execute(
        text(
            """
            INSERT INTO onboarding_mcq_answers (run_id, phase, qid, opt, free_text)
            VALUES (:rid, :ph, :qid, :opt, :txt)
            ON CONFLICT (run_id, phase, qid)
            DO UPDATE SET opt = EXCLUDED.opt, free_text = EXCLUDED.free_text, updated_at = now()
            """
        ),
        {"rid": str(run_id), "ph": phase, "qid": qid, "opt": opt, "txt": free_text},
    )


async def _store_dynamic_cfg(conn, run_id: uuid.UUID, phase: str, cfg: Dict[str, Any]):
    qid = str(cfg.get("id"))
    await conn.execute(
        text(
            """
            INSERT INTO onboarding_mcq_dynamic (run_id, phase, qid, cfg)
            VALUES (:rid, :ph, :qid, CAST(:cfg AS jsonb))
            ON CONFLICT (run_id, phase, qid)
            DO UPDATE SET cfg = EXCLUDED.cfg
            """
        ),
        {"rid": str(run_id), "ph": phase, "qid": qid, "cfg": json.dumps(cfg, ensure_ascii=False)},
    )


async def _fetch_eval_state(conn, run_id: uuid.UUID) -> dict:
    res = await conn.execute(
        text("SELECT eval_state FROM onboarding_mcq_runs WHERE id = :rid"),
        {"rid": str(run_id)},
    )
    row = res.mappings().first()
    st = row["eval_state"] if row else {}
    st = _maybe_load_json(st)
    return st if isinstance(st, dict) else {}


def _avg_conf(results: Dict[int, Dict[str, float]], trait_ids: List[int]) -> float:
    if not trait_ids:
        return 1.0
    s = 0.0
    for tid in trait_ids:
        try:
            s += float(results.get(int(tid), {}).get("confidence", 0.0))
        except Exception:
            s += 0.0
    return s / max(1, len(trait_ids))


async def _answered_qids(conn, run_id: uuid.UUID, phase: str) -> List[str]:
    res = await conn.execute(
        text(
            """
            SELECT qid
            FROM onboarding_mcq_answers
            WHERE run_id = :rid AND phase = :ph
            """
        ),
        {"rid": str(run_id), "ph": phase},
    )
    return [str(r["qid"]) for r in res.mappings().all()]


async def _recent_dynamic_question_texts(conn, run_id: uuid.UUID, phase: str, limit: int = 8) -> List[str]:
    res = await conn.execute(
        text(
            """
            SELECT cfg
            FROM onboarding_mcq_dynamic
            WHERE run_id = :rid AND phase = :ph
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"rid": str(run_id), "ph": phase, "lim": int(limit)},
    )
    out: List[str] = []
    for r in res.mappings().all():
        cfg = _maybe_load_json(r.get("cfg"))
        if isinstance(cfg, dict):
            t = (cfg.get("text") or "").strip()
            if t:
                out.append(t)
    return list(reversed(out))


def _pref_first_qid() -> Optional[str]:
    return PHASE2_ORDER[0] if PHASE2_ORDER else None


async def _transition_to_pref_and_ask_freeform(conn, run_id: uuid.UUID, user_id: uuid.UUID, results: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
    # lock self traits first
    await chatbot_phase1.upsert_user_personality(user_id, results)

    # start pref phase with one-time freeform
    await _update_run(
        conn,
        run_id,
        phase="pref",
        base_index=0,
        current_qid=PREF_FREEFORM_QID,
        eval_state={},
    )
    return {"reply": _pref_intro_prompt(), "phase": "pref"}


async def _next_question_or_transition(conn, run: dict, user_id: uuid.UUID) -> Dict[str, Any]:
    run_id = uuid.UUID(str(run["id"]))
    phase = str(run.get("phase") or "self")

    if phase == "done":
        return {"reply": "You're fully profiled ✅", "phase": "done"}

    # ---------- SELF ----------
    if phase == "self":
        answered = set(await _answered_qids(conn, run_id, "self"))

        missing_base = next((qid for qid in PHASE1_ORDER if qid not in answered), None)
        if missing_base:
            await _update_run(conn, run_id, current_qid=missing_base, base_index=PHASE1_ORDER.index(missing_base))
            return {"reply": chatbot_phase1.format_mcq_from_bank(missing_base), "phase": "self"}

        results, weak = await chatbot_phase1.evaluate_phase1(run_id)

        if not weak:
            return await _transition_to_pref_and_ask_freeform(conn, run_id, user_id, results)

        boosters_self = int(run.get("boosters_self") or 0)
        if boosters_self >= MAX_BOOSTERS_PER_PHASE:
            return await _transition_to_pref_and_ask_freeform(conn, run_id, user_id, results)

        eval_state = await _fetch_eval_state(conn, run_id)
        last_weak = eval_state.get("last_weak") or []
        last_avg = float(eval_state.get("last_avg") or 0.0)
        now_avg = _avg_conf(results, weak)

        same_weak = (sorted(list(map(int, last_weak))) == sorted(list(map(int, weak))))
        if same_weak and (now_avg - last_avg) < 0.03 and boosters_self >= 2:
            return await _transition_to_pref_and_ask_freeform(conn, run_id, user_id, results)

        next_id = f"B{boosters_self + 1}_S"
        answers, dyn_cfg = await chatbot_phase1.fetch_phase_answers(run_id)
        asked_ids = list(set(list(answers.keys()) + list(dyn_cfg.keys())))
        recent_texts = await _recent_dynamic_question_texts(conn, run_id, "self", limit=8)

        q = await mcq_dynamic.generate_booster_mcq(
            phase="self",
            missing_trait_ids=weak,
            already_asked_ids=asked_ids,
            next_id=next_id,
            recent_questions=recent_texts,
        )
        if not q:
            return await _transition_to_pref_and_ask_freeform(conn, run_id, user_id, results)

        await _store_dynamic_cfg(conn, run_id, "self", q)
        await _update_run(
            conn,
            run_id,
            current_qid=q["id"],
            boosters_self=boosters_self + 1,
            eval_state={"last_weak": weak, "last_avg": now_avg},
        )
        return {"reply": chatbot_phase1.format_dynamic_mcq(q), "phase": "self"}

    # ---------- PREF ----------
    if phase == "pref":
        answered = set(await _answered_qids(conn, run_id, "pref"))

        # One-time freeform at the very start of pref phase
        if PREF_FREEFORM_QID not in answered:
            await _update_run(conn, run_id, current_qid=PREF_FREEFORM_QID)
            return {"reply": _pref_intro_prompt(), "phase": "pref"}

        # Normal pref MCQs
        missing_base = next((qid for qid in PHASE2_ORDER if qid not in answered), None)
        if missing_base:
            await _update_run(conn, run_id, current_qid=missing_base, base_index=PHASE2_ORDER.index(missing_base))
            return {"reply": chatbot_phase2.format_mcq_from_bank(missing_base), "phase": "pref"}

        results, weak = await chatbot_phase2.evaluate_phase2(run_id)

        if not weak:
            await chatbot_phase2.upsert_user_preferences(user_id, results)
            await _update_run(conn, run_id, phase="done", current_qid=None, eval_state={})
            return {"reply": "Done — I’ve got what matters to you in a match. ✅", "phase": "done"}

        boosters_pref = int(run.get("boosters_pref") or 0)
        if boosters_pref >= MAX_BOOSTERS_PER_PHASE:
            await chatbot_phase2.upsert_user_preferences(user_id, results)
            await _update_run(conn, run_id, phase="done", current_qid=None, eval_state={})
            return {"reply": "Done — I’ve got what matters to you in a match. ✅", "phase": "done"}

        eval_state = await _fetch_eval_state(conn, run_id)
        last_weak = eval_state.get("last_weak") or []
        last_avg = float(eval_state.get("last_avg") or 0.0)
        now_avg = _avg_conf(results, weak)

        same_weak = (sorted(list(map(int, last_weak))) == sorted(list(map(int, weak))))
        if same_weak and (now_avg - last_avg) < 0.03 and boosters_pref >= 2:
            await chatbot_phase2.upsert_user_preferences(user_id, results)
            await _update_run(conn, run_id, phase="done", current_qid=None, eval_state={})
            return {"reply": "Done — I’ve got what matters to you in a match. ✅", "phase": "done"}

        next_id = f"B{boosters_pref + 1}_P"
        answers, dyn_cfg = await chatbot_phase2.fetch_phase_answers(run_id)
        asked_ids = list(set(list(answers.keys()) + list(dyn_cfg.keys())))
        recent_texts = await _recent_dynamic_question_texts(conn, run_id, "pref", limit=8)

        q = await mcq_dynamic.generate_booster_mcq(
            phase="pref",
            missing_trait_ids=weak,
            already_asked_ids=asked_ids,
            next_id=next_id,
            recent_questions=recent_texts,
        )
        if not q:
            await chatbot_phase2.upsert_user_preferences(user_id, results)
            await _update_run(conn, run_id, phase="done", current_qid=None, eval_state={})
            return {"reply": "Done — I’ve got what matters to you in a match. ✅", "phase": "done"}

        await _store_dynamic_cfg(conn, run_id, "pref", q)
        await _update_run(
            conn,
            run_id,
            current_qid=q["id"],
            boosters_pref=boosters_pref + 1,
            eval_state={"last_weak": weak, "last_avg": now_avg},
        )
        return {"reply": chatbot_phase2.format_dynamic_mcq(q), "phase": "pref"}

    await _update_run(conn, run_id, phase="done", current_qid=None, eval_state={})
    return {"reply": "You're fully profiled ✅", "phase": "done"}


# ----------------------------
# BIO HELPERS (RE-ADDED)
# ----------------------------

def _normalize_one_paragraph(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"\n\s*\n+", " ", s)
    s = re.sub(r"\s+\n\s+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _is_valid_bio(text: str) -> bool:
    s = _normalize_one_paragraph(text)
    if not s:
        return False
    if re.search(r"\n\s*\n", text.strip()):
        return False
    if ":" in s or ";" in s:
        return False
    low = s.lower()
    if any(b in low for b in BIO_BANNED_SUBSTRINGS):
        return False
    if len(s) > 600:
        return False
    return True


async def _fetch_bio_payload(conn, user_id: uuid.UUID, conversation_id: uuid.UUID) -> dict:
    uid = str(user_id)
    cid = str(conversation_id)

    res = await conn.execute(
        text(
            """
            WITH user_row AS (
              SELECT id, full_name, age, gender, location
              FROM users
              WHERE id = :uid
              LIMIT 1
            ),
            history AS (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'ts', created_at,
                  'role', CASE WHEN sender_bot_id IS NOT NULL THEN 'assistant' ELSE 'user' END,
                  'text', content_text
                )
                ORDER BY created_at ASC
              ) AS messages
              FROM (
                SELECT created_at, sender_bot_id, content_text
                FROM messages
                WHERE conversation_id = :cid
                  AND content_text IS NOT NULL
                ORDER BY created_at ASC
                LIMIT :hist_lim
              ) m
            ),
            self_traits AS (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'trait_id', t.id,
                  'trait', t.name,
                  'description', t.description,
                  'score', up.score,
                  'confidence', up.confidence
                )
                ORDER BY t.name
              ) AS traits
              FROM user_personality up
              JOIN personality_traits t ON t.id = up.trait_id
              WHERE up.user_id = :uid
                AND COALESCE(up.confidence, 0) >= :min_conf
            ),
            pref_traits AS (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'trait_id', t.id,
                  'trait', t.name,
                  'description', t.description,
                  'importance', upp.score,
                  'confidence', upp.confidence
                )
                ORDER BY t.name
              ) AS traits
              FROM user_personality_pref upp
              JOIN personality_traits t ON t.id = upp.trait_id
              WHERE upp.user_id = :uid
                AND COALESCE(upp.confidence, 0) >= :min_conf
            )
            SELECT jsonb_build_object(
              'user', jsonb_build_object(
                'id', (SELECT id FROM user_row),
                'full_name', (SELECT full_name FROM user_row),
                'age', (SELECT age FROM user_row),
                'gender', (SELECT gender FROM user_row),
                'location', (SELECT location FROM user_row)
              ),
              'conversation_id', CAST(:cid AS uuid),
              'assessment', jsonb_build_object(
                'history', (SELECT messages FROM history),
                'self_traits', (SELECT traits FROM self_traits),
                'preference_traits', (SELECT traits FROM pref_traits)
              )
            ) AS payload
            """
        ),
        {
            "uid": uid,
            "cid": cid,
            "min_conf": float(BIO_MIN_CONFIDENCE),
            "hist_lim": int(BIO_HISTORY_LIMIT),
        },
    )

    row = res.mappings().first()
    if not row or row.get("payload") is None:
        return {"user": {"id": uid}, "conversation_id": cid, "assessment": {}}

    payload = row["payload"]
    payload = _maybe_load_json(payload)
    return payload if isinstance(payload, dict) else {"raw": payload}


def _compress_bio_payload(payload: dict) -> dict:
    user = payload.get("user") or {}
    assessment = payload.get("assessment") or {}

    def pack(xs: Optional[List[dict]], score_key: str) -> list[dict]:
        out: list[dict] = []
        if not xs:
            return out
        for x in xs:
            out.append({"trait_id": x.get("trait_id"), score_key: x.get(score_key), "confidence": x.get("confidence", 0)})
        return out

    return {
        "user": {"age": user.get("age"), "gender": user.get("gender"), "location": user.get("location")},
        "traits": {
            "self": pack(assessment.get("self_traits"), "score"),
            "pref": pack(assessment.get("preference_traits"), "importance"),
        },
    }


async def _generate_personal_bio_with_gpt(payload: dict) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    system_prompt = (
        "You are an elite biographer and behavioral analyst.\n"
        "You are given only numeric personality trait signals, numeric preference signals, and confidence.\n"
        "Infer observable relational patterns from combinations; do not describe traits.\n\n"
        "Write a concise personal bio for a high-end matchmaking app.\n\n"
        "FORMAT:\n"
        "- Exactly 1 paragraph.\n"
        "- 2–4 sentences.\n"
        "- No lists.\n"
        "- No colons.\n"
        "- No semicolons.\n\n"
        "HARD CONSTRAINTS:\n"
        "- Do NOT mention traits, scores, confidence, assessments, or testing.\n"
        "- Do NOT invent life facts or specific activities.\n"
        "- Avoid generic virtue words and dating-profile fluff.\n\n"
        "BEHAVIORAL REQUIREMENTS:\n"
        "- Translate signals into how the person tends to behave in relationships.\n"
        "- Implicitly show what they prioritize over alternatives.\n"
        "- Include one subtle exclusion/boundary derived from preference signals.\n"
        "- Include one recognizable tension or trade-off.\n"
        "- Write with specificity and restraint.\n"
        "- Speak in the paragraph like you are speaking to the user of the bio , use YOU and not he/she .\n"
    )

    compressed = _compress_bio_payload(payload)
    user_prompt = "Data (JSON):\n" + json.dumps(compressed, ensure_ascii=False)

    from openai import AsyncOpenAI  # type: ignore
    client = AsyncOpenAI(api_key=api_key)

    async def _call_once(extra_system: Optional[str] = None) -> str:
        sys = system_prompt if not extra_system else (system_prompt + "\n" + extra_system)
        try:
            resp = await client.responses.create(
                model=BIO_MODEL,
                input=[{"role": "system", "content": sys}, {"role": "user", "content": user_prompt}],
                temperature=BIO_TEMPERATURE,
                max_output_tokens=BIO_MAX_TOKENS,
            )
            text_out = getattr(resp, "output_text", None)
            if isinstance(text_out, str) and text_out.strip():
                return text_out.strip()
        except Exception:
            pass

        chat = await client.chat.completions.create(
            model=BIO_MODEL,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user_prompt}],
            temperature=BIO_TEMPERATURE,
            max_tokens=BIO_MAX_TOKENS,
        )
        return (chat.choices[0].message.content or "").strip()

    bio = _normalize_one_paragraph(await _call_once())
    if not _is_valid_bio(bio) and BIO_MAX_RETRIES > 0:
        repair = (
            "REPAIR INSTRUCTIONS:\n"
            "- Rewrite to comply.\n"
            "- One paragraph, 2–4 sentences.\n"
            "- Remove generic virtue language.\n"
            "- Include one subtle exclusion/boundary.\n"
            "- Do not invent any facts.\n"
        )
        bio2 = _normalize_one_paragraph(await _call_once(extra_system=repair))
        if _is_valid_bio(bio2):
            return bio2
    return bio


async def _store_bio_on_user(conn, user_id: uuid.UUID, bio: str) -> None:
    await conn.execute(
        text("UPDATE users SET personal_bio = :bio, updated_at = now() WHERE id = :uid"),
        {"bio": bio, "uid": str(user_id)},
    )


@router.post("/message")
async def chatbot_message(payload: dict, user_id: uuid.UUID = Depends(get_current_user_id)):
    user_text = (payload.get("message") or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="message is required")

    async with engine.begin() as conn:
        convo_id = await get_or_create_chatbot_conversation(conn, user_id)
        await log_user_message(conn, convo_id, user_id, user_text)

        run = await _get_or_create_run(conn, user_id, convo_id)
        run_id = uuid.UUID(str(run["id"]))
        phase = str(run.get("phase") or "self")
        current_qid = run.get("current_qid")

        # If we have a pending question, store the answer
        if current_qid:
            # Special: PREF_FREE_1 accepts raw text or Skip (one-time)
            if phase == "pref" and str(current_qid) == PREF_FREEFORM_QID:
                if user_text.strip().lower() == "skip":
                    # record as answered but with no text => no inference
                    await _upsert_answer(conn, run_id, "pref", PREF_FREEFORM_QID, "D", None)
                else:
                    await _upsert_answer(conn, run_id, "pref", PREF_FREEFORM_QID, "D", user_text.strip())

                await _update_run(conn, run_id, current_qid=None)

            else:
                # Normal MCQ parse path
                a = _parse_user_answer(user_text)
                if a.get("opt") is None:
                    # re-ask same question without changing state
                    if phase == "self":
                        reply = chatbot_phase1.format_mcq_from_bank(current_qid) if current_qid in PHASE1_ORDER else None
                        if not reply:
                            res = await conn.execute(
                                text(
                                    """
                                    SELECT cfg FROM onboarding_mcq_dynamic
                                    WHERE run_id = :rid AND phase = 'self' AND qid = :qid
                                    """
                                ),
                                {"rid": str(run_id), "qid": current_qid},
                            )
                            row = res.mappings().first()
                            cfg = _maybe_load_json(row["cfg"]) if row else None
                            reply = chatbot_phase1.format_dynamic_mcq(cfg) if isinstance(cfg, dict) else "Tap A, B, C — or D."
                        await log_bot_message(conn, convo_id, user_id, reply)
                        prog = await _fetch_trait_progress(conn, user_id, CONF_THRESHOLD)
                        return {"reply": reply, "phase": "self", "progress": prog}
                    else:
                        reply = chatbot_phase2.format_mcq_from_bank(current_qid) if current_qid in PHASE2_ORDER else None
                        if not reply:
                            res = await conn.execute(
                                text(
                                    """
                                    SELECT cfg FROM onboarding_mcq_dynamic
                                    WHERE run_id = :rid AND phase = 'pref' AND qid = :qid
                                    """
                                ),
                                {"rid": str(run_id), "qid": current_qid},
                            )
                            row = res.mappings().first()
                            cfg = _maybe_load_json(row["cfg"]) if row else None
                            reply = chatbot_phase2.format_dynamic_mcq(cfg) if isinstance(cfg, dict) else "Tap A, B, C — or D."
                        await log_bot_message(conn, convo_id, user_id, reply)
                        prog = await _fetch_trait_progress(conn, user_id, CONF_THRESHOLD)
                        return {"reply": reply, "phase": "pref", "progress": prog}

                opt = a["opt"]
                txt = a.get("text")
                await _upsert_answer(conn, run_id, phase, str(current_qid), str(opt), txt)

                # best-effort base_index increment (truth is answers table)
                if phase == "self" and str(current_qid) in PHASE1_ORDER:
                    await _update_run(
                        conn,
                        run_id,
                        base_index=max(int(run.get("base_index") or 0), PHASE1_ORDER.index(str(current_qid)) + 1),
                    )
                elif phase == "pref" and str(current_qid) in PHASE2_ORDER:
                    await _update_run(
                        conn,
                        run_id,
                        base_index=max(int(run.get("base_index") or 0), PHASE2_ORDER.index(str(current_qid)) + 1),
                    )

                await _update_run(conn, run_id, current_qid=None)

        # Refresh run
        res = await conn.execute(text("SELECT * FROM onboarding_mcq_runs WHERE id = :rid"), {"rid": str(run_id)})
        run = dict(res.mappings().first())

        out = await _next_question_or_transition(conn, run, user_id)
        reply = (out.get("reply") or "").strip()
        if reply:
            await log_bot_message(conn, convo_id, user_id, reply)

        prog = await _fetch_trait_progress(conn, user_id, CONF_THRESHOLD)

    # If done, generate bio (outside transaction)
    if out.get("phase") == "done":
        generated_bio: Optional[str] = None
        try:
            async with engine.begin() as conn2:
                payload_for_gpt = await _fetch_bio_payload(conn2, user_id, convo_id)

            generated_bio = await _generate_personal_bio_with_gpt(payload_for_gpt)

            if generated_bio:
                async with engine.begin() as conn3:
                    await _store_bio_on_user(conn3, user_id, generated_bio)
                    await log_bot_message(conn3, convo_id, user_id, generated_bio)

        except Exception:
            logger.warning("Bio generation failed", exc_info=True)

        final_msg = generated_bio or "You're fully profiled ✅"
        return {"reply": final_msg, "phase": "done", "progress": prog, "bio": generated_bio}

    return {"reply": reply, "phase": out.get("phase"), "progress": prog}


@router.get("/history")
async def chatbot_history(user_id: uuid.UUID = Depends(get_current_user_id)):
    async with engine.begin() as conn:
        convo_id = await get_or_create_chatbot_conversation(conn, user_id)
        messages = await fetch_conversation_messages(conn, convo_id, limit=250)
        db_progress = await _fetch_trait_progress(conn, user_id, CONF_THRESHOLD)

        run = await _get_or_create_run(conn, user_id, convo_id)
        phase = str(run.get("phase") or "self")
        if phase not in ("self", "pref", "done"):
            phase = "self"

    return {"messages": messages, "phase": phase, "progress": db_progress}
