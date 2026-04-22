"""Debate engine: turn loop, speaker selection, output parsing, termination."""

from __future__ import annotations

import uuid

from parliament.agents.base import AgentBackend, BackendTimeoutError
from parliament.debate.context import ContextAssembler
from parliament.debate.synthesis import Synthesizer
from parliament.integrations.base import Publisher
from parliament.integrations.discord.registry import DiscordRegistry
from parliament.json_codec import loads_json
from parliament.models import (
    BackendResult,
    ConsensusSignal,
    JSONObject,
    JSONValue,
    SessionStatus,
    TurnRecord,
    TurnRole,
)
from parliament.sessions.store import SessionStore
from parliament.topics.config import ProtocolConfig, TopicConfig


class DebateEngine:
    """Orchestrates a multi-agent turn-based debate."""

    def __init__(
        self,
        store: SessionStore | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self.store = store or SessionStore()
        self.publisher = publisher

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def determine_next_speaker(turns: list[TurnRecord], ordering: list[str]) -> str:
        """Return the next speaker based on *ordering* and number of completed turns."""
        if not ordering:
            raise ValueError("ordering must not be empty")
        return ordering[len(turns) % len(ordering)]

    @staticmethod
    def parse_output(raw_text: str) -> tuple[str, ConsensusSignal | None, JSONObject | None]:
        """Parse agent output, extracting optional consensus signals.

        Returns ``(content, consensus_signal, structured)``.
        """
        # 1. Signal block
        marker = "=== PARLIAMENT SIGNAL ==="
        if marker in raw_text:
            content, _, after = raw_text.partition(marker)
            content = content.rstrip()
            raw_signal = after.strip().splitlines()[0].strip() if after.strip() else None
            signal = ConsensusSignal.parse(raw_signal)
            return content, signal, None

        # 2. JSON tail
        text = raw_text.rstrip()
        start = text.rfind("{")
        if start != -1:
            brace_count = 0
            end = -1
            for i in range(start, len(text)):
                ch = text[i]
                if ch == "{":
                    brace_count += 1
                elif ch == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end = i
                        break
            if end != -1:
                json_str = text[start : end + 1]
                try:
                    parsed: JSONValue = loads_json(json_str)
                except ValueError:
                    return text, None, None
                if not isinstance(parsed, dict):
                    return text, None, None
                data = parsed
                signal = ConsensusSignal.parse(data.pop("consensus_signal", None))
                structured = data if data else None
                content = text[:start].rstrip()
                return content, signal, structured

        return text, None, None

    @staticmethod
    def check_termination(turns: list[TurnRecord], config: ProtocolConfig) -> bool:
        """Return ``True`` if the debate should stop."""
        if len(turns) >= config.termination.max_turns:
            return True

        if config.termination.early_stop and len(turns) >= config.termination.min_turns:
            latest_signals: dict[str, ConsensusSignal | None] = {}
            for turn in turns:
                if turn.role == TurnRole.USER:
                    continue
                latest_signals[turn.profile] = turn.consensus_signal
            if latest_signals and all(
                signal == ConsensusSignal.AGREE for signal in latest_signals.values()
            ):
                return True

        return False

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def run_turn(
        self,
        profile: str,
        prompt: str,
        backend: AgentBackend,
    ) -> TurnRecord:
        """Invoke *backend* for *profile* with *prompt* and return a ``TurnRecord``."""
        try:
            result: BackendResult = await backend.invoke(profile, prompt)
        except BackendTimeoutError:
            return TurnRecord(
                turn_uuid=str(uuid.uuid4()),
                seq=0,
                profile=profile,
                role=TurnRole.DEBATER,
                content="[TIMEOUT] 응답 없음",
                structured=None,
                consensus_signal=None,
            )
        content, signal, structured = self.parse_output(result.text)
        return TurnRecord(
            turn_uuid=str(uuid.uuid4()),
            seq=0,
            profile=profile,
            role=TurnRole.DEBATER,
            content=content,
            structured=structured,
            consensus_signal=signal,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(
        self,
        session_id: str,
        config: TopicConfig | None = None,
        registry: DiscordRegistry | None = None,
        backend: AgentBackend | None = None,
    ) -> None:
        """Run the debate loop for *session_id*.

        If *config*, *registry*, or *backend* are not provided, the engine
        attempts to load defaults from the session store or system paths.
        """
        # -- Load session config if not provided --------------------------------
        if config is None:
            session = self.store.load_session(session_id)
            cfg_dict = dict(session.config)
            cfg_dict.pop("topic", None)
            cfg_dict.pop("participants", None)
            config = TopicConfig.model_validate(cfg_dict)

        # -- Resolve ordering (participants) ------------------------------------
        ordering: list[str] = []
        if registry and registry._profiles:
            # Maintain deterministic order based on config participants if available
            p1 = config.participant_1
            p2 = config.participant_2
            if p1 and p2:
                ordering = [p1, p2]
            else:
                ordering = [p.hermes_profile for p in registry._profiles.values()]
        else:
            session = self.store.load_session(session_id)
            participants = session.config.get("participants", [])
            ordering = (
                [participant for participant in participants if isinstance(participant, str)]
                if isinstance(participants, list)
                else []
            )
            if not ordering and config.participant_1 and config.participant_2:
                ordering = [config.participant_1, config.participant_2]

        if not ordering:
            raise ValueError("No participants found for the session")

        # -- Backend ------------------------------------------------------------
        if backend is None:
            from parliament.agents.hermes import HermesBackend

            backend = HermesBackend()

        # -- Replay existing turns (resume support) -----------------------------
        session = self.store.load_session(session_id)
        turns: list[TurnRecord] = list(session.turns)

        # -- Resume: publish any previously unpublished turns ---------------------
        if self.publisher is not None:
            for turn in self.store.get_unpublished_turns(session_id):
                await self.publisher.send_turn(session_id, turn)

        # -- Turn loop ----------------------------------------------------------
        while True:
            speaker = self.determine_next_speaker(turns, ordering)
            prompt = self._build_prompt(session_id, speaker, turns, config)
            turn = await self.run_turn(speaker, prompt, backend)

            # Assign deterministic seq / uuid for this turn
            turn = turn.model_copy(update={"seq": len(turns), "turn_uuid": f"t-{len(turns)}"})
            self.store.append_turn(session_id, turn)
            turns.append(turn)

            if self.publisher is not None:
                await self.publisher.send_turn(session_id, turn)

            if self.check_termination(turns, config.protocol):
                break

        # -- Synthesis ----------------------------------------------------------
        if config.synthesis.enabled:
            schema_value = config.synthesis.output.get("schema", {})
            schema: JSONObject = schema_value if isinstance(schema_value, dict) else {}
            synth = Synthesizer(backend)
            try:
                result = await synth.run(
                    session_id,
                    config.synthesis.profile,
                    turns,
                    schema,
                )
            except Exception:
                result = None

            if result is not None and self.publisher is not None:
                coord_token = ""
                if registry and registry.coordinator:
                    coord_token = registry.coordinator.get("bot_token") or ""
                await self.publisher.send_final(coord_token, result)

        # -- Finalise checkpoint ------------------------------------------------
        self.store._overwrite_checkpoint(session_id, status=SessionStatus.COMPLETED)

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        session_id: str,
        profile: str,
        turns: list[TurnRecord],
        config: TopicConfig,
    ) -> str:
        """Assemble the prompt for *profile*."""
        ctx = ContextAssembler(store=self.store, session_id=session_id)
        return ctx.build_prompt(
            profile,
            config,
            list(turns),
            "당신의 차례입니다. 자유롭게 발언하세요.",
        )
