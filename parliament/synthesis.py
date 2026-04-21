"""Synthesis step: assemble history, invoke backend, parse JSON, fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from parliament.backends.base import AgentBackend
from parliament.models import BackendResult, SynthesisResult, TurnRecord

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


def _validate_and_strip(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Strip extra fields and validate required fields / types against *schema*."""
    if "properties" in schema:
        allowed = set(schema["properties"].keys())
        data = {k: v for k, v in data.items() if k in allowed}

    if "required" in schema:
        for key in schema["required"]:
            if key not in data:
                raise ValueError(f"Missing required field: {key}")

    if "properties" in schema:
        for key, prop_schema in schema["properties"].items():
            if key not in data:
                continue
            expected_type = prop_schema.get("type")
            value = data[key]
            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"Field {key} must be string")
            if expected_type == "number" and not isinstance(value, (int, float)):
                raise ValueError(f"Field {key} must be number")
            if expected_type == "boolean" and not isinstance(value, bool):
                raise ValueError(f"Field {key} must be boolean")

    return data


def _assemble_prompt(history: list[TurnRecord], schema: dict[str, Any]) -> str:
    """Build the synthesis prompt from *history* and *schema*."""
    lines: list[str] = []
    for turn in history:
        lines.append(f"- Turn {turn.seq} ({turn.profile}): {turn.content}")
    history_text = "\n".join(lines) if lines else "(없음)"
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)

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
        if turn.role != "user":
            last_turns[turn.profile] = turn

    all_agree = False
    if last_turns:
        all_agree = all(t.consensus_signal == "agree" for t in last_turns.values())

    disagreeing: list[str] = []
    if not all_agree and last_turns:
        disagreeing = [p for p, t in last_turns.items() if t.consensus_signal != "agree"]

    structured: dict[str, Any] = {
        "decision": "inconclusive",
        "confidence": 0.0,
        "reasoning": "Synthesis failed after maximum retries.",
        "consensus_reached": all_agree,
    }
    if disagreeing:
        structured["disagreeing_profiles"] = disagreeing

    return SynthesisResult(
        decision="inconclusive",
        confidence=0.0,
        reasoning="Synthesis failed after maximum retries.",
        consensus_reached=all_agree,
        disagreeing_profiles=disagreeing or None,
        structured=structured,
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
        schema: dict[str, Any],
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
        last_error: str | None = None

        for _attempt in range(3):  # initial + 2 retries
            result: BackendResult = await self.backend.invoke(profile, prompt)
            if result.code != 0:
                last_error = f"Backend error (code={result.code}): {result.error}"
                continue

            json_text = _extract_json(result.text)
            if json_text is None:
                last_error = "No JSON block found in response"
                continue

            try:
                data: dict[str, Any] = json.loads(json_text)
            except json.JSONDecodeError as exc:
                last_error = f"JSON decode error: {exc}"
                continue

            try:
                data = _validate_and_strip(data, schema)
            except ValueError as exc:
                last_error = f"Validation error: {exc}"
                continue

            # Success path
            return SynthesisResult(
                decision=data.get("decision", ""),
                confidence=data.get("confidence", 0.0),
                reasoning=data.get("reasoning", ""),
                consensus_reached=data.get("consensus_reached", False),
                disagreeing_profiles=data.get("disagreeing_profiles"),
                structured=data,
            )

        # All retries exhausted → fallback
        return _fallback_result(history)
