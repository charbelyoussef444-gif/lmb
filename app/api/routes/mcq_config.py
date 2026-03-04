# app/api/routes/mcq_config.py

# Trait IDs (DB)
# 1 warmth
# 2 honesty_humility
# 3 attachment_anxiety
# 4 attachment_avoidance
# 5 openness
# 6 repair_orientation
# 7 autonomy
# 8 communication_clarity
# 9 reliability
# 10 exclusivity

TRAIT_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

# Canonical labels for LLM / logging / debugging
TRAIT_LABELS = {
    1: "warmth",
    2: "honesty_humility",
    3: "attachment_anxiety",
    4: "attachment_avoidance",
    5: "openness",
    6: "repair_orientation",
    7: "autonomy",
    8: "communication_clarity",
    9: "reliability",
    10: "exclusivity",
}

# Reverse map (for fallback if model returns names)
TRAIT_ID_BY_LABEL = {v.lower(): k for k, v in TRAIT_LABELS.items()}

# Engineered to cover ALL traits with minimal questions
# Added:
# - S6 to cover trait 10 (exclusivity) in self phase
# - P6 to cover trait 5 (openness) in preference phase
PHASE1_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6"]
PHASE2_ORDER = ["P1", "P2", "P3", "P4", "P5", "P6"]

PHASE1_QUESTIONS = {
    "S1": {
        "text": "When I’m really into someone, I usually…",
        "A": "Want frequent contact and reassurance",
        "B": "Like steady contact but not too intense",
        "C": "Need lots of space, hate constant messaging",
        "D": "Other (one sentence)",
    },
    "S2": {
        "text": "In a conflict, I usually…",
        "A": "Talk it out immediately",
        "B": "Cool off, then talk",
        "C": "Avoid it / shut down",
        "D": "Other (one sentence)",
    },
    "S3": {
        "text": "Plans with me are usually…",
        "A": "Locked in. If I commit, I show up.",
        "B": "Flexible, but I try to be reliable.",
        "C": "Spontaneous / last-minute, I hate rigid plans.",
        "D": "Other (one sentence)",
    },
    "S4": {
        "text": "When something bothers me, I…",
        "A": "Say it directly and clearly",
        "B": "Hint / soften it, avoid being harsh",
        "C": "Keep it in, deal with it alone",
        "D": "Other (one sentence)",
    },
    "S5": {
        "text": "My vibe with new experiences is…",
        "A": "Give me novelty—art, culture, travel, weird ideas",
        "B": "Some novelty is fun, but I like balance",
        "C": "I prefer familiar routines and predictable environments",
        "D": "Other (one sentence)",
    },
    "S6": {
        "text": "When I’m seeing someone I like, I prefer…",
        "A": "Exclusive pretty quickly",
        "B": "Take time, then commit if it’s right",
        "C": "Keep it flexible / not define it for a while",
        "D": "Other (one sentence)",
    },
}

PHASE2_QUESTIONS = {
    "P1": {
        "text": "I’m most attracted to someone who is…",
        "A": "Warm and emotionally available",
        "B": "Honest, blunt, intellectually sharp",
        "C": "Independent, low-drama, needs space",
        "D": "Other (one sentence)",
    },
    "P2": {
        "text": "Instant turn-off for me is…",
        "A": "Flaky / unreliable",
        "B": "Controlling / jealous",
        "C": "Emotionally cold / unavailable",
        "D": "Other (one sentence)",
    },
    "P3": {
        "text": "After a fight, my ideal person…",
        "A": "Owns it fast and repairs",
        "B": "Needs space, then repairs",
        "C": "Pretends nothing happened / avoids repair",
        "D": "Other (one sentence)",
    },
    "P4": {
        "text": "My ideal relationship structure is…",
        "A": "Strictly monogamous",
        "B": "Mostly monogamous, some flexibility",
        "C": "Open / ENM-friendly",
        "D": "Other (one sentence)",
    },
    "P5": {
        "text": "The kind of person I keep liking is…",
        "A": "Stable, kind, consistent (even if less exciting)",
        "B": "A mix: solid + playful + some edge",
        "C": "Intense / exciting / unpredictable (even if risky)",
        "D": "Other (one sentence)",
    },
    "P6": {
        "text": "I’m most drawn to someone who…",
        "A": "Is curious and loves trying new things",
        "B": "Enjoys some new stuff but also likes routine",
        "C": "Prefers familiar routines and predictability",
        "D": "Other (one sentence)",
    },
}

# Option -> trait score map (0..1)
PHASE1_SCORES = {
    # closeness / independence
    "S1": {
        "A": {3: 0.85, 4: 0.15, 7: 0.25, 1: 0.65},
        "B": {3: 0.50, 4: 0.50, 7: 0.55, 1: 0.60},
        "C": {3: 0.20, 4: 0.85, 7: 0.85, 1: 0.35},
    },
    # conflict / repair / communication
    "S2": {
        "A": {6: 0.85, 8: 0.85, 4: 0.20},
        "B": {6: 0.65, 8: 0.60, 4: 0.50},
        "C": {6: 0.20, 8: 0.20, 4: 0.85},
    },
    # reliability / autonomy tradeoff
    "S3": {
        "A": {9: 0.90, 7: 0.35, 2: 0.65},
        "B": {9: 0.65, 7: 0.60, 2: 0.70},
        "C": {9: 0.25, 7: 0.85, 2: 0.55},
    },
    # directness / warmth
    "S4": {
        "A": {8: 0.90, 1: 0.60, 2: 0.70},
        "B": {8: 0.60, 1: 0.80, 2: 0.75},
        "C": {8: 0.25, 1: 0.35, 2: 0.60},
    },
    # openness
    "S5": {
        "A": {5: 0.90, 7: 0.55},
        "B": {5: 0.60, 7: 0.60},
        "C": {5: 0.20, 7: 0.55},
    },
    # exclusivity (added)
    "S6": {
        "A": {10: 0.90},
        "B": {10: 0.60},
        "C": {10: 0.20},
    },
}

PHASE2_SCORES = {
    "P1": {
        "A": {1: 0.90, 4: 0.25, 3: 0.45},
        "B": {2: 0.90, 8: 0.75, 1: 0.55},
        "C": {7: 0.85, 4: 0.75, 1: 0.30},
    },
    "P2": {
        "A": {9: 0.90, 2: 0.65},
        "B": {7: 0.90, 3: 0.55},
        "C": {1: 0.90, 4: 0.70},
    },
    "P3": {
        "A": {6: 0.90, 8: 0.80},
        "B": {6: 0.65, 8: 0.55},
        "C": {6: 0.20, 8: 0.25},
    },
    "P4": {
        "A": {10: 0.90},
        "B": {10: 0.60},
        "C": {10: 0.15},
    },
    "P5": {
        "A": {9: 0.85, 1: 0.75, 3: 0.30},
        "B": {9: 0.60, 1: 0.60, 3: 0.50},
        "C": {9: 0.25, 1: 0.35, 3: 0.70},
    },
    # openness preference (added)
    "P6": {
        "A": {5: 0.90},
        "B": {5: 0.60},
        "C": {5: 0.20},
    },
}

# Trait -> question weights (diagnostic strength)
# Only set weights where you *care*; missing falls back to 1.0 in scoring engine
TRAIT_WEIGHTS = {
    1: {"S1": 0.5, "S4": 0.6, "P1": 0.7, "P2": 0.6, "P5": 0.5},
    2: {"S3": 0.5, "S4": 0.6, "P2": 0.4},
    3: {"S1": 0.7, "P5": 0.6, "P1": 0.4},
    4: {"S1": 0.6, "S2": 0.4, "P1": 0.5, "P2": 0.4},
    5: {"S5": 1.0, "P6": 1.0},  # added P6
    6: {"S2": 0.7, "P3": 0.8},
    7: {"S1": 0.5, "S3": 0.6, "S5": 0.4, "P1": 0.5, "P2": 0.4},
    8: {"S2": 0.5, "S4": 0.8, "P3": 0.5},
    9: {"S3": 0.9, "P2": 0.7, "P5": 0.6},
    10: {"P4": 1.0, "S6": 1.0},  # added S6
}
