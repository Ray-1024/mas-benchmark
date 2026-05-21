from __future__ import annotations

from pathlib import Path

from agent import AgentDefinition

from pydantic import BaseModel, SecretStr

from openhands.sdk import Agent, Conversation, LLM, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


class OpenHandsRunnerConfig(BaseModel):
    model: str
    api_key: SecretStr
    base_url: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = 4096
    max_iteration_per_run: int = 80

class OpenhandsRunner:
    def __init__(self, config: OpenHandsRunnerConfig, workspace_root: Path) -> None:
        self.config = config
        self.workspace_root = workspace_root.resolve()

    def run_task(self, role: AgentDefinition, task: str) -> None:
        llm = LLM(
            model=self.config.model,
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
        )
        agent = Agent(
            llm=llm,
            system_prompt=role.system_prompt,
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
                Tool(name=TaskTrackerTool.name),
            ],
        )
        conversation = Conversation(
            agent=agent,
            workspace=str(self.workspace_root),
            max_iteration_per_run=self.config.max_iteration_per_run,
        )
        conversation.send_message(task)
        conversation.run()

