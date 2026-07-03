"""`LLMProvider` adapter for Anthropic Claude (messages API, tools + vision)."""

from __future__ import annotations

from typing import Any, cast

import structlog
from anthropic import Anthropic

from domain.models.conversation import Message, Role, ToolCall
from domain.ports.llm import ToolSpec
from infrastructure.llm.encoding import encode_image_base64

log = structlog.get_logger(__name__)


class AnthropicProvider:
    def __init__(self, *, api_key: str, model: str, max_tokens: int, temperature: float) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> Message:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=system,
            tools=cast("Any", [self._tool_payload(t) for t in tools]),
            messages=cast("Any", [self._to_anthropic(m) for m in messages]),
        )
        return self._from_anthropic(response)

    def _tool_payload(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    def _to_anthropic(self, message: Message) -> dict[str, Any]:
        if message.role is Role.TOOL:
            content: list[dict[str, Any]] = []
            for result in message.tool_results:
                blocks: list[dict[str, Any]] = [{"type": "text", "text": result.text}]
                for image in result.images:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image.media_type,
                                "data": encode_image_base64(image),
                            },
                        }
                    )
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": blocks,
                        "is_error": not result.ok,
                    }
                )
            return {"role": "user", "content": content}

        if message.role is Role.ASSISTANT:
            blocks = []
            if message.text:
                blocks.append({"type": "text", "text": message.text})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            return {"role": "assistant", "content": blocks}

        return {"role": "user", "content": [{"type": "text", "text": message.text}]}

    def _from_anthropic(self, response: Any) -> Message:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        return Message(
            role=Role.ASSISTANT,
            text="\n".join(text_parts).strip(),
            tool_calls=tuple(tool_calls),
        )
