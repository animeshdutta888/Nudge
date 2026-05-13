from __future__ import annotations

import json


def assistant_display_text(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    if clean.startswith("```"):
        lines = clean.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            clean = "\n".join(lines[1:-1]).strip()
    if clean.startswith("{") and clean.endswith("}") and '"answer"' in clean:
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            return clean
        if isinstance(parsed, dict):
            answer = str(parsed.get("answer", "")).strip()
            return answer or clean
    return clean
