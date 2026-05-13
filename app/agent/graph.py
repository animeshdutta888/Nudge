LANGGRAPH_AVAILABLE = False


class AgentWorkflow:
    def __init__(self, agent) -> None:
        self._agent = agent

    def run(self, user_text: str) -> str:
        return self._agent.run_agent(user_text)
