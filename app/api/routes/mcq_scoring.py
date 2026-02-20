# app/api/routes/mcq_scoring.py

import math
from collections import defaultdict
from typing import Dict, Any, Tuple, List

from .mcq_config import TRAIT_WEIGHTS, TRAIT_IDS

AI_OTHER_WEIGHT = 0.7
MCQ_WEIGHT = 0.3

DEFAULT_SCORE = 0.5
DEFAULT_CONF = 0.2

# ---- Special handling: pref freeform should be broad but not dominant
PREF_FREEFORM_QID = "PREF_FREE_1"
PREF_FREEFORM_MAX_CONF_SCALE = 0.45


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def aggregate_traits_all(
    answers: Dict[str, Dict[str, Any]],
    phase_scores: Dict[str, Dict[str, Dict[int, float]]],
    trait_ids: List[int] = TRAIT_IDS,
) -> Dict[int, Dict[str, float]]:
    """
    answers[qid] = {"opt":"A"|"B"|"C"|"D", "text"?:str, "ai_traits"?:{tid:score}, "ai_conf"?:float}
    phase_scores[qid][opt] = {trait_id: score_0..1}
    Returns ALL traits (if no signal -> defaults).
    """

    # trait -> list[(qid, score, w)]
    trait_points: Dict[int, list[Tuple[str, float, float]]] = defaultdict(list)

    for qid, a in (answers or {}).items():
        opt = a.get("opt")
        if qid not in phase_scores:
            # NOTE: For freeform pref intro, we store it in phase='pref' with qid PREF_FREE_1.
            # It will NOT exist in PHASE2_SCORES; it contributes via ai_traits only.
            pass

        if opt in ("A", "B", "C"):
            if qid not in phase_scores:
                continue
            mapping = phase_scores[qid].get(opt) or {}
            for tid, score in mapping.items():
                base_w = TRAIT_WEIGHTS.get(int(tid), {}).get(qid, 1.0)
                w = float(base_w) * MCQ_WEIGHT
                trait_points[int(tid)].append((qid, float(score), float(w)))

        if opt == "D" and isinstance(a.get("ai_traits"), dict):
            # Use model confidence as a weight scaler (but don't allow it to go to ~0)
            try:
                ai_conf = float(a.get("ai_conf", 0.5))
            except Exception:
                ai_conf = 0.5
            ai_conf = _clamp01(ai_conf)
            conf_scale = max(0.2, ai_conf)  # floor keeps D from being ignored entirely

            # Special: ideal-match freeform should influence broadly but not dominate MCQs
            if str(qid) == PREF_FREEFORM_QID:
                conf_scale = min(conf_scale, PREF_FREEFORM_MAX_CONF_SCALE)

            for tid, score in (a["ai_traits"] or {}).items():
                base_w = TRAIT_WEIGHTS.get(int(tid), {}).get(qid, 1.0)
                w = float(base_w) * AI_OTHER_WEIGHT * conf_scale
                trait_points[int(tid)].append((qid, float(score), float(w)))

    results: Dict[int, Dict[str, float]] = {}

    for tid in trait_ids:
        pts = trait_points.get(int(tid), [])
        if not pts:
            results[int(tid)] = {"score": DEFAULT_SCORE, "confidence": DEFAULT_CONF}
            continue

        num = 0.0
        den = 0.0
        for (_qid, s, w) in pts:
            num += w * s
            den += w

        if den <= 1e-9:
            results[int(tid)] = {"score": DEFAULT_SCORE, "confidence": DEFAULT_CONF}
            continue

        score = num / den

        var = 0.0
        for (_qid, s, w) in pts:
            var += w * (s - score) ** 2
        var = var / den
        std = math.sqrt(var)

        coverage = min(1.0, den)
        consistency = max(0.0, 1.0 - std)

        conf = 0.2 + 0.6 * coverage + 0.2 * consistency
        if std > 0.25:
            conf -= 0.25

        results[int(tid)] = {
            "score": round(_clamp01(score), 3),
            "confidence": round(_clamp01(conf), 3),
        }

    return results


def weak_traits(results: Dict[int, Dict[str, float]], min_conf: float = 0.35) -> List[int]:
    out: List[int] = []
    for tid, r in (results or {}).items():
        try:
            c = float(r.get("confidence", 0.0))
        except Exception:
            c = 0.0
        if c < min_conf:
            out.append(int(tid))
    return out
