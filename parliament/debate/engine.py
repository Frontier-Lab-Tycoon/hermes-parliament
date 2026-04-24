"""Debate engine: turn loop, speaker selection, output parsing, termination."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import structlog

from parliament.agents.base import AgentBackend, BackendTimeoutError
from parliament.topics.config import ProtocolConfig, TopicConfig
from parliament.debate.context import ContextAssembler
from parliament.integrations.discord.registry import DiscordRegistry
from parliament.models import BackendResult, TurnRecord
from parliament.integrations.base import Publisher
from parliament.sessions.store import SessionStore
from parliament.debate.synthesis import Synthesizer

logger = structlog.get_logger()


class DebateEngine:
    """Orchestrates a multi-agent turn-based debate."""

    def __init__(
        self,
        store: SessionStore | None = None,
        publisher: Publisher | None = None,
        warmup_enabled: bool = True,
    ) -> None:
        self.store = store or SessionStore()
        self.publisher = publisher
        self.warmup_enabled = warmup_enabled

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
    def parse_output(raw_text: str) -> tuple[str, str | None, dict[str, Any] | None]:
        """Parse agent output, extracting optional consensus signals.

        Returns ``(content, consensus_signal, structured)``.
        """
        # 1. Signal block
        marker = "=== PARLIAMENT SIGNAL ==="
        if marker in raw_text:
            content, _, after = raw_text.partition(marker)
            content = content.rstrip()
            signal = after.strip().splitlines()[0].strip() if after.strip() else None
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
                    data: dict[str, Any] = json.loads(json_str)
                except json.JSONDecodeError:
                    return text, None, None
                signal = data.pop("consensus_signal", None)
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
            latest_signals: dict[str, str | None] = {}
            for turn in turns:
                if turn.role == "user":
                    continue
                latest_signals[turn.profile] = turn.consensus_signal
            if latest_signals and all(signal == "agree" for signal in latest_signals.values()):
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
        registry: DiscordRegistry | None = None,
    ) -> TurnRecord:
        """Invoke *backend* for *profile* with *prompt* and return a ``TurnRecord``."""
        logger.info("run_turn_start", profile=profile, prompt_length=len(prompt))
        try:
            result: BackendResult = await backend.invoke(profile, prompt)
            logger.info(
                "run_turn_result",
                profile=profile,
                result_length=len(result.text),
                code=result.code,
            )
        except BackendTimeoutError:
            logger.warning("run_turn_timeout", profile=profile)
            return TurnRecord(
                turn_uuid=str(uuid.uuid4()),
                seq=0,
                profile=profile,
                role="debater",
                content="[TIMEOUT] 응답 없음",
                structured=None,
                consensus_signal=None,
            )
        content, signal, structured = self.parse_output(result.text)
        logger.info(
            "run_turn_parsed",
            profile=profile,
            content_length=len(content),
            has_signal=signal is not None,
        )

        discord_user_id = None
        if registry is not None:
            try:
                discord_user_id = registry.resolve_by_hermes_profile(profile).discord_user_id
            except Exception:
                pass

        return TurnRecord(
            turn_uuid=str(uuid.uuid4()),
            seq=0,
            profile=profile,
            role="debater",
            content=content,
            structured=structured,
            consensus_signal=signal,
            discord_user_id=discord_user_id,
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
            config = TopicConfig(**cfg_dict)

        # -- Resolve ordering (participants) ------------------------------------
        ordering: list[str] = []
        session = self.store.load_session(session_id)
        session_participants = session.config.get("participants", [])

        if session_participants:
            ordering = list(session_participants)
        elif registry and registry._profiles:
            # Maintain deterministic order based on config participants if available.
            p1 = config.participant_1
            p2 = config.participant_2
            if p1 and p2:
                ordering = [p1, p2]
            else:
                ordering = [p.hermes_profile for p in registry._profiles.values()]
        else:
            if config.participant_1 and config.participant_2:
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

        # -- Warm-up: invoke each participant briefly to avoid cold-start timeout -
        if backend is not None and self.warmup_enabled:
            for speaker in ordering:
                try:
                    logger.info("warmup_start", profile=speaker, session_id=session_id)
                    await backend.invoke(
                        speaker,
                        f"'{config.topic}' 주제에 대한 토론이 곧 시작됩니다. 준비되셨나요? 짧게 '준비되었습니다'라고만 답변해주세요.",
                    )
                    logger.info("warmup_done", profile=speaker, session_id=session_id)
                except Exception as exc:
                    logger.warning("warmup_failed", profile=speaker, session_id=session_id, error=str(exc))

        # -- Turn loop ----------------------------------------------------------
        while True:
            speaker = self.determine_next_speaker(turns, ordering)
            prompt = self._build_prompt(session_id, speaker, turns, config)
            turn = await self.run_turn(speaker, prompt, backend, registry)

            # Assign deterministic seq / uuid for this turn
            turn = turn.model_copy(update={"seq": len(turns), "turn_uuid": f"t-{len(turns)}"})
            self.store.append_turn(session_id, turn)
            turns.append(turn)
            logger.info(
                "turn_recorded",
                session_id=session_id,
                turn_uuid=turn.turn_uuid,
                seq=turn.seq,
                profile=turn.profile,
                content_length=len(turn.content),
            )

            if self.publisher is not None:
                logger.info(
                    "publishing_turn",
                    session_id=session_id,
                    turn_uuid=turn.turn_uuid,
                )
                await self.publisher.send_turn(session_id, turn)

            if self.check_termination(turns, config.protocol):
                break

        # -- Synthesis ----------------------------------------------------------
        if config.synthesis.enabled:
            schema = config.synthesis.output.get("schema", {})
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
        self.store._overwrite_checkpoint(session_id, status="completed")

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
            "당신의 차례입니다. 2~3문장 내외로 핵심만 간결하게 발언하세요.",
        )
