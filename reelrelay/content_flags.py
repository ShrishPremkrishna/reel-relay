"""Stable editorial flags shared by model prompts, policy checks and the UI.

Allowing a flag changes the operator's hold policy; it does not establish rights,
make a clip suitable for every audience, or weaken technical validation.
"""

CONTENT_FLAGS = [
    {
        "id": "strong_language",
        "label": "Strong language",
        "description": "Ordinary profanity without slurs or threats.",
        "default_action": "allow",
    },
    {
        "id": "slurs",
        "label": "Slurs",
        "description": "Derogatory slurs targeting a protected or marginalized group.",
        "default_action": "hold",
    },
    {
        "id": "hate_speech",
        "label": "Hate speech",
        "description": "Hateful attacks or dehumanization aimed at a protected group.",
        "default_action": "hold",
    },
    {
        "id": "threats",
        "label": "Threats",
        "description": "Threats of harm directed at a person or group.",
        "default_action": "hold",
    },
    {
        "id": "harassment",
        "label": "Harassment",
        "description": "Targeted abusive or humiliating treatment of a person.",
        "default_action": "hold",
    },
    {
        "id": "sexual_content",
        "label": "Sexual content",
        "description": "Explicit sexual discussion or sexual activity.",
        "default_action": "hold",
    },
    {
        "id": "nudity",
        "label": "Nudity",
        "description": "Visible nudity or intimate body parts.",
        "default_action": "hold",
    },
    {
        "id": "game_violence",
        "label": "Game violence",
        "description": "Non-graphic fictional violence shown in gameplay.",
        "default_action": "hold",
    },
    {
        "id": "real_world_violence",
        "label": "Real-world violence",
        "description": "Violence involving real people or animals.",
        "default_action": "hold",
    },
    {
        "id": "graphic_violence",
        "label": "Graphic violence",
        "description": "Graphic injury, gore or disturbing violence, including fictional scenes.",
        "default_action": "hold",
    },
    {
        "id": "self_harm",
        "label": "Self-harm",
        "description": "Self-harm or suicide content needing contextual review.",
        "default_action": "hold",
    },
    {
        "id": "dangerous_acts",
        "label": "Dangerous acts",
        "description": "Real dangerous behavior that could encourage imitation.",
        "default_action": "hold",
    },
    {
        "id": "personal_information",
        "label": "Personal information",
        "description": "Visible or spoken private identifying information.",
        "default_action": "hold",
    },
    {
        "id": "major_spoilers",
        "label": "Major spoilers",
        "description": "A substantial story reveal or ending.",
        "default_action": "hold",
    },
    {
        "id": "unclear_speech",
        "label": "Unclear speech",
        "description": "Unresolved dialogue needed to understand the selected moment.",
        "default_action": "hold",
    },
    {
        "id": "uncertain_framing",
        "label": "Uncertain framing",
        "description": "The subject or decisive action cannot be framed reliably.",
        "default_action": "hold",
    },
    {
        "id": "misleading_edit",
        "label": "Misleading edit",
        "description": "Cuts or presentation could materially change the source's meaning.",
        "default_action": "hold",
    },
    {
        "id": "incomplete_joke",
        "label": "Incomplete payoff",
        "description": "The setup or payoff needed for a coherent moment is missing.",
        "default_action": "hold",
    },
]

KNOWN_FLAGS = frozenset(row["id"] for row in CONTENT_FLAGS)


def held_flags(flags, allowed_flags):
    """Unknown labels always hold, even if imported into an allow-list."""
    allowed = set(allowed_flags) & KNOWN_FLAGS
    return set(flags) - allowed
