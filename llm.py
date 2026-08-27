"""LLM client abstraction.

Reads the model provider from the environment: ANTHROPIC_API_KEY (Anthropic),
OPENAI_API_KEY (OpenAI), or GEMINI_API_KEY (Google Gemini).  Prefers Anthropic
when multiple keys are present.  Returns structured, Pydantic-validated output
for planning and plain text for narrative.
"""

from __future__ import annotations

import os
from typing import TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

DEFAULT_MODEL_ANTHROPIC = os.environ.get("CHARTCOPILOT_MODEL", "claude-3-5-sonnet-20241022")
DEFAULT_MODEL_OPENAI = os.environ.get("CHARTCOPILOT_MODEL", "gpt-4o-mini")
DEFAULT_MODEL_GEMINI = os.environ.get("CHARTCOPILOT_MODEL", "gemini-2.5-flash")

STRUCT_TOOL_NAME = "structured_response"


class LLMError(RuntimeError):
    pass


def available_provider() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    return None


def _anthropic_client():
    try:
        import anthropic  # noqa: PLC0415

        return anthropic.Anthropic(timeout=45.0)
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise LLMError(f"Failed to initialise Anthropic client: {exc}") from exc


def _openai_client():
    try:
        from openai import OpenAI  # noqa: PLC0415

        return OpenAI(timeout=45.0)
    except Exception as exc:  # pragma: no cover
        raise LLMError(f"Failed to initialise OpenAI client: {exc}") from exc


def _gemini_client():
    try:
        from google import genai  # noqa: PLC0415

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return genai.Client(api_key=api_key)
    except Exception as exc:  # pragma: no cover
        raise LLMError(f"Failed to initialise Gemini client: {exc}") from exc


def _strip_for_strict(schema: dict) -> dict:
    """Coerce a pydantic JSON schema into OpenAI strict-mode shape."""
    props = schema.get("properties", {})
    schema = dict(schema)
    schema["required"] = list(props.keys())
    schema["additionalProperties"] = False
    schema.pop("$defs", None)
    for name, p in props.items():
        p = dict(p)
        p.pop("default", None)
        p.pop("title", None)
        props[name] = p
    schema["properties"] = props
    return schema


def _parse_json(text: str):
    import json  # noqa: PLC0415

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {exc}") from exc


def _openai_structured(system: str, user: str, model: type[M]) -> M:
    client = _openai_client()
    schema = _strip_for_strict(model.model_json_schema())
    try:
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL_OPENAI,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": model.__name__, "schema": schema, "strict": True},
            },
        )
    except Exception as exc:
        raise LLMError(f"OpenAI structured call failed: {exc}") from exc
    content = resp.choices[0].message.content
    if not content:
        raise LLMError("OpenAI returned an empty structured response")
    return model.model_validate(_parse_json(content))


def _anthropic_structured(system: str, user: str, model: type[M]) -> M:
    client = _anthropic_client()
    tool = {
        "name": STRUCT_TOOL_NAME,
        "description": f"Return a JSON object satisfying this schema: {model.model_json_schema()}",
        "input_schema": model.model_json_schema(),
    }
    try:
        resp = client.messages.create(
            model=DEFAULT_MODEL_ANTHROPIC,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": STRUCT_TOOL_NAME},
        )
    except Exception as exc:
        raise LLMError(f"Anthropic structured call failed: {exc}") from exc

    for block in resp.content:
        if block.type == "tool_use" and block.name == STRUCT_TOOL_NAME:
            return model.model_validate(block.input)
    raise LLMError("Anthropic did not invoke the structured response tool")


def _gemini_structured(system: str, user: str, model: type[M]) -> M:
    client = _gemini_client()
    try:
        resp = client.models.generate_content(
            model=DEFAULT_MODEL_GEMINI,
            contents=user,
            config={
                "system_instruction": system,
                "response_mime_type": "application/json",
                "response_schema": model.model_json_schema(),
            },
        )
    except Exception as exc:
        raise LLMError(f"Gemini structured call failed: {exc}") from exc
    if not resp.text:
        raise LLMError("Gemini returned an empty structured response")
    return model.model_validate_json(resp.text)


def llm_structured(system: str, user: str, model: type[M]) -> M:
    """Force a structured, schema-validated response from the LLM."""
    provider = available_provider()
    if provider == "anthropic":
        return _anthropic_structured(system, user, model)
    if provider == "openai":
        return _openai_structured(system, user, model)
    if provider == "gemini":
        return _gemini_structured(system, user, model)
    raise LLMError("No LLM provider configured (set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY)")


def _gemini_text(system: str, user: str, max_tokens: int = 1200) -> str:
    client = _gemini_client()
    try:
        resp = client.models.generate_content(
            model=DEFAULT_MODEL_GEMINI,
            contents=user,
            config={
                "system_instruction": system,
                "max_output_tokens": max_tokens,
            },
        )
    except Exception as exc:
        raise LLMError(f"Gemini text call failed: {exc}") from exc
    return (resp.text or "").strip()


def llm_text(system: str, user: str, max_tokens: int = 1200) -> str:
    """Plain-text LLM call for narrative synthesis."""
    provider = available_provider()
    if provider == "anthropic":
        client = _anthropic_client()
        try:
            resp = client.messages.create(
                model=DEFAULT_MODEL_ANTHROPIC,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise LLMError(f"Anthropic text call failed: {exc}") from exc
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "\n".join(parts).strip()
    if provider == "openai":
        client = _openai_client()
        try:
            resp = client.chat.completions.create(
                model=DEFAULT_MODEL_OPENAI,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI text call failed: {exc}") from exc
        return (resp.choices[0].message.content or "").strip()
    if provider == "gemini":
        return _gemini_text(system, user, max_tokens)
    raise LLMError("No LLM provider configured (set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY)")


def explain_llm_mode() -> str | None:
    """Human-readable description of the active LLM mode (for the UI)."""
    return {
        "anthropic": "claude (Anthropic)",
        "openai": "OpenAI",
        "gemini": "gemini (Google)",
    }.get(available_provider())