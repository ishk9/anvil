"""`LLMProvider` adapter for OpenAI chat completions (tools + vision).

OpenAI `tool` messages only accept string content, so rendered images are attached via a
trailing `user` message with image parts — an adapter-internal detail invisible to the
rest of the system.
"""

from __future__ import annotations

import json
from typing import Any, cast

import structlog
from openai import OpenAI

from domain.models.conversation import Message, Role, ToolCall
from domain.ports.llm import ToolSpec
from infrastructure.llm.encoding import data_uri

log = structlog.get_logger(__name__)


class OpenAIProvider:
    def __init__(self, *, api_key: str, model: str, max_tokens: int, temperature: float) -> None:
        self._client = OpenAI(api_key=api_key)
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
        payload: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            payload.extend(self._to_openai(message))

        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            tools=cast("Any", [self._tool_payload(t) for t in tools]),
            tool_choice="auto",
            messages=cast("Any", payload),
        )
        return self._from_openai(response)

    def _tool_payload(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    def _to_openai(self, message: Message) -> list[dict[str, Any]]:
        if message.role is Role.TOOL:
            out: list[dict[str, Any]] = []
            image_parts: list[dict[str, Any]] = []
            for result in message.tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "content": result.text,
                    }
                )
                image_parts.extend(
                    {"type": "image_url", "image_url": {"url": data_uri(img)}}
                    for img in result.images
                )
            if image_parts:
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Rendered views of the current model:"},
                            *image_parts,
                        ],
                    }
                )
            return out

        if message.role is Role.ASSISTANT:
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            return [entry]

        return [{"role": "user", "content": message.text}]

    def _from_openai(self, response: Any) -> Message:
        choice = response.choices[0].message
        tool_calls: list[ToolCall] = []
        for call in choice.tool_calls or []:
            arguments = json.loads(call.function.arguments or "{}")
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return Message(
            role=Role.ASSISTANT,
            text=(choice.content or "").strip(),
            tool_calls=tuple(tool_calls),
        )
