from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.governance import GovernanceAgent
from app.config import Config
from app.services.semantic_cache import _is_sensitive_query, _normalize_query
from schemas.shared import SharedState


class GuardrailTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_governance_blocks_tool_loop_language(self) -> None:
        cfg = _temp_config()
        agent = GovernanceAgent(cfg)
        state = SharedState(
            run_id="run-1",
            query="did I skip gym any time?",
            synthesis_output="I will call tools again and keep calling tool memory until I know more.",
        )
        findings = await agent.post_synthesis(state)
        self.assertTrue(any(item.code == "TOOL_LOOP_LANGUAGE" for item in findings))

    async def test_output_governance_requires_degraded_answer_without_context(self) -> None:
        cfg = _temp_config()
        agent = GovernanceAgent(cfg)
        state = SharedState(
            run_id="run-2",
            query="who is my favourite footballer?",
            synthesis_output="Your favorite footballer is probably Messi.",
        )
        findings = await agent.post_synthesis(state)
        self.assertTrue(any(item.code == "UNSUPPORTED_NO_CONTEXT" for item in findings))


class SemanticCacheHelpersTests(unittest.TestCase):
    def test_normalize_query_collapses_spacing(self) -> None:
        self.assertEqual(_normalize_query("  Did   I skip   gym? "), "did i skip gym?")

    def test_sensitive_query_detection(self) -> None:
        self.assertTrue(_is_sensitive_query("my api key is abc"))
        self.assertFalse(_is_sensitive_query("did I skip gym any time"))


def _temp_config() -> Config:
    tmp = Path(tempfile.mkdtemp())
    return Config(
        model="qwen2.5:3b",
        ollama_base_url="http://localhost:11434",
        data_dir=tmp,
        recent_logs_n=20,
        timeout_s=25.0,
        embed_model="nomic-embed-text",
        faiss_index_path=tmp / "faiss.index",
        embeddings_path=tmp / "embeddings.json",
        notes_path=tmp / "notes.json",
        logs_path=tmp / "logs.json",
        persona_path=tmp / "persona.json",
        state_path=tmp / "state.json",
        reminders_path=tmp / "reminders.json",
        conversations_path=tmp / "conversations.json",
        projects_path=tmp / "projects.json",
        semantic_cache_path=tmp / "semantic_cache.json",
        traces_db_path=tmp / "execution_traces.sqlite3",
        max_retries=2,
        agent_timeout_s=15.0,
        semantic_cache_threshold=0.75,
        global_budget_s=25.0,
    )
