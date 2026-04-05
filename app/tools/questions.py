from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaQuestion:
    qid: str
    text: str


# Offline question bank. Keep questions short, personal, and actionable.
# IDs are stable so we can track what has been asked in state.json.
QUESTION_BANK: list[PersonaQuestion] = [
    PersonaQuestion("goals_now", "What are you trying to improve in your life right now (health, work, learning, relationships)?"),
    PersonaQuestion("energy_time", "When do you feel most energetic during the day?"),
    PersonaQuestion("sleep", "How has your sleep been lately, and what usually helps you sleep better?"),
    PersonaQuestion("exercise", "What kind of movement do you enjoy most (gym, sport, walking), and how often feels realistic?"),
    PersonaQuestion("food", "Any food preferences or constraints I should remember (veg, allergies, caffeine, sugar)?"),
    PersonaQuestion("stress", "What usually triggers stress for you, and what helps you calm down?"),
    PersonaQuestion("work_style", "What kind of workday suits you best: deep focus blocks or lots of small tasks?"),
    PersonaQuestion("learning_topic", "What are you learning these days, and why does it matter to you?"),
    PersonaQuestion("social", "Do you recharge alone or with people? What does a good weekend look like?"),
    PersonaQuestion("values", "What are 2 values you want your life to reflect more this year?"),
    PersonaQuestion("habits_one", "If you could build one habit in the next 30 days, what would it be?"),
    PersonaQuestion("blockers", "What is the biggest thing getting in your way lately?"),
    PersonaQuestion("motivation", "What reliably motivates you: progress, competition, curiosity, helping others, or something else?"),
    PersonaQuestion("boundaries", "Any boundaries you want me to respect (tone, reminders, topics, privacy)?"),
    PersonaQuestion("wins", "What’s a small win from the past week that you want to repeat?"),
    PersonaQuestion("time_budget", "How much time can you realistically spend per day on self-improvement (10/20/45+ min)?"),
]


def pick_persona_question(asked_ids: set[str]) -> PersonaQuestion:
    """Pick a mostly-unique question; repeats only after exhausting the bank."""
    remaining = [q for q in QUESTION_BANK if q.qid not in asked_ids]
    if remaining:
        # Shuffle remaining deterministically enough for variety across sessions.
        r = random.Random(len(asked_ids) + 17)
        return r.choice(remaining)
    r = random.Random(len(asked_ids) + 999)
    return r.choice(QUESTION_BANK)

