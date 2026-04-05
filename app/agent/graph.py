from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:
    END = "__end__"
    START = "__start__"
    StateGraph = None
    LANGGRAPH_AVAILABLE = False

if TYPE_CHECKING:
    from app.agent.core import NudgeAgent


class AgentGraphState(TypedDict, total=False):
    user_text: str
    plan_kind: str
    clean_text: str
    explicit: bool
    answer: str
    response: str
    persona: Any
    retrieved: list[Any]


class AgentWorkflow:
    def __init__(self, agent: "NudgeAgent") -> None:
        self._agent = agent
        self._app = self._build_graph()

    def run(self, user_text: str) -> str:
        state: AgentGraphState = {"user_text": user_text}
        if self._app is None:
            result = self._run_linear(state)
        else:
            result = self._app.invoke(state)
        return str(result.get("response", "")).strip()

    def _build_graph(self):
        if not LANGGRAPH_AVAILABLE or StateGraph is None:
            return None

        graph = StateGraph(AgentGraphState)
        graph.add_node("classify", self._classify)
        graph.add_node("explicit_save", self._explicit_save)
        graph.add_node("answer", self._answer)
        graph.add_node("memory_policy", self._memory_policy)
        graph.add_edge(START, "classify")
        graph.add_conditional_edges(
            "classify",
            self._route_after_classify,
            {
                "explicit_save": "explicit_save",
                "answer": "answer",
            },
        )
        graph.add_edge("explicit_save", END)
        graph.add_edge("answer", "memory_policy")
        graph.add_edge("memory_policy", END)
        return graph.compile()

    def _run_linear(self, state: AgentGraphState) -> AgentGraphState:
        state = {**state, **self._classify(state)}
        if self._route_after_classify(state) == "explicit_save":
            return {**state, **self._explicit_save(state)}
        state = {**state, **self._answer(state)}
        return {**state, **self._memory_policy(state)}

    def _classify(self, state: AgentGraphState) -> AgentGraphState:
        plan = self._agent._planner.classify(state["user_text"])
        return {
            "plan_kind": plan.kind,
            "clean_text": plan.clean_text,
            "explicit": plan.explicit,
        }

    def _route_after_classify(self, state: AgentGraphState) -> str:
        if state.get("explicit") and state.get("plan_kind") in {"log", "note"}:
            return "explicit_save"
        return "answer"

    def _explicit_save(self, state: AgentGraphState) -> AgentGraphState:
        kind = str(state.get("plan_kind", ""))
        text = str(state.get("clean_text", "")).strip()
        if kind == "log":
            self._agent._memory.append_log(text)
            self._agent._update_persona()
            return {"response": "Saved log."}
        self._agent._memory.add_note(text)
        self._agent._update_persona()
        return {"response": "Saved note."}

    def _answer(self, state: AgentGraphState) -> AgentGraphState:
        answer, persona, retrieved = self._agent._answer_core(str(state.get("clean_text", "")))
        return {
            "answer": answer,
            "persona": persona,
            "retrieved": retrieved,
        }

    def _memory_policy(self, state: AgentGraphState) -> AgentGraphState:
        response = self._agent._apply_memory_policy(
            str(state.get("answer", "")).strip(),
            str(state.get("clean_text", "")).strip(),
            state.get("persona"),
            state.get("retrieved"),
        )
        return {"response": response}
