from __future__ import annotations

import asyncio
import json
from typing import Any

from app.services.llm import LlmConfig, LlmError, ask_llm
from app.tools.reminders import list_reminders
from schemas.shared import RetrievalChunk, SharedState
from storage.local import LocalWorkspace


class SynthesisAgent:
    def __init__(self, cfg: LlmConfig, workspace: LocalWorkspace) -> None:
        self._cfg = cfg
        self._workspace = workspace

    async def synthesize(self, state: SharedState) -> tuple[str, list[str]]:
        if state.degraded_mode:
            return self._fallback_answer(state), self._fallback_citations(state)

        prompt = self._build_prompt(state)
        try:
            raw = await asyncio.to_thread(ask_llm, self._cfg, prompt)
            data = _parse_json(raw)
            answer = str(data.get("answer", "")).strip()
            citations = [str(item) for item in data.get("citations", []) if str(item).strip()]
            if answer:
                return answer, citations
        except LlmError:
            pass
        return self._fallback_answer(state), self._fallback_citations(state)

    def _build_prompt(self, state: SharedState) -> str:
        reminders = list_reminders(self._workspace.reminders_path, upcoming_days=14)
        reminder_lines = [
            f"- reminder:{item.id} due={item.due_ts or 'n/a'} text={item.text}"
            for item in reminders[:6]
        ]
        retrieval_lines = [
            f"- chunk:{chunk.chunk_id} kind={chunk.source_kind} ts={chunk.source_ts} text={chunk.text}"
            for chunk in state.retrieved_chunks[:6]
        ]
        memory_lines = [
            f"- {record.record_id} kind={record.kind} ts={record.ts} text={record.text}"
            for record in state.memory_context[:6]
        ]
        return (
            "You are the Nudge synthesis agent. Use only the provided local context.\n"
            "Answer only the user's asked point. Do not include unrelated facts from nearby context.\n"
            "Return JSON with keys: answer, citations.\n"
            "If context is insufficient, say so plainly and do not fabricate.\n\n"
            f"User query:\n{state.query}\n\n"
            "Retrieved context:\n"
            + ("\n".join(retrieval_lines) if retrieval_lines else "- none")
            + "\n\nMemory context:\n"
            + ("\n".join(memory_lines) if memory_lines else "- none")
            + "\n\nUpcoming reminders:\n"
            + ("\n".join(reminder_lines) if reminder_lines else "- none")
        )

    def _fallback_answer(self, state: SharedState) -> str:
        evidence = list(state.retrieved_chunks[:3]) + [
            RetrievalChunk(
                chunk_id=item.record_id,
                source_kind=item.kind,
                source_ts=item.ts,
                text=item.text,
            )
            for item in state.memory_context[:2]
        ]
        if evidence:
            lines = [f"I found {len(evidence)} local context item(s) related to that."]
            for item in evidence[:3]:
                lines.append(f"- [{item.source_kind}] {item.text}")
            lines.append("This answer is running in degraded local mode, so I limited it to stored context.")
            return "\n".join(lines)
        return "I couldn't find enough validated local context to answer that safely."

    def _fallback_citations(self, state: SharedState) -> list[str]:
        citations = [chunk.chunk_id for chunk in state.retrieved_chunks[:3]]
        citations.extend(item.record_id for item in state.memory_context[:2])
        return citations


def _parse_json(raw: str) -> dict[str, Any]:
    text = _strip_code_fence(raw.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {"answer": text, "citations": []}
            return _normalize_payload(parsed, text)
        return {"answer": text, "citations": []}
    return _normalize_payload(parsed, text)


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


def _normalize_payload(parsed: Any, fallback_text: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {"answer": fallback_text, "citations": []}
    answer = str(parsed.get("answer", "")).strip()
    citations_raw = parsed.get("citations", [])
    citations: list[str] = []
    if isinstance(citations_raw, list):
        for item in citations_raw:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    citations.append(text)
            else:
                value = str(item).strip()
                if value:
                    citations.append(value)
    return {
        "answer": answer or fallback_text,
        "citations": citations,
    }
