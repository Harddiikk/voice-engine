from unittest.mock import patch

from google.genai.types import GenerateContentConfig, LiveConnectConfig
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiJSONSchemaAdapter,
)
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_vertex import (
    DograhGeminiLiveVertexLLMService,
)
from api.services.pipecat.service_factory import (
    DograhGoogleLLMService,
    DograhGoogleVertexLLMService,
    create_llm_service_from_provider,
)


def test_gemini_tools_use_json_schema_parameters_for_external_schemas():
    function_schema = FunctionSchema(
        name="customer_lookup",
        description="Look up a customer by email.",
        properties={
            "customerEmail": {
                "description": "Customer email address",
                "anyOf": [
                    {"anyOf": [{"not": {}}]},
                    {"const": ""},
                ],
            },
            "metadata": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        required=["customerEmail"],
    )

    tools = DograhGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert (
        declaration["parameters_json_schema"]["properties"]["customerEmail"]["anyOf"][
            0
        ]["anyOf"][0]["not"]
        == {}
    )
    assert (
        declaration["parameters_json_schema"]["properties"]["customerEmail"]["anyOf"][
            1
        ]["const"]
        == ""
    )
    assert declaration["parameters_json_schema"]["properties"]["metadata"][
        "additionalProperties"
    ] == {"type": "string"}

    GenerateContentConfig(tools=tools)


def test_gemini_tools_use_json_schema_parameters_for_no_argument_tools():
    function_schema = FunctionSchema(
        name="refresh_context",
        description="Refresh the current context.",
        properties={},
        required=[],
    )

    tools = DograhGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert declaration["parameters_json_schema"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }

    GenerateContentConfig(tools=tools)


def test_google_service_classes_use_dograh_gemini_adapter_class():
    assert DograhGoogleLLMService.adapter_class is DograhGeminiJSONSchemaAdapter
    assert DograhGoogleVertexLLMService.adapter_class is DograhGeminiJSONSchemaAdapter


def test_google_llm_service_factory_uses_dograh_service_class():
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleLLMService",
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE.value,
            model="gemini-2.5-flash",
            api_key="test-api-key",
        )

    assert result is mock_service.return_value
    assert mock_service.call_args.kwargs["api_key"] == "test-api-key"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-flash"


def test_google_vertex_llm_service_factory_uses_dograh_service_class():
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleVertexLLMService",
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE_VERTEX.value,
            model="gemini-2.5-pro",
            api_key=None,
            project_id="demo-project",
            location="us-central1",
            credentials='{"type":"service_account"}',
        )

    assert result is mock_service.return_value
    assert mock_service.call_args.kwargs["project_id"] == "demo-project"
    assert mock_service.call_args.kwargs["location"] == "us-central1"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-pro"


def test_gemini_live_service_classes_use_dograh_gemini_adapter_class():
    assert DograhGeminiLiveLLMService.adapter_class is DograhGeminiJSONSchemaAdapter
    # Vertex Live inherits adapter_class from DograhGeminiLiveLLMService via MRO.
    assert (
        DograhGeminiLiveVertexLLMService.adapter_class is DograhGeminiJSONSchemaAdapter
    )


def test_gemini_live_config_accepts_json_schema_tools():
    function_schema = FunctionSchema(
        name="customer_lookup",
        description="Look up a customer by email.",
        properties={
            "customerEmail": {
                "description": "Customer email address",
                "anyOf": [{"not": {}}, {"const": ""}],
            },
        },
        required=["customerEmail"],
    )

    tools = DograhGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert "parameters_json_schema" in declaration

    # Gemini Live validates tools through LiveConnectConfig rather than
    # GenerateContentConfig; it must also accept the raw JSON Schema payload.
    LiveConnectConfig(tools=tools)


# ---------------------------------------------------------------------------
# Orphaned-function-turn stripping (guards the Gemini "function call turn must
# come immediately after a user turn or after a function response turn" 400 that
# crashed realtime calls on every node transition).
# ---------------------------------------------------------------------------


def _messages_with_function_turn():
    """A conversation carrying a move_to_* tool call + its result."""
    return [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "move_to_qualification",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "done"}'},
        {"role": "user", "content": "yes please"},
    ]


def _has_function_parts(messages) -> bool:
    for m in messages:
        for p in getattr(m, "parts", None) or []:
            if getattr(p, "function_call", None) or getattr(
                p, "function_response", None
            ):
                return True
    return False


def _all_text(messages) -> str:
    return " ".join(
        p.text
        for m in messages
        for p in (getattr(m, "parts", None) or [])
        if getattr(p, "text", None)
    )


def test_tool_less_context_strips_orphaned_function_turns():
    # No tools declared: this is an out-of-band / classifier context. Any
    # function turn in it is a stray leftover that would trigger the 400.
    context = LLMContext(messages=_messages_with_function_turn())

    params = DograhGeminiJSONSchemaAdapter().get_llm_invocation_params(context)

    assert not _has_function_parts(params["messages"])
    # The surrounding conversation text is preserved so extraction/summary/
    # classification still have something to work with.
    text = _all_text(params["messages"])
    assert "hi" in text
    assert "yes please" in text


def test_tool_less_context_with_only_a_function_call_drops_that_turn():
    context = LLMContext(
        messages=[
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "move_to_end", "arguments": "{}"},
                    }
                ],
            },
        ]
    )

    params = DograhGeminiJSONSchemaAdapter().get_llm_invocation_params(context)

    assert not _has_function_parts(params["messages"])
    assert "hello" in _all_text(params["messages"])


def test_tool_bearing_context_preserves_function_turns():
    # A real function-calling conversation (the non-realtime Gemini main LLM,
    # or a Gemini Live node with tools) must keep its function turns intact.
    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="move_to_qualification",
                description="Advance the call.",
                properties={},
                required=[],
            )
        ]
    )
    context = LLMContext(messages=_messages_with_function_turn(), tools=tools)

    params = DograhGeminiJSONSchemaAdapter().get_llm_invocation_params(context)

    assert _has_function_parts(params["messages"])
