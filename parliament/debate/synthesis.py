"""Synthesis step: assemble history, invoke backend, parse JSON, fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from parliament.agents.base import AgentBackend
from parliament.json_codec import dumps_pretty_json, loads_json
from parliament.json_fields import bool_field, float_field, str_field, str_list_field
from parliament.models import (
    BackendResult,
    ConsensusSignal,
    JSONObject,
    JSONValue,
    SynthesisResult,
    TurnRecord,
    TurnRole,
)

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def _profile_exists(profile: str) -> bool:
    """Return True if *profile* has a SOUL.md file."""
    path = Path.home() / ".hermes" / "profiles" / profile / "SOUL.md"
    return path.exists()


def _extract_json(text: str) -> str | None:
    """Extract JSON string from a ```json ... ``` block, or raw JSON object."""
    match = _JSON_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def _validate_and_strip(data: JSONObject, schema: JSONObject) -> JSONObject:
    """Strip extra fields and validate required fields / types against *schema*."""
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            raise ValueError("schema.properties must be an object")
        allowed = set(properties.keys())
        data = {k: v for k, v in data.items() if k in allowed}

    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list):
            raise ValueError("schema.required must be an array")
        for key in required:
            if not isinstance(key, str):
                raise ValueError("schema.required entries must be strings")
            if key not in data:
                raise ValueError(f"Missing required field: {key}")

    if "properties" in schema:
        properties = cast(dict[str, JSONValue], schema["properties"])
        for key, prop_schema in properties.items():
            if key not in data:
                continue
            if not isinstance(prop_schema, dict):
                raise ValueError(f"schema.properties.{key} must be an object")
            expected_type = prop_schema.get("type")
            value = data[key]
            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"Field {key} must be string")
            if expected_type == "number" and not isinstance(value, (int, float)):
                raise ValueError(f"Field {key} must be number")
            if expected_type == "boolean" and not isinstance(value, bool):
                raise ValueError(f"Field {key} must be boolean")

    return data


def _assemble_prompt(history: list[TurnRecord], schema: JSONObject) -> str:
    """Build the synthesis prompt from *history* and *schema*."""
    lines: list[str] = []
    for turn in history:
        lines.append(f"- Turn {turn.seq} ({turn.profile}): {turn.content}")
    history_text = "\n".join(lines) if lines else "(없음)"
    schema_text = dumps_pretty_json(schema)

    return (
        "아래 토론 내용을 바탕으로 최종 결론을 JSON 형식으로 정리하세요.\n\n"
        f"# 토론 내용\n{history_text}\n\n"
        "# 출력 형식\n"
        "아래 JSON Schema를 따르세요:\n"
        f"```json\n{schema_text}\n```\n\n"
        "반드시 ```json ... ``` 블록으로 출력하세요.\n"
    )


def _fallback_result(history: list[TurnRecord]) -> SynthesisResult:
    """Generate a rule-based fallback JSON when synthesis fails or no profile is available."""
    last_turns: dict[str, TurnRecord] = {}
    for turn in history:
        if turn.role != TurnRole.USER:
            last_turns[turn.profile] = turn

    all_agree = False
    if last_turns:
        all_agree = all(t.consensus_signal == ConsensusSignal.AGREE for t in last_turns.values())

    disagreeing: list[str] = []
    if not all_agree and last_turns:
        disagreeing = [
            p for p, t in last_turns.items() if t.consensus_signal != ConsensusSignal.AGREE
        ]

    return SynthesisResult(
        decision="inconclusive",
        confidence=0.0,
        reasoning="Synthesis failed after maximum retries.",
        consensus_reached=all_agree,
        disagreeing_profiles=disagreeing or None,
    )


class Synthesizer:
    """Orchestrate the synthesis step at the end of a session."""

    def __init__(self, backend: AgentBackend) -> None:
        self.backend = backend

    async def run(
        self,
        session_id: str,
        profile: str | None,
        history: list[TurnRecord],
        schema: JSONObject,
    ) -> SynthesisResult:
        """Run synthesis for *session_id* using *profile* (or fallback)."""
        if profile is not None and not _profile_exists(profile):
            raise ValueError(f"Synthesis profile '{profile}' does not exist")

        if profile is None:
            if _profile_exists("coordinator"):
                profile = "coordinator"
            else:
                return _fallback_result(history)

        prompt = _assemble_prompt(history, schema)

        for _attempt in range(3):  # initial + 2 retries
            result: BackendResult = await self.backend.invoke(profile, prompt)
            if result.code != 0:
                continue

            json_text = _extract_json(result.text)
            if json_text is None:
                continue

            try:
                parsed: JSONValue = loads_json(json_text)
            except ValueError:
                continue
            if not isinstance(parsed, dict):
                continue
            data = parsed

            try:
                data = _validate_and_strip(data, schema)
            except ValueError:
                continue

            # Success path
            return SynthesisResult(
                decision=str_field(data, "decision"),
                confidence=float_field(data, "confidence"),
                reasoning=str_field(data, "reasoning"),
                consensus_reached=bool_field(data, "consensus_reached"),
                disagreeing_profiles=str_list_field(data, "disagreeing_profiles"),
            )

        # All retries exhausted → fallback
        return _fallback_result(history)
