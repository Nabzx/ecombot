"""Structured-output extraction, parsing and schema validation.

Turns raw provider text into a validated Pydantic model. Handles JSON wrapped in
Markdown fences or surrounded by prose, rejects anything that is not a single JSON
object, and never accepts a partially-parsed arbitrary dict — validation is always
against the task schema.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from app.llm.enums import ModelErrorCode

_FENCE_LANGS = ("```json", "```JSON", "```")


class StructuredOutputError(Exception):
    """Raised when provider output cannot be parsed/validated to the task schema."""

    def __init__(
        self, code: ModelErrorCode, message: str, *, errors: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.errors = errors or []


def extract_json_object(raw_text: str) -> str:
    """Extract a single JSON object string from ``raw_text``.

    Strips Markdown code fences and any surrounding prose by scanning for the first
    balanced ``{...}`` (respecting strings/escapes). Raises on no object found.
    """
    text = raw_text.strip()
    for fence in _FENCE_LANGS:
        if text.startswith(fence):
            text = text[len(fence) :]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            break

    start = text.find("{")
    if start == -1:
        raise StructuredOutputError(
            ModelErrorCode.INVALID_STRUCTURED_OUTPUT, "No JSON object found in output"
        )
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise StructuredOutputError(
        ModelErrorCode.INVALID_STRUCTURED_OUTPUT, "Unbalanced JSON object in output"
    )


def parse_json_object(raw_text: str) -> dict[str, object]:
    """Extract and JSON-parse a single object, or raise a typed error."""
    candidate = extract_json_object(raw_text)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            ModelErrorCode.INVALID_STRUCTURED_OUTPUT, f"Invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise StructuredOutputError(
            ModelErrorCode.INVALID_STRUCTURED_OUTPUT, "Output JSON is not an object"
        )
    return value


def validate_output(data: dict[str, object], schema: type[BaseModel]) -> BaseModel:
    """Validate a parsed object against a task schema, or raise a typed error."""
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        messages = [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        ]
        raise StructuredOutputError(
            ModelErrorCode.SCHEMA_VALIDATION_FAILED,
            "Output failed schema validation",
            errors=messages,
        ) from exc


def parse_and_validate(raw_text: str, schema: type[BaseModel]) -> BaseModel:
    """Full pipeline: extract JSON, parse, then validate against ``schema``."""
    return validate_output(parse_json_object(raw_text), schema)
