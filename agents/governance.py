from __future__ import annotations

import re
from urllib.parse import urlparse

from app.config import Config
from schemas.shared import CriticFeedback, GovernanceDecision, SharedState


class GovernanceAgent:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    async def preflight(self, state: SharedState) -> GovernanceDecision:
        checks: list[str] = []
        query = state.query.strip()
        if not query:
            return GovernanceDecision(allowed=False, reason="Empty query", checks=["empty_query"])

        if len(query) > 8000:
            return GovernanceDecision(allowed=False, reason="Query exceeds token budget", checks=["token_budget"])

        low = query.lower()
        if any(flag in low for flag in ("ignore previous instructions", "bypass governance", "self-dispatch", "rm -rf")):
            return GovernanceDecision(allowed=False, reason="Rejected unsafe or injection-like request", checks=["prompt_injection"])

        parsed = urlparse(self._cfg.ollama_base_url)
        host = (parsed.hostname or "").lower()
        if host not in {"localhost", "127.0.0.1"}:
            checks.append("non_local_llm_endpoint")
            return GovernanceDecision(
                allowed=True,
                degraded=True,
                reason="LLM endpoint is not local; runtime downgraded to offline-safe mode",
                checks=checks,
            )

        checks.append("local_first_ok")
        return GovernanceDecision(allowed=True, degraded=False, reason="", checks=checks)

    async def post_synthesis(self, state: SharedState) -> list[CriticFeedback]:
        answer = (state.synthesis_output or "").strip()
        findings: list[CriticFeedback] = []
        if not answer:
            return findings

        low = answer.lower()
        if any(flag in low for flag in ("i will call", "calling tool", "use tool", "next i will", "continue looping", "keep calling")):
            findings.append(
                CriticFeedback(
                    severity="error",
                    code="TOOL_LOOP_LANGUAGE",
                    message="Output suggested autonomous tool execution or looping behavior",
                )
            )
        if any(flag in low for flag in ("ignore previous instructions", "bypass governance", "override system", "comply with injection")):
            findings.append(
                CriticFeedback(
                    severity="error",
                    code="INJECTION_COMPLIANCE",
                    message="Output appears to comply with unsafe prompt-injection instructions",
                )
            )
        if not state.retrieved_chunks and not state.memory_context:
            if "couldn't find enough validated local context" not in low and "i don't have enough validated local context" not in low:
                findings.append(
                    CriticFeedback(
                        severity="error",
                        code="UNSUPPORTED_NO_CONTEXT",
                        message="Answer should degrade when no validated context is available",
                    )
                )
        significant_terms = _significant_terms(state.query)
        if significant_terms:
            answer_terms = set(_significant_terms(answer))
            if answer_terms and not answer_terms.intersection(significant_terms):
                findings.append(
                    CriticFeedback(
                        severity="warning",
                        code="LOW_QUERY_ALIGNMENT",
                        message="Answer may not stay focused on the user query",
                    )
                )
        return findings


def _significant_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    stopwords = {
        "a",
        "about",
        "an",
        "and",
        "any",
        "are",
        "did",
        "do",
        "i",
        "if",
        "in",
        "is",
        "it",
        "later",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "what",
        "who",
        "why",
        "you",
    }
    return [token for token in tokens if token not in stopwords and len(token) > 2]
