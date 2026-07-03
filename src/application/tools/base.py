"""Tool abstraction: a single capability the agent can invoke."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from application.design_toolkit import DesignToolkit
from application.session import DesignSession
from config.settings import Settings
from domain.models.conversation import ToolResult
from domain.ports.llm import ToolSpec


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool needs: mutable session + the shared design toolkit."""

    session: DesignSession
    toolkit: DesignToolkit
    settings: Settings


class Tool(ABC):
    """A named, schema-described capability. Implements the Command pattern."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, object]]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    @abstractmethod
    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult: ...
