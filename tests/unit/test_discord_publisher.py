"""Discord publisher tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from aioresponses import aioresponses

from parliament.integrations.discord.publisher import DiscordPublisher
from parliament.integrations.discord.registry import DiscordRegistry, HermesProfile
from parliament.models import PublishState, TurnRecord, TurnRole
from parliament.sessions.store import SessionStore

DISCORD_API_URL = "https://discord.com/api/v10/channels/999999999/messages"


class TestDiscordPublisher:
    @pytest.fixture
    def store(self, tmp_path: Path) -> SessionStore:
        return SessionStore(base_dir=tmp_path / ".parliament")

    @pytest.fixture
    def registry(self) -> DiscordRegistry:
        return DiscordRegistry(
            profiles={
                "123456789": HermesProfile(
                    hermes_profile="architect-devil",
                    discord_bot_token="devil-token-123",
                    discord_user_id="123456789",
                    discord_name="Test Bot",
                )
            },
            coordinator={
                "bot_token": "coordinator-token-456",
                "channel_id": "999999999",
            },
        )

    @pytest.fixture
    def publisher(self, registry: DiscordRegistry, store: SessionStore) -> DiscordPublisher:
        return DiscordPublisher(registry, store)

    @pytest.fixture
    def session_id(self, store: SessionStore) -> str:
        return store.create_session("topic", ["architect-devil"], {})

    @pytest.fixture
    def turn_record(self) -> TurnRecord:
        return TurnRecord(
            turn_uuid="turn-1",
            seq=0,
            profile="architect-devil",
            role=TurnRole.DEBATER,
            content="Hello world",
        )

    @pytest.fixture
    def persisted_turn(
        self, store: SessionStore, session_id: str, turn_record: TurnRecord
    ) -> TurnRecord:
        store.append_turn(session_id, turn_record)
        return turn_record

    @pytest.fixture
    def discord_api(self) -> Iterator[aioresponses]:
        with aioresponses() as mocked_api:
            yield mocked_api

    @pytest.fixture
    def successful_post(self, discord_api: aioresponses) -> None:
        discord_api.post(DISCORD_API_URL, status=200, payload={"id": "msg-123"})

    @pytest.fixture
    def fallback_post(self, discord_api: aioresponses) -> None:
        discord_api.post(DISCORD_API_URL, status=403)
        discord_api.post(DISCORD_API_URL, status=200, payload={"id": "msg-fallback"})

    @pytest.fixture
    def timeout_post(self, discord_api: aioresponses) -> None:
        discord_api.post(DISCORD_API_URL, exception=TimeoutError(), repeat=4)

    @pytest.fixture
    def rate_limited_post(
        self, discord_api: aioresponses, monkeypatch: pytest.MonkeyPatch
    ) -> list[float]:
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        discord_api.post(DISCORD_API_URL, status=429, headers={"Retry-After": "0.5"})
        discord_api.post(DISCORD_API_URL, status=200, payload={"id": "msg-429-ok"})
        return sleep_calls

    @pytest.fixture
    def already_sent(
        self, store: SessionStore, session_id: str, turn_record: TurnRecord
    ) -> TurnRecord:
        store.append_turn(session_id, turn_record)
        store.mark_turn_published(
            session_id,
            turn_record.turn_uuid,
            "msg-old",
            "participant_bot",
            "2026-04-21T12:00:00Z",
            state=PublishState.SENT,
            attempt_publisher="participant_bot",
        )
        return turn_record

    async def test_send_turn_marks_turn_sent(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        persisted_turn: TurnRecord,
        successful_post: None,
    ) -> None:
        msg_id = await publisher.send_turn(session_id, persisted_turn)

        assert msg_id == "msg-123"
        assert (
            store.get_turn_publish_state(session_id, persisted_turn.turn_uuid) == PublishState.SENT
        )

    async def test_unauthorized_participant_publish_falls_back_to_coordinator(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        persisted_turn: TurnRecord,
        fallback_post: None,
    ) -> None:
        msg_id = await publisher.send_turn(session_id, persisted_turn)

        assert msg_id == "msg-fallback"
        assert (
            store.get_turn_publish_state(session_id, persisted_turn.turn_uuid)
            == PublishState.SENT_VIA_FALLBACK
        )

    async def test_network_timeout_is_retryable_failure(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        persisted_turn: TurnRecord,
        timeout_post: None,
    ) -> None:
        msg_id = await publisher.send_turn(session_id, persisted_turn)

        assert msg_id is None
        assert (
            store.get_turn_publish_state(session_id, persisted_turn.turn_uuid)
            == PublishState.FAILED_RETRYABLE
        )

    async def test_rate_limit_retries_and_succeeds(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        persisted_turn: TurnRecord,
        rate_limited_post: list[float],
    ) -> None:
        msg_id = await publisher.send_turn(session_id, persisted_turn)

        assert msg_id == "msg-429-ok"
        assert rate_limited_post == [0.5]
        assert (
            store.get_turn_publish_state(session_id, persisted_turn.turn_uuid) == PublishState.SENT
        )

    async def test_already_sent_turn_is_not_republished(
        self: TestDiscordPublisher,
        publisher: DiscordPublisher,
        session_id: str,
        already_sent: TurnRecord,
        discord_api: aioresponses,
    ) -> None:
        msg_id = await publisher.send_turn(session_id, already_sent)

        assert msg_id is None
