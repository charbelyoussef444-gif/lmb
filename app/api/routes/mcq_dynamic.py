# app/api/routes/mcq_dynamic.py

import os
import json
import base64
import asyncio
import logging
from typing import Dict, Any, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)
OPENAI_MODEL = os.getenv("MCQ_DYNAMIC_MODEL", "gpt-4o")

CFG_TAG_PREFIX = "[[mcqcfg:"
CFG_TAG_SUFFIX = "]]"

__all__ = ["encode_cfg", "decode_cfg_from_text", "generate_booster_mcq"]


def get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def encode_cfg(cfg: Dict[str, Any]) -> str:
    raw = json.dumps(cfg, ensure_ascii=False)
    b = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return f"{CFG_TAG_PREFIX}{b}{CFG_TAG_SUFFIX}"


def decode_cfg_from_text(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    i = text.find(CFG_TAG_PREFIX)
    if i < 0:
        return None
    j = text.find(CFG_TAG_SUFFIX, i)
    if j < 0:
        return None
    b = text[i + len(CFG_TAG_PREFIX) : j].strip()
    try:
        raw = base64.urlsafe_b64decode(b.encode("ascii")).decode("utf-8")
        cfg = json.loads(raw)
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


async def generate_booster_mcq(
    phase: str,
    missing_trait_ids: List[int],
    already_asked_ids: List[str],
    next_id: str,
    recent_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Returns a dynamic MCQ config dict:
      { id, text, A,B,C,D, scores:{A:{tid:..},B:{..},C:{..}}, weights:{tid:..} }
    """
    client = get_openai_client()

    # Preference stem diversity (NEW)
    pref_stems = [
        "Instant green flag in a partner:",
        "The kind of person you click with most is someone who…",
        "Your ideal partner in a tough week is someone who…",
        "When plans change last-minute, you want someone who…",
        "You feel most respected by a partner who…",
        "A relationship feels easiest with someone who…",
        "If you had to pick, you’d rather date someone who…",
        "The best communication-style match for you is…",
        "On a first trip together, you’d want them to…",
        "In conflict, you want them to…",
    ]

    # Base rules
    rules = [
        "You must output JSON only (no markdown).",
        "You must set id EXACTLY equal to next_id.",
        "Make ONE MCQ that gives signal on MULTIPLE traits at once (2 to 5).",
        "Options A/B/C must be clearly different real-world choices.",
        "Keep question short and behavior-based (no abstract corporate topics).",
        "Avoid repeating the same subject as any recent_questions.",
        "Return absolute 0..1 scores for each targeted trait per option.",
        "D exists but does not need deterministic mapping.",
        "Avoid extreme scores unless the option is clearly extreme.",
    ]

    if (phase or "").strip().lower() == "pref":
        rules += [
            "THIS IS ABOUT WHAT THE USER WANTS IN A PARTNER (not about the user).",
            "Pick ONE stem from pref_stems and use it as the question opener.",
            "Do NOT reuse common boilerplate like 'I prefer someone who', 'I'm drawn to', 'Looking for someone who'.",
            "Answer options must describe partner behavior (e.g. 'Someone who...'), not the user's behavior.",
            "Do NOT write 'When I...' / 'I usually...' / 'My vibe...' / 'Plans with me...' style self descriptions.",
        ]
    else:
        rules += [
            "THIS IS ABOUT THE USER'S OWN BEHAVIOR (self phase).",
            "Write the question as 'When I…' / 'I tend to…' style.",
        ]

    payload = {
        "phase": phase,
        "target_trait_ids": [int(x) for x in (missing_trait_ids or [])[:5]],
        "already_asked_ids": already_asked_ids[-50:],
        "next_id": next_id,
        "recent_questions": (recent_questions or [])[-10:],
        "rules": rules,
        "pref_stems": pref_stems if (phase or "").strip().lower() == "pref" else [],
    }

    system = """
Return JSON only with this exact shape:
{
  "id": "<must equal next_id>",
  "text": "...",
  "A": "...",
  "B": "...",
  "C": "...",
  "D": "Other (one sentence)",
  "scores": {
    "A": { "<trait_id>": <0..1>, ... },
    "B": { "<trait_id>": <0..1>, ... },
    "C": { "<trait_id>": <0..1>, ... }
  },
  "weights": { "<trait_id>": <0.2..1.0>, ... }
}

Hard constraints:
- id must equal next_id exactly.
- 2 to 5 traits only (across scores/weights).
- Trait IDs must be integers encoded as strings in JSON keys.
- weights should sum roughly ~1–3 total.
- Must be semantically different from recent_questions.

Phase constraints:
- If phase == "self": question describes the user's behavior.
- If phase == "pref": question must be partner-focused and use ONE of pref_stems as the opener.
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
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.2,
            ),
        )

        raw = (out.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError("bad json")

        qid = str(next_id)
        txt = str(data.get("text") or "Quick one:")
        A = str(data.get("A") or "Option A")
        B = str(data.get("B") or "Option B")
        C = str(data.get("C") or "Option C")
        D = "Other (one sentence)"

        if (phase or "").strip().lower() == "pref":
            t = txt.strip().lower()
            banned_starts = (
                "when i",
                "i usually",
                "i tend to",
                "my vibe",
                "plans with me",
                "when i'm",
                "i prefer",
                "i'm drawn",
                "you prefer",
                "looking for someone",
                "your partner should",
            )
            if t.startswith(banned_starts):
                return {}

        scores = data.get("scores") or {}
        weights = data.get("weights") or {}

        def clean_map(m: Any) -> Dict[int, float]:
            outm: Dict[int, float] = {}
            if not isinstance(m, dict):
                return outm
            for k, v in m.items():
                try:
                    tid = int(k)
                    sv = max(0.0, min(1.0, float(v)))
                    outm[tid] = sv
                except Exception:
                    continue
            return outm

        scoresA = clean_map(scores.get("A"))
        scoresB = clean_map(scores.get("B"))
        scoresC = clean_map(scores.get("C"))
        wclean = clean_map(weights)

        allowed = set(int(x) for x in (missing_trait_ids or []))
        scoresA = {k: v for k, v in scoresA.items() if k in allowed}
        scoresB = {k: v for k, v in scoresB.items() if k in allowed}
        scoresC = {k: v for k, v in scoresC.items() if k in allowed}
        wclean = {k: v for k, v in wclean.items() if k in allowed}

        if len(scoresA) + len(scoresB) + len(scoresC) < 2:
            return {}

        return {
            "id": qid,
            "text": txt,
            "A": A,
            "B": B,
            "C": C,
            "D": D,
            "scores": {"A": scoresA, "B": scoresB, "C": scoresC},
            "weights": wclean,
        }

    except Exception:
        logger.exception("generate_booster_mcq failed")
        return {}
