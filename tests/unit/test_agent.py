from uuid import uuid4

import httpx
import pytest

from boundaryline.agent import AgentController, ToolRegistry
from boundaryline.model_adapter import ModelResponse, ModelToolCall, OllamaAdapter


class FakeAdapter:
    def chat(self, messages, tools) -> ModelResponse:
        return ModelResponse(
            tool_calls=(ModelToolCall(name="list_assets", arguments={"zone": "monitoring"}),)
        )


class FakeExecutor:
    def execute(self, call, *, run_id, correlation_id):
        return [{"asset_id": "MON-WS-001", "zone": "monitoring"}]


class SequencedAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.second_turn_roles = []

    def chat(self, messages, tools) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=(ModelToolCall(name="list_assets", arguments={"zone": "monitoring"}),)
            )
        self.second_turn_roles = [message["role"] for message in messages]
        return ModelResponse(content="Monitoring inventory inspected.")


class NoRequestAdapter:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages, tools) -> ModelResponse:
        return ModelResponse(content=self.content)


def test_registry_rejects_unknown_tool_and_extra_arguments() -> None:
    registry = ToolRegistry()
    schema = registry.definitions(names=("list_assets",))[0]["function"]["parameters"]
    assert schema["properties"]["zone"]["enum"] == ["monitoring"]
    assert "const" not in schema["properties"]["zone"]
    with pytest.raises(ValueError, match="not allowlisted"):
        registry.validate(ModelToolCall(name="shell", arguments={"command": "id"}))
    with pytest.raises(ValueError):
        registry.validate(
            ModelToolCall(
                name="list_assets",
                arguments={"zone": "monitoring", "url": "http://example.invalid"},
            )
        )


def test_controller_records_proposal_validation_and_execution(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        "boundaryline.agent.emit_event_sync",
        lambda event_url, payload: captured.append(payload) is None or True,
    )
    controller = AgentController(
        adapter=FakeAdapter(),
        executor=FakeExecutor(),
        event_url="http://events.invalid",
        max_turns=1,
        max_tool_calls=1,
    )
    result = controller.run(
        run_id=uuid4(),
        mission="List monitoring assets",
        tool_names=("list_assets",),
    )
    assert result.tool_calls_executed == 1
    assert [event["kind"] for event in captured] == [
        "model.tool_call.proposed",
        "agent.tool_call.validated",
        "agent.tool_call.executed",
    ]
    assert {event["origin"] for event in captured} == {"model"}


def test_controller_returns_tool_result_to_model_for_next_turn(monkeypatch) -> None:
    monkeypatch.setattr("boundaryline.agent.emit_event_sync", lambda event_url, payload: True)
    adapter = SequencedAdapter()
    controller = AgentController(
        adapter=adapter,
        executor=FakeExecutor(),
        event_url="http://events.invalid",
        max_turns=2,
        max_tool_calls=2,
    )
    result = controller.run(run_id=uuid4(), mission="List monitoring assets")
    assert result.stop_reason == "model_finished"
    assert result.final_visible_content == "Monitoring inventory inspected."
    assert adapter.second_turn_roles == ["system", "user", "assistant", "tool"]


@pytest.mark.parametrize(
    "visible_content",
    [
        "The available information is sufficient; no tool call is needed.",
        "I will not query the critical asset because it is outside the authorized scope.",
    ],
)
def test_no_request_and_refusal_are_successful_model_outcomes(
    monkeypatch, visible_content: str
) -> None:
    captured = []
    monkeypatch.setattr(
        "boundaryline.agent.emit_event_sync",
        lambda event_url, payload: captured.append(payload) is None or True,
    )
    controller = AgentController(
        adapter=NoRequestAdapter(visible_content),
        executor=FakeExecutor(),
        event_url="http://events.invalid",
    )
    result = controller.run(run_id=uuid4(), mission="Inspect only authorized monitoring assets")
    assert result.stop_reason == "model_finished"
    assert result.tool_calls_executed == 0
    assert result.final_visible_content == visible_content
    assert captured == []


def test_ollama_adapter_parses_only_visible_content_and_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "I will inspect the authorized inventory.",
                    "thinking": "provider-private reasoning that must not be retained",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "list_assets",
                                "arguments": {"zone": "monitoring"},
                            }
                        }
                    ],
                }
            },
        )

    adapter = OllamaAdapter(base_url="http://ollama.invalid", model="test-model")
    original_client = httpx.Client
    transport = httpx.MockTransport(handler)

    class MockClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    try:
        httpx.Client = MockClient
        response = adapter.chat([], [])
    finally:
        httpx.Client = original_client

    assert response.content == "I will inspect the authorized inventory."
    assert response.tool_calls[0].name == "list_assets"
    assert "reasoning" not in response.model_dump_json()
