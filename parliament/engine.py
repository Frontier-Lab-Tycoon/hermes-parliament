"""Debate turn-loop engine."""

from __future__ import annotations

import json
import uuid

from parliament.backends.base import AgentBackend
from parliament.config import ProtocolConfig, TopicConfig
from parliament.context import ContextAssembler
from parliament.discord_registry import DiscordRegistry
from parliament.models import BackendResult, TurnRecord
from parliament.publishers.base import Publisher
from parliament.session import SessionStore


class DebateEngine:
    """Orchestrates the turn-based debate loop."""

    def __init__(self, store: SessionStore, publisher: Publisher) -> None:
        self.store = store
        self.publisher = publisher

    @staticmethod
    def determine_next_speaker(turns: list[TurnRecord], ordering: list[str]) -> str:
        """Return the next speaker in alternating order."""
        if not ordering:
            raise ValueError("ordering must not be empty")
        if not turns:
            return ordering[0]
        last_profile = turns[-1].profile
        try:
            idx = ordering.index(last_profile)
        except ValueError:
            return ordering[0]
        return ordering[(idx + 1) % len(ordering)]

    @staticmethod
    def parse_output(raw_text: str) -> tuple[str, str | None, dict | None]:
        """Extract content, consensus signal, and structured data from raw text.

        Looks for a ``=== PARLIAMENT SIGNAL ===`` tail block or a JSON tail
        block containing ``consensus_signal``.
        """
        text = raw_text.strip()

        # 1. Signal marker block
        marker = "=== PARLIAMENT SIGNAL ==="
        if marker in text:
            parts = text.split(marker, 1)
            content = parts[0].strip()
            after = parts[1].strip()
            consensus_signal = after.splitlines()[0].strip() if after else None
            return content, consensus_signal, None

        # 2. JSON tail block containing consensus_signal
        # Scan for '{' that starts a valid JSON object extending to the end.
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            suffix = text[i:]
            try:
                obj = json.loads(suffix)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "consensus_signal" in obj:
                content = text[:i].strip()
                consensus_signal = obj["consensus_signal"]
                structured = {k: v for k, v in obj.items() if k != "consensus_signal"} or None
                return content, consensus_signal, structured

        return text, None, None

    @staticmethod
    def check_termination(turns: list[TurnRecord], config: ProtocolConfig) -> bool:
        """Return ``True`` if the debate should stop."""
        if len(turns) >= config.termination.max_turns:
            return True
        if len(turns) >= config.termination.min_turns:
            latest_signals: dict[str, str | None] = {}
            for turn in turns:
                latest_signals[turn.profile] = turn.consensus_signal
            return all(signal == "agree" for signal in latest_signals.values())
        return False

    async def run_turn(self, profile: str, prompt: str, backend: AgentBackend) -> TurnRecord:
        """Invoke *backend* for *profile* and return a parsed ``TurnRecord``."""
        result: BackendResult = await backend.invoke(profile, prompt)
        content, consensus_signal, structured = self.parse_output(result.text)

        return TurnRecord(
            turn_uuid=str(uuid.uuid4()),
            seq=0,
            profile=profile,
            role="debater",
            content=content,
            structured=structured,
            consensus_signal=consensus_signal,
        )

    async def run(
        self,
        session_id: str,
        config: TopicConfig,
        registry: DiscordRegistry,
        backend: AgentBackend,
    ) -> None:
        """Run the full debate loop until termination."""
        ordering: list[str] = []
        if config.participant_1:
            ordering.append(config.participant_1)
        if config.participant_2:
            ordering.append(config.participant_2)

        ctx = ContextAssembler(store=self.store, session_id=session_id)

        while True:
            session = self.store.load_session(session_id)
            turns = session.turns

            next_profile = self.determine_next_speaker(turns, ordering)

            prompt = ctx.build_prompt(
                next_profile,
                config,
                turns,
                turn_instruction="주제에 대해 의견을 제시하세요.",
            )

            turn = await self.run_turn(next_profile, prompt, backend)
            turn = turn.model_copy(update={"seq": len(turns)})

            self.store.append_turn(session_id, turn)

            if self.check_termination(self.store.load_session(session_id).turns, config.protocol):
                break
