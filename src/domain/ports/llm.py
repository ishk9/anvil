"""Port for a tool-calling, vision-capable chat model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from domain.models.conversation import Message


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A JSON-schema tool declaration, vendor-neutral."""

    name: str
    description: str
    input_schema: dict[str, object]


@runtime_checkable
class LLMProvider(Protocol):
    """Strategy for a chat completion round.

    Implementations must support: a system prompt, multi-turn history (including tool
    results with images), and returning either assistant text, tool calls, or both.
    """

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> Message: ...
