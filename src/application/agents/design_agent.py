"""The tool-calling agent loop.

One `run_turn` consumes a user message and drives the model through as many tool calls as
it needs (bounded by `max_steps`), streaming progress to an observer, and returns the
updated conversation history.
"""

from __future__ import annotations

import structlog

from application.agents.events import AgentObserver
from application.agents.prompts import SYSTEM_PROMPT
from application.tools.base import ToolContext
from application.tools.registry import ToolRegistry
from domain.models.conversation import Message, Role
from domain.ports.llm import LLMProvider

log = structlog.get_logger(__name__)


class DesignAgent:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        registry: ToolRegistry,
        max_steps: int,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._max_steps = max_steps

    def run_turn(
        self,
        *,
        ctx: ToolContext,
        history: list[Message],
        user_text: str,
        observer: AgentObserver,
    ) -> list[Message]:
        history.append(Message(role=Role.USER, text=user_text))
        tools = self._registry.specs()

        for step in range(self._max_steps):
            assistant = self._provider.complete(system=SYSTEM_PROMPT, messages=history, tools=tools)
            history.append(assistant)

            if assistant.text:
                observer.on_assistant_text(assistant.text)

            if not assistant.has_tool_calls:
                return history  # model yielded the turn back to the user

            results = []
            for call in assistant.tool_calls:
                observer.on_tool_call(call)
                result = self._registry.execute(ctx, call)
                observer.on_tool_result(result)
                results.append(result)

            history.append(Message(role=Role.TOOL, tool_results=tuple(results)))
            log.info("agent.step", step=step + 1, tool_calls=len(assistant.tool_calls))

        observer.on_step_limit(self._max_steps)
        history.append(
            Message(
                role=Role.ASSISTANT,
                text=(
                    f"I hit the {self._max_steps}-step limit for this turn. Tell me how to "
                    f"proceed and I'll continue."
                ),
            )
        )
        return history
