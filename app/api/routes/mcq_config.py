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
PHASE1_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6"]
PHASE2_ORDER = ["P1", "P2", "P3", "P4", "P5", "P6"]

PHASE1_QUESTIONS = {
    "S1": {
        "text": "When I start liking someone, my natural rhythm of contact tends to be…",
        "A": "Frequent messages and check-ins feel natural to me",
        "B": "Regular contact, but with space for both people’s routines",
        "C": "I enjoy the connection, but I like plenty of personal space between conversations",
        "D": "Write your own answer",
    },
    "S2": {
        "text": "When tension comes up between me and someone I care about, I usually…",
        "A": "Prefer addressing it fairly soon so we can clear the air",
        "B": "Take a little time to think, then come back to talk it through",
        "C": "Give the situation space and let things settle naturally",
        "D": "Write your own answer",
    },
    "S3": {
        "text": "When making plans with me, people usually notice that I…",
        "A": "Like confirming plans clearly ahead of time",
        "B": "Am dependable, but I prefer keeping things somewhat flexible",
        "C": "Enjoy plans that evolve naturally in the moment",
        "D": "Write your own answer",
    },
    "S4": {
        "text": "If something someone does bothers me, my instinct is to…",
        "A": "Explain it clearly so we both understand each other",
        "B": "Bring it up gently so it doesn’t feel confrontational",
        "C": "Reflect on it privately before deciding whether to mention it",
        "D": "Write your own answer",
    },
    "S5": {
        "text": "My relationship with new experiences is best described as…",
        "A": "I enjoy exploring new ideas, places, and perspectives whenever I can",
        "B": "I like mixing familiar routines with occasional new experiences",
        "C": "I usually feel most comfortable with familiar environments and patterns",
        "D": "Write your own answer",
    },
    "S6": {
        "text": "When I’m dating someone I’m interested in, I usually feel most comfortable when…",
        "A": "The connection becomes clearly exclusive fairly early",
        "B": "Exclusivity develops gradually as the relationship grows",
        "C": "The connection stays open and undefined for a while",
        "D": "Write your own answer",
    },
}

PHASE2_QUESTIONS = {
    "P1": {
        "text": "The type of person I’m naturally drawn to is someone who…",
        "A": "Feels emotionally warm and present with people",
        "B": "Values honesty and speaks their mind clearly",
        "C": "Has a strong independent streak and their own space",
        "D": "Write your own answer",
    },
    "P2": {
        "text": "One dynamic that tends to drain my interest quickly is when someone…",
        "A": "Often changes plans or struggles with consistency",
        "B": "Feels overly controlling or tries to manage everything",
        "C": "Keeps emotional distance most of the time",
        "D": "Write your own answer",
    },
    "P3": {
        "text": "After a disagreement, I appreciate someone who…",
        "A": "Addresses the issue and works toward resolving it together",
        "B": "Takes a little space first, then reconnects to sort things out",
        "C": "Lets time smooth things over before revisiting it",
        "D": "Write your own answer",
    },
    "P4": {
        "text": "My preferred relationship structure tends to be…",
        "A": "Clearly monogamous",
        "B": "Mostly monogamous with some flexibility depending on the relationship",
        "C": "Comfortable with open or non-monogamous dynamics",
        "D": "Write your own answer",
    },
    "P5": {
        "text": "The kind of partner I tend to stay interested in over time is someone who…",
        "A": "Is steady, kind, and dependable",
        "B": "Balances stability with some spontaneity",
        "C": "Brings intensity and unpredictability into life",
        "D": "Write your own answer",
    },
    "P6": {
        "text": "I’m especially drawn to someone who…",
        "A": "Is curious about the world and likes exploring new experiences",
        "B": "Enjoys new experiences but also values routines",
        "C": "Prefers stability and familiar patterns",
        "D": "Write your own answer",
    },
}

# Option -> trait score map (0..1)
PHASE1_SCORES = {
    "S1": {
        "A": {3: 0.85, 4: 0.15, 7: 0.25, 1: 0.65},
        "B": {3: 0.50, 4: 0.50, 7: 0.55, 1: 0.60},
        "C": {3: 0.20, 4: 0.85, 7: 0.85, 1: 0.35},
    },
    "S2": {
        "A": {6: 0.85, 8: 0.85, 4: 0.20},
        "B": {6: 0.65, 8: 0.60, 4: 0.50},
        "C": {6: 0.20, 8: 0.20, 4: 0.85},
    },
    "S3": {
        "A": {9: 0.90, 7: 0.35, 2: 0.65},
        "B": {9: 0.65, 7: 0.60, 2: 0.70},
        "C": {9: 0.25, 7: 0.85, 2: 0.55},
    },
    "S4": {
        "A": {8: 0.90, 1: 0.60, 2: 0.70},
        "B": {8: 0.60, 1: 0.80, 2: 0.75},
        "C": {8: 0.25, 1: 0.35, 2: 0.60},
    },
    "S5": {
        "A": {5: 0.90, 7: 0.55},
        "B": {5: 0.60, 7: 0.60},
        "C": {5: 0.20, 7: 0.55},
    },
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
    "P6": {
        "A": {5: 0.90},
        "B": {5: 0.60},
        "C": {5: 0.20},
    },
}

TRAIT_WEIGHTS = {
    1: {"S1": 0.5, "S4": 0.6, "P1": 0.7, "P2": 0.6, "P5": 0.5},
    2: {"S3": 0.5, "S4": 0.6, "P2": 0.4},
    3: {"S1": 0.7, "P5": 0.6, "P1": 0.4},
    4: {"S1": 0.6,
