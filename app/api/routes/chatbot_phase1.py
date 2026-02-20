import os
import json
import uuid
import asyncio
import logging
from typing import Dict, Any, List, Tuple

from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import Integer
from openai import OpenAI

from app.db.engine import engine
from .mcq_config import (
    PHASE1_ORDER,
    PHASE1_QUESTIONS,
    PHASE1_SCORES,
    TRAIT_IDS,
    TRAIT_LABELS,
    TRAIT_ID_BY_LABEL,
)
from .mcq_scoring import aggregate_traits_all, weak_traits
from . import mcq_dynamic

logger = logging.getLogger(__name__)
OPENAI_MODEL = "gpt-4o"

CONF_TARGET = 0.5
MAX_BOOSTERS_PHASE = 6


def get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def format_mcq_from_bank(qid: str) -> str:
    q = PHASE1_QUESTIONS[qid]
    return (
        f"{q['text']}\n"
        f"A) {q['A']}\n"
        f"B) {q['B']}\n"
        f"C) {q['C']}\n"
        f"D) {q['D']}\n\n"
        f"[[mcq:{qid}]]"
    )


def format_dynamic_mcq(cfg: Dict[str, Any]) -> str:
    qid = cfg["id"]
    base = (
        f"{cfg['text']}\n"
        f"A) {cfg['A']}\n"
        f"B) {cfg['B']}\n"
        f"C) {cfg['C']}\n"
        f"D) {cfg['D']}\n\n"
        f"[[mcq:{qid}]]"
    )
    return base + "\n" + mcq_dynamic.encode_cfg(cfg)


def _trait_id_legend() -> str:
    return "\n".join([f"{tid} {TRAIT_LABELS.get(tid, str(tid))}" for tid in TRAIT_IDS])


def _clean_trait_map(traits: Any) -> Dict[int, float]:
    clean: Dict[int, float] = {}
    if not isinstance(traits, dict):
        return clean

    for k, v in traits.items():
        tid: int | None = None
        try:
            tid = int(k)
        except Exception:
            tid = None

        if tid is None and isinstance(k, str):
            kk = k.strip().lower().replace(" ", "_").replace("-", "_")
            tid = TRAIT_ID_BY_LABEL.get(kk)

        if tid is None:
            continue

        try:
            sv = max(0.0, min(1.0, float(v)))
        except Exception:
            continue

        if tid in TRAIT_IDS:
            clean[int(tid)] = sv

    return clean


async def _call_openai_for_other(qid: str, text_in: str) -> Dict[str, Any]:
    if not text_in:
        return {"traits": {}, "confidence": 0.3}

    client = get_openai_client()
    payload = {"question_id": qid, "text": text_in}
    legend = _trait_id_legend()

    system = f"""
Return JSON only:
{{
  "traits": {{ "<trait_id_int_as_string>": <score_0_to_1>, ... }},
  "confidence": <0_to_1>
}}

Trait IDs (use ONLY these numeric IDs as keys):
{legend}

Rules:
- Only include traits you have real signal for.
- Omit uncertain traits (do not guess).
- 1-6 traits max.
"""

    loop = asyncio.get_event_loop()
    try:
        out = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=OPENAI_MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                temperature=0.2,
            ),
        )
        raw = (out.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            return {"traits": {}, "confidence": 0.3}

        traits_raw = data.get("traits", {})
        conf_raw = data.get("confidence", 0.5)

        traits = _clean_trait_map(traits_raw)
        try:
            conf = float(conf_raw)
        except Exception:
            conf = 0.5
        conf = max(0.0, min(1.0, conf))

        return {"traits": traits, "confidence": conf}
    except Exception:
        logger.exception("OpenAI Other failed (phase1)")
        return {"traits": {}, "confidence": 0.3}


def _maybe_load_json(x: Any) -> Any:
    if isinstance(x, str):
        s = x.strip()
        if s and (s.startswith("{") or s.startswith("[")):
            try:
                return json.loads(s)
            except Exception:
                return x
    return x


async def fetch_phase_answers(run_id: uuid.UUID) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    rid = str(run_id)
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                """
                SELECT qid, opt, free_text, ai_traits, ai_confidence
                FROM onboarding_mcq_answers
                WHERE run_id = :rid AND phase = 'self'
                ORDER BY created_at ASC
                """
            ),
            {"rid": rid},
        )
        rows = res.mappings().all()

        res2 = await conn.execute(
            text(
                """
                SELECT qid, cfg
                FROM onboarding_mcq_dynamic
                WHERE run_id = :rid AND phase = 'self'
                """
            ),
            {"rid": rid},
        )
        drows = res2.mappings().all()

    answers: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        qid = str(r["qid"])
        ai_traits = _maybe_load_json(r.get("ai_traits")) or {}
        if not isinstance(ai_traits, dict):
            ai_traits = {}
        answers[qid] = {
            "opt": (r["opt"] or "").strip(),
            "text": r.get("free_text"),
            "ai_traits": ai_traits,
            "ai_conf": float(r.get("ai_confidence") or 0.0),
        }

    dyn_cfg: Dict[str, Dict[str, Any]] = {}
    for r in drows:
        qid = str(r["qid"])
        cfg = _maybe_load_json(r.get("cfg"))
        if isinstance(cfg, dict):
            dyn_cfg[qid] = cfg

    return answers, dyn_cfg


def merge_dynamic_into_scores(base_scores: Dict[str, Any], dyn_cfg: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base_scores)
    for qid, cfg in (dyn_cfg or {}).items():
        try:
            scores = cfg.get("scores") or {}
            merged[qid] = {
                "A": {int(k): float(v) for k, v in (scores.get("A") or {}).items()},
                "B": {int(k): float(v) for k, v in (scores.get("B") or {}).items()},
                "C": {int(k): float(v) for k, v in (scores.get("C") or {}).items()},
            }
        except Exception:
            continue
    return merged


async def ensure_d_inference(run_id: uuid.UUID, answers: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    updates: List[Tuple[str, dict, float]] = []
    for qid, a in (answers or {}).items():
        if a.get("opt") == "D" and a.get("text") and not a.get("ai_traits"):
            ai = await _call_openai_for_other(qid, a["text"])
            a["ai_traits"] = ai["traits"]
            a["ai_conf"] = ai["confidence"]
            updates.append((qid, ai["traits"], ai["confidence"]))

    if updates:
        rid = str(run_id)
        async with engine.begin() as conn:
            for (qid, traits, conf) in updates:
                await conn.execute(
                    text(
                        """
                        UPDATE onboarding_mcq_answers
                        SET ai_traits = CAST(:t AS jsonb), ai_confidence = :c, updated_at = now()
                        WHERE run_id = :rid AND phase = 'self' AND qid = :qid
                        """
                    ),
                    {"rid": rid, "qid": qid, "t": json.dumps(traits), "c": float(conf)},
                )
    return answers


async def evaluate_phase1(run_id: uuid.UUID) -> Tuple[Dict[int, Dict[str, float]], List[int]]:
    answers, dyn_cfg = await fetch_phase_answers(run_id)
    answers = await ensure_d_inference(run_id, answers)

    merged_scores = merge_dynamic_into_scores(PHASE1_SCORES, dyn_cfg)
    results = aggregate_traits_all(answers, merged_scores)
    weak = [tid for tid in weak_traits(results, min_conf=CONF_TARGET)]
    return results, weak


async def upsert_user_personality(user_id: uuid.UUID, results: Dict[int, Dict[str, float]]) -> None:
    uid = str(user_id)

    seed_stmt = text(
        """
        INSERT INTO user_personality (user_id, trait_id, score, confidence)
        SELECT :uid, t, 0.5, 0.2
        FROM unnest(:tids) AS t
        ON CONFLICT (user_id, trait_id) DO NOTHING
        """
    ).bindparams(bindparam("tids", type_=ARRAY(Integer)))

    async with engine.begin() as conn:
        await conn.execute(seed_stmt, {"uid": uid, "tids": [int(x) for x in TRAIT_IDS]})

        for tid, r in results.items():
            await conn.execute(
                text(
                    """
                    INSERT INTO user_personality (user_id, trait_id, score, confidence)
                    VALUES (:uid, :tid, :s, :c)
                    ON CONFLICT (user_id, trait_id)
                    DO UPDATE SET score = EXCLUDED.score, confidence = EXCLUDED.confidence
                    """
                ),
                {"uid": uid, "tid": int(tid), "s": float(r["score"]), "c": float(r["confidence"])},
            )
    