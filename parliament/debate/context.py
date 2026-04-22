"""Context assembly, prompt building, and summarization."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from parliament.topics.config import TopicConfig
from parliament.models import TurnRecord
from parliament.sessions.store import SessionStore


def load_soul_md(profile: str) -> str | None:
    """Load SOUL.md for a Hermes profile.

    Returns ``None`` if the file does not exist. Callers should fall back to a
    default identity prompt.
    """
    path = Path.home() / ".hermes" / "profiles" / profile / "SOUL.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


class Summarizer:
    """Abstract summarizer that can condense a list of turns."""

    def summarize(self, turns: list[TurnRecord]) -> str:
        """Return a summary string of the given turns."""
        raise NotImplementedError


class ContextAssembler:
    """Assemble prompts for agent invocation and manage context-window limits."""

    def __init__(
        self,
        store: SessionStore | None = None,
        session_id: str | None = None,
        summarizer: Summarizer | None = None,
        token_threshold: int = 4000,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.summarizer = summarizer
        self.token_threshold = token_threshold
        self._excluded_turn_uuids: set[str] = set()
        self._summaries: dict[str, str] = {}

    @staticmethod
    def _estimate_tokens(turns: list[TurnRecord]) -> int:
        """Simple token heuristic: characters / 4."""
        return sum(len(t.content) // 4 for t in turns)

    def _protected_turns(self, profile: str, history: list[TurnRecord]) -> set[str]:
        """Return the set of turn_uuids that must never be summarized/excluded."""
        protected: set[str] = set()
        if history and history[0].role == "user":
            # The very first user topic turn.
            protected.add(history[0].turn_uuid)
        # The current participant's most recent previous turn.
        for turn in reversed(history):
            if turn.profile == profile:
                protected.add(turn.turn_uuid)
                break
        return protected

    def _find_oldest_summarizable(
        self, history: list[TurnRecord], protected: set[str]
    ) -> TurnRecord | None:
        for turn in history:
            if (
                turn.turn_uuid not in protected
                and turn.turn_uuid not in self._excluded_turn_uuids
                and turn.turn_uuid not in self._summaries
            ):
                return turn
        return None

    def _write_summary_event(self, turn: TurnRecord, summary: str) -> None:
        if not self.store or not self.session_id:
            return
        event: dict[str, Any] = {
            "type": "summary",
            "turn_uuid": turn.turn_uuid,
            "seq": turn.seq,
            "content": summary,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.store.append_raw(self.session_id, event)

    def _write_prompt_snapshot(self, history: list[TurnRecord]) -> None:
        if not self.store or not self.session_id:
            return
        included = [t for t in history if t.turn_uuid not in self._excluded_turn_uuids]
        window = [included[0].seq, included[-1].seq] if included else [0, 0]
        event: dict[str, Any] = {
            "type": "prompt_snapshot",
            "history_window": window,
            "excluded_turn_uuids": sorted(self._excluded_turn_uuids),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.store.append_raw(self.session_id, event)

    def _apply_soft_limit(
        self, history: list[TurnRecord], protected: set[str]
    ) -> None:
        """Exclude the oldest non-protected turn from future prompts."""
        for turn in history:
            if (
                turn.turn_uuid not in protected
                and turn.turn_uuid not in self._excluded_turn_uuids
            ):
                self._excluded_turn_uuids.add(turn.turn_uuid)
                break
        self._write_prompt_snapshot(history)

    def _format_history(self, history: list[TurnRecord]) -> str:
        lines: list[str] = []
        for turn in history:
            if turn.turn_uuid in self._excluded_turn_uuids:
                continue
            if turn.turn_uuid in self._summaries:
                display = f"[요약] {self._summaries[turn.turn_uuid]}"
            else:
                display = turn.content
            lines.append(f"- Turn {turn.seq} ({turn.profile}): {display}")
        if not lines:
            return "(없음)"
        return "\n".join(lines)

    def build_prompt(
        self,
        profile: str,
        topic: TopicConfig,
        history: list[TurnRecord],
        turn_instruction: str,
    ) -> str:
        """Assemble the final prompt string for *profile*."""
        history_tokens = self._estimate_tokens(history)
        threshold_70 = int(self.token_threshold * 0.7)
        threshold_80 = int(self.token_threshold * 0.8)

        if (
            history_tokens > threshold_70
            and self.summarizer
            and self.store
            and self.session_id
        ):
            protected = self._protected_turns(profile, history)
            candidate = self._find_oldest_summarizable(history, protected)

            if candidate:
                success = False
                try:
                    summary = self.summarizer.summarize([candidate])
                    self._write_summary_event(candidate, summary)
                    self._summaries[candidate.turn_uuid] = summary
                    success = True
                except Exception:
                    pass

                if not success and history_tokens > threshold_80:
                    try:
                        summary = self.summarizer.summarize([candidate])
                        self._write_summary_event(candidate, summary)
                        self._summaries[candidate.turn_uuid] = summary
                        success = True
                    except Exception:
                        pass

                    if not success:
                        self._apply_soft_limit(history, protected)

        soul_md = load_soul_md(profile)
        identity_text = (
            soul_md
            if soul_md is not None
            else f'당신은 "{profile}"입니다. 기본 에이전트 identity입니다.'
        )

        history_md = self._format_history(history)

        prompt = f"""# identity
당신은 "{profile}"입니다.
{identity_text}

# session context
주제: {topic.topic}

# 이전 턴 내용
{history_md}

# your turn
{turn_instruction}
"""
        return prompt
