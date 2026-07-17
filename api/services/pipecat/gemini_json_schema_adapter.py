"""Dograh-specific Gemini adapter customizations."""

from typing import Any

from google.genai.types import Content
from loguru import logger

from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.adapters.services.gemini_adapter import GeminiLLMAdapter
from pipecat.processors.aggregators.llm_context import LLMContext


class DograhGeminiJSONSchemaAdapter(GeminiLLMAdapter):
    """Use Gemini's full JSON Schema tool parameter field.

    Pipecat's default Gemini adapter maps ``FunctionSchema.parameters`` into
    ``FunctionDeclaration.parameters``, which is backed by Google GenAI's
    stricter OpenAPI-style ``Schema`` model. MCP and imported tools may contain
    valid JSON Schema keywords such as ``const`` and ``not`` that are rejected
    by that model. ``parameters_json_schema`` is the Google GenAI field intended
    for full JSON Schema payloads.
    """

    def get_llm_invocation_params(
        self, context: LLMContext, *, system_instruction: str | None = None
    ):
        """Build Gemini invocation params, dropping orphaned function turns.

        Guards against Gemini's ``400`` — "Please ensure that function call turn
        comes immediately after a user turn or after a function response turn."

        In realtime (Gemini Live) mode the workflow spins up a *second*,
        standard Gemini text model for out-of-band work: variable extraction,
        context summarization, and the voicemail-detection classifier. None of
        those do function-calling, yet the context they are handed can still
        carry assistant ``function_call`` / ``function_response`` turns — e.g. a
        ``move_to_*`` node-transition tool call that leaks in while Gemini Live
        is reconnecting. Those orphaned function turns violate Gemini's
        content-ordering rule and crash the whole pipeline with a 400.

        A context that declares no tools is never a real function-calling
        conversation, so any function turn in it is a stray leftover and is safe
        to drop. Contexts that DO declare tools (the non-realtime Gemini main
        LLM) are left untouched so their function-calling keeps working. Gemini
        Live uses the base :class:`GeminiLLMAdapter`, not this one, so it is
        unaffected either way.
        """
        params = super().get_llm_invocation_params(
            context, system_instruction=system_instruction
        )

        tools = getattr(context, "tools", None)
        has_tools = isinstance(tools, ToolsSchema) and bool(tools.standard_tools)
        if not has_tools:
            params["messages"] = self._strip_function_turns(params["messages"])
        return params

    @staticmethod
    def _strip_function_turns(messages: list) -> list:
        """Drop ``function_call`` / ``function_response`` parts from contents.

        Textual parts are kept; a turn left with no parts is dropped entirely.
        Only ever applied to tool-less contexts, where function turns are always
        orphaned leftovers rather than a live tool exchange.
        """
        sanitized: list = []
        stripped = 0
        for msg in messages:
            parts = getattr(msg, "parts", None) or []
            kept = [
                p
                for p in parts
                if not getattr(p, "function_call", None)
                and not getattr(p, "function_response", None)
            ]
            if len(kept) != len(parts):
                stripped += len(parts) - len(kept)
            if not kept:
                continue
            if len(kept) == len(parts):
                sanitized.append(msg)
            else:
                sanitized.append(Content(role=getattr(msg, "role", None), parts=kept))
        if stripped:
            logger.debug(
                f"DograhGeminiJSONSchemaAdapter: stripped {stripped} orphaned "
                "function part(s) from a tool-less Gemini context"
            )
        return sanitized

    def to_provider_tools_format(
        self, tools_schema: ToolsSchema
    ) -> list[dict[str, Any]]:
        functions_schema = tools_schema.standard_tools
        if functions_schema:
            formatted_functions = []
            for func in functions_schema:
                func_dict = func.to_default_dict()
                parameters = func_dict.pop("parameters")
                func_dict["parameters_json_schema"] = parameters
                formatted_functions.append(func_dict)
            formatted_standard_tools = [{"function_declarations": formatted_functions}]
        else:
            formatted_standard_tools = []

        custom_gemini_tools = []
        if tools_schema.custom_tools:
            custom_gemini_tools = tools_schema.custom_tools.get(AdapterType.GEMINI, [])

        return formatted_standard_tools + custom_gemini_tools
