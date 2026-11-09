"""Provider-neutral model response types and the local Ollama implementation."""

from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

MODEL_ADAPTER_VERSION = "ollama-native-tools-v1"


class ModelToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()


class ModelAdapter(Protocol):
    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...


class OllamaAdapter:
    """Small adapter around Ollama's native chat API.

    Only visible message content and declared tool calls are returned. Provider-specific
    reasoning fields are deliberately ignored and are never added to the evidence stream.
    """

    adapter_version = MODEL_ADAPTER_VERSION

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 180.0,
        max_output_tokens: int = 512,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "tools": tools,
            "options": {"temperature": 0, "num_predict": self.max_output_tokens},
        }
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            response = client.post("/api/chat", json=payload)
            response.raise_for_status()
        message = response.json().get("message", {})
        calls: list[ModelToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, dict) and isinstance(function.get("name"), str):
                calls.append(ModelToolCall(name=function["name"], arguments=arguments))
        content = message.get("content")
        return ModelResponse(
            content=content if isinstance(content, str) else "",
            tool_calls=tuple(calls),
        )
