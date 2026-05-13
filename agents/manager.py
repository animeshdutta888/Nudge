from __future__ import annotations

import asyncio
import time
from typing import Optional

from observability.logger import ExecutionLogger
from schemas.shared import AgentFailure, CriticFeedback, SharedState


class ManagerAgent:
    def __init__(
        self,
        *,
        logger: ExecutionLogger,
        governance,
        retrieval,
        memory,
        semantic_cache,
        synthesis,
        critic,
        timeout_s: float,
        max_retries: int,
        global_budget_s: float,
    ) -> None:
        self._logger = logger
        self._governance = governance
        self._retrieval = retrieval
        self._memory = memory
        self._semantic_cache = semantic_cache
        self._synthesis = synthesis
        self._critic = critic
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._global_budget_s = global_budget_s

    async def run(self, state: SharedState) -> SharedState:
        started_at = time.perf_counter()
        state.execution_status = "RUNNING"
        self._logger.add(state, agent="manager", step="start", status="START", message="Planning task")

        decision = await self._governance.preflight(state)
        if not decision.allowed:
            state.execution_status = "REJECTED"
            state.governance_reason = decision.reason
            state.synthesis_output = f"Execution rejected by governance: {decision.reason}"
            self._logger.add(state, agent="governance", step="preflight", status="ERROR", message=decision.reason, payload={"checks": decision.checks})
            return state

        if decision.degraded:
            state.degraded_mode = True
            state.governance_reason = decision.reason
            self._logger.add(state, agent="governance", step="preflight", status="DEGRADED", message=decision.reason, payload={"checks": decision.checks})
        else:
            self._logger.add(state, agent="governance", step="preflight", status="OK", message="Safety checks passed", payload={"checks": decision.checks})

        if self._budget_exhausted(started_at):
            return self._degrade_on_budget(state)

        if self._should_skip_context(state.query):
            state.retrieved_chunks = []
            state.memory_context = []
            self._logger.add(state, agent="manager", step="context_gate", status="SKIP", message="Skipped retrieval and memory for low-context query")
        else:
            retrieval_task = self._run_with_retry(state, "retrieval", self._retrieval.retrieve, state, started_at=started_at)
            memory_task = self._run_with_retry(state, "memory", self._memory.recall, state, started_at=started_at)
            retrieval_result, memory_result = await asyncio.gather(retrieval_task, memory_task)
            state.retrieved_chunks = retrieval_result if isinstance(retrieval_result, list) else []
            state.memory_context = memory_result if isinstance(memory_result, list) else []
            state.retrieved_chunks = self._filter_relevant_chunks(state.query, state.retrieved_chunks)
            state.memory_context = self._filter_relevant_records(state.query, state.memory_context)

        if self._budget_exhausted(started_at):
            return self._degrade_on_budget(state)

        cache_result = await self._run_with_retry(state, "semantic_cache", self._semantic_cache.lookup, state.query, started_at=started_at)
        if cache_result is not None:
            state.synthesis_output = cache_result.answer
            state.synthesis_citations = cache_result.citations
            state.metadata["semantic_cache_hit"] = True
            state.metadata["semantic_cache_score"] = cache_result.score
            self._logger.add(
                state,
                agent="semantic_cache",
                step="hit",
                status="SKIP",
                message="Reused cached answer",
                payload={"score": cache_result.score, "threshold": self._semantic_cache.threshold},
            )
        else:
            state.metadata["semantic_cache_hit"] = False
            if not state.retrieved_chunks and not state.memory_context and not self._safe_without_context(state.query):
                state.degraded_mode = True
                state.synthesis_output = "I couldn't find enough validated local context to answer that safely."
                state.synthesis_citations = []
                self._logger.add(state, agent="manager", step="synthesis_gate", status="SKIP", message="Skipped synthesis because no validated context was available")
            else:
                answer, citations = await self._run_with_retry(state, "synthesis", self._synthesis.synthesize, state, started_at=started_at)
                state.synthesis_output = answer
                state.synthesis_citations = citations
                await self._run_with_retry(
                    state,
                    "semantic_cache_store",
                    self._semantic_cache.store,
                    state.query,
                    answer,
                    citations,
                    started_at=started_at,
                )

        state.critic_feedback = await self._run_with_retry(state, "critic", self._critic.validate, state, started_at=started_at)
        governance_feedback = await self._run_with_retry(state, "output_governance", self._governance.post_synthesis, state, started_at=started_at)
        if isinstance(governance_feedback, list):
            state.critic_feedback.extend(governance_feedback)
        if any(item.code == "TOOL_LOOP_LANGUAGE" or item.code == "INJECTION_COMPLIANCE" or item.code == "UNSUPPORTED_NO_CONTEXT" for item in state.critic_feedback):
            state.degraded_mode = True
            state.synthesis_output = "I couldn't complete that safely with validated local context, so I stopped and returned a constrained answer."
        has_error = any(item.severity == "error" for item in state.critic_feedback)
        state.execution_status = "DEGRADED" if state.degraded_mode or has_error else "COMPLETED"
        self._logger.add(
            state,
            agent="manager",
            step="finish",
            status="DEGRADED" if state.execution_status == "DEGRADED" else "OK",
            message="Execution finished",
            payload={"critic_feedback": [item.model_dump() for item in state.critic_feedback]},
        )
        return state

    async def _run_with_retry(self, state: SharedState, agent_name: str, fn, *args, started_at: Optional[float] = None):
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            done = self._logger.timed(state, agent=agent_name, step="run")
            try:
                timeout_s = self._timeout_s
                if started_at is not None:
                    remaining = self._remaining_budget_s(started_at)
                    if remaining <= 0:
                        raise TimeoutError("Global execution budget exhausted")
                    timeout_s = min(timeout_s, remaining)
                result = await asyncio.wait_for(fn(*args), timeout=timeout_s)
                done("OK", f"{agent_name.title()} completed", retry_count=attempt)
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                retryable = attempt < self._max_retries
                done("ERROR", str(exc), retry_count=attempt)
                state.failures.append(
                    AgentFailure(
                        error=str(exc),
                        agent=agent_name,
                        retryable=retryable,
                    )
                )
                if retryable:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                if agent_name == "critic":
                    return []
                if agent_name == "output_governance":
                    return []
                if agent_name == "semantic_cache":
                    return None
                if agent_name == "semantic_cache_store":
                    return None
                if agent_name == "synthesis":
                    return ("I hit a local runtime failure and switched to a degraded response path.", [])
                return []
        raise RuntimeError(str(last_exc) if last_exc is not None else f"{agent_name} failed")

    def _remaining_budget_s(self, started_at: float) -> float:
        return self._global_budget_s - (time.perf_counter() - started_at)

    def _budget_exhausted(self, started_at: float) -> bool:
        return self._remaining_budget_s(started_at) <= 0

    def _degrade_on_budget(self, state: SharedState) -> SharedState:
        state.degraded_mode = True
        state.execution_status = "DEGRADED"
        state.synthesis_output = "I hit the global execution budget and returned a constrained local response instead."
        self._logger.add(state, agent="manager", step="budget", status="DEGRADED", message="Global execution budget exhausted")
        return state

    def _should_skip_context(self, query: str) -> bool:
        tokens = _significant_tokens(query)
        if len(tokens) <= 2 and not any(word in query.lower() for word in ("remember", "saved", "did i", "my", "favourite", "favorite")):
            return True
        return False

    def _safe_without_context(self, query: str) -> bool:
        low = query.lower()
        return low in {"hi", "hello", "hey", "thanks", "thank you"} or any(
            marker in low for marker in ("what can you do", "help", "how do i use")
        )

    def _filter_relevant_chunks(self, query: str, chunks: list) -> list:
        query_tokens = set(_significant_tokens(query))
        if not query_tokens:
            return chunks[:5]
        filtered = [chunk for chunk in chunks if query_tokens.intersection(_significant_tokens(chunk.text))]
        return filtered if filtered else chunks[:5]

    def _filter_relevant_records(self, query: str, records: list) -> list:
        query_tokens = set(_significant_tokens(query))
        if not query_tokens:
            return records[:5]
        filtered = [record for record in records if query_tokens.intersection(_significant_tokens(record.text))]
        return filtered if filtered else records[:5]


def _significant_tokens(text: str) -> set[str]:
    stopwords = {
        "a",
        "about",
        "again",
        "an",
        "and",
        "any",
        "are",
        "based",
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
        "part",
        "question",
        "should",
        "that",
        "the",
        "to",
        "was",
        "what",
        "who",
        "why",
        "you",
    }
    return {token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if token not in stopwords and len(token) > 2}
