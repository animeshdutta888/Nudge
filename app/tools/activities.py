from __future__ import annotations

from typing import Any


def recommend_activities_from_persona(persona: dict[str, Any]) -> list[str]:
    """
    Deterministic, offline suggestions based on persona fields.
    This is used as a fallback when the LLM is unavailable or low-quality.
    """
    interests = _as_lower_list(persona.get("interests"))
    habits = _as_lower_list(persona.get("habits"))
    focus = _as_lower_list(persona.get("current_focus"))

    out: list[str] = []

    if _has_any(interests + habits, ("badminton",)):
        out.extend(
            [
                "Badminton: 30 min skill session (footwork ladder + 3x10 shadow swings).",
                "Badminton: play 1 focused game where you track 1 thing (serve placement or net shots).",
                "Mobility + injury-proofing: 12 min ankles/hips + calf raises + glute bridges.",
            ]
        )

    if _has_any(interests + habits, ("gym", "strength", "workout")):
        out.extend(
            [
                "Strength: 30-40 min full-body (squat/hinge/push/pull) at easy effort, focus on form.",
                "Recovery: 20 min walk + 5 min stretch (hamstrings/hips/shoulders).",
            ]
        )

    if _has_any(interests, ("music", "retro", "coldplay")):
        out.extend(
            [
                "Music: make a 20-song 'retro focus' playlist and use it for a 45-min deep work block.",
                "Music: listen to one full album start-to-finish and jot 3 notes you liked.",
            ]
        )

    if _has_any(interests, ("football", "soccer", "cule", "barca", "valencia")):
        out.extend(
            [
                "Football: watch a 10-min tactics breakdown (offline video you already have) and note 1 pattern.",
                "Football: play a casual 30-min kickabout or short dribbling routine.",
            ]
        )

    if _has_any(focus, ("nudge", "build", "building", "app", "project")):
        out.extend(
            [
                "Build: one 45-min deep work sprint with a single outcome (1 bug fixed or 1 feature shipped).",
                "Build: 10-min cleanup (rename, delete dead code, add one test) to keep momentum.",
            ]
        )

    # Always include a few universal, low-effort options.
    out.extend(
        [
            "Mind: 5-min journal: 'What matters today?' + one small next action.",
            "Body: 15-min walk outside (no phone) to reset attention.",
            "Home: 10-min tidy of one area to reduce friction.",
        ]
    )

    return _dedupe_keep_order(out)[:10]


def _as_lower_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip().lower()]
    return []


def _has_any(values: list[str], needles: tuple[str, ...]) -> bool:
    return any(any(n in v for n in needles) for v in values)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

