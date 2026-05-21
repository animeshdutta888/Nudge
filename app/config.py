from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    model: str
    ollama_base_url: str
    data_dir: Path
    workspace_root: Path
    recent_logs_n: int
    timeout_s: float
    embed_model: str
    faiss_index_path: Path
    embeddings_path: Path
    notes_path: Path
    logs_path: Path
    persona_path: Path
    state_path: Path
    reminders_path: Path
    daily_plans_path: Path
    conversations_path: Path
    projects_path: Path
    semantic_cache_path: Path
    traces_db_path: Path
    max_retries: int
    agent_timeout_s: float
    semantic_cache_threshold: float
    global_budget_s: float

    @staticmethod
    def load() -> "Config":
        project_root = Path(__file__).resolve().parents[1]  # nudge/

        model = os.getenv("NUDGE_MODEL", "qwen2.5:3b")
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        data_dir = Path(os.getenv("NUDGE_DATA_DIR", str(project_root / "data"))).resolve()
        default_workspace_root = (Path.home() / "Projects") if (Path.home() / "Projects").exists() else project_root
        workspace_root = Path(os.getenv("NUDGE_WORKSPACE_ROOT", str(default_workspace_root))).resolve()

        try:
            recent_logs_n = int(os.getenv("NUDGE_RECENT_LOGS", "20"))
        except ValueError:
            recent_logs_n = 20

        try:
            timeout_s = float(os.getenv("NUDGE_TIMEOUT_S", "25"))
        except ValueError:
            timeout_s = 25.0

        embed_model = os.getenv("NUDGE_EMBED_MODEL", "nomic-embed-text")

        faiss_index_path = data_dir / "faiss.index"
        embeddings_path = data_dir / "embeddings.json"
        notes_path = data_dir / "notes.json"
        logs_path = data_dir / "logs.json"
        persona_path = data_dir / "persona.json"
        state_path = data_dir / "state.json"
        reminders_path = data_dir / "reminders.json"
        daily_plans_path = data_dir / "daily_plans.json"
        conversations_path = data_dir / "conversations.json"
        projects_path = data_dir / "projects.json"
        semantic_cache_path = data_dir / "semantic_cache.json"
        traces_db_path = data_dir / "execution_traces.sqlite3"
        try:
            max_retries = int(os.getenv("NUDGE_MAX_RETRIES", "2"))
        except ValueError:
            max_retries = 2
        try:
            agent_timeout_s = float(os.getenv("NUDGE_AGENT_TIMEOUT_S", "15"))
        except ValueError:
            agent_timeout_s = 15.0
        try:
            semantic_cache_threshold = float(os.getenv("NUDGE_SEMANTIC_CACHE_THRESHOLD", "0.75"))
        except ValueError:
            semantic_cache_threshold = 0.75
        try:
            global_budget_s = float(os.getenv("NUDGE_GLOBAL_BUDGET_S", "25"))
        except ValueError:
            global_budget_s = 25.0

        return Config(
            model=model,
            ollama_base_url=ollama_base_url,
            data_dir=data_dir,
            workspace_root=workspace_root,
            recent_logs_n=recent_logs_n,
            timeout_s=timeout_s,
            embed_model=embed_model,
            faiss_index_path=faiss_index_path,
            embeddings_path=embeddings_path,
            notes_path=notes_path,
            logs_path=logs_path,
            persona_path=persona_path,
            state_path=state_path,
            reminders_path=reminders_path,
            daily_plans_path=daily_plans_path,
            conversations_path=conversations_path,
            projects_path=projects_path,
            semantic_cache_path=semantic_cache_path,
            traces_db_path=traces_db_path,
            max_retries=max_retries,
            agent_timeout_s=agent_timeout_s,
            semantic_cache_threshold=semantic_cache_threshold,
            global_budget_s=global_budget_s,
        )
