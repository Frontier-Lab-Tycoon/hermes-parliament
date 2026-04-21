"""Phase 5 acceptance criteria: Discord Publisher + Publish State."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aioresponses import aioresponses

from parliament.discord_registry import DiscordRegistry, HermesProfile
from parliament.models import TurnRecord
from parliament.publishers.discord import DiscordPublisher
from parliament.session import SessionStore


@pytest.fixture
def tmp_parliament_dir(tmp_path: Path) -> Path:
    return tmp_path / ".parliament"


@pytest.fixture
def store(tmp_parliament_dir: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_parliament_dir)


@pytest.fixture
def registry() -> DiscordRegistry:
    profiles = {
        "123456789": HermesProfile(
            hermes_profile="architect-devil",
            discord_bot_token="devil-token-123",
            discord_user_id="123456789",
            discord_name="Test Bot",
        )
    }
    coordinator = {
        "bot_token": "coordinator-token-456",
        "channel_id": "999999999",
    }
    return DiscordRegistry(profiles=profiles, coordinator=coordinator)


@pytest.fixture
def publisher(registry: DiscordRegistry, store: SessionStore) -> DiscordPublisher:
    return DiscordPublisher(registry, store)


@pytest.fixture
def session_id(store: SessionStore) -> str:
    return store.create_session("topic", ["p1", "p2"], {})


@pytest.fixture
def turn_record() -> TurnRecord:
    return TurnRecord(
        turn_uuid="turn-1",
        seq=0,
        profile="architect-devil",
        role="user",
        content="Hello world",
    )


DISCORD_API_URL = "https://discord.com/api/v10/channels/999999999/messages"


class TestT5ValidToken:
    """T5-1: valid token → 200, message_id, sent state."""

    async def test_send_turn_success(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                status=200,
                payload={"id": "msg-123"},
            )
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id == "msg-123"
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "sent"
        )


class TestT5UnauthorizedFallback:
    """T5-2: 403/401 → fallback, sent_via_fallback."""

    async def test_403_fallback_to_coordinator(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                status=403,
            )
            m.post(
                DISCORD_API_URL,
                status=200,
                payload={"id": "msg-fallback"},
            )
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id == "msg-fallback"
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "sent_via_fallback"
        )

    async def test_401_fallback_to_coordinator(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                status=401,
            )
            m.post(
                DISCORD_API_URL,
                status=200,
                payload={"id": "msg-fallback-401"},
            )
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id == "msg-fallback-401"
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "sent_via_fallback"
        )


class TestT5NetworkTimeout:
    """T5-3: network timeout → 3 retries, failed_retryable."""

    async def test_network_timeout_retries_then_failed_retryable(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                exception=asyncio.TimeoutError(),
                repeat=4,
            )
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id is None
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "failed_retryable"
        )


class TestT5RateLimit:
    """T5-4: 429 → retry-after wait, then success."""

    async def test_429_wait_then_success(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store.append_turn(session_id, turn_record)
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                status=429,
                headers={"Retry-After": "0.5"},
            )
            m.post(
                DISCORD_API_URL,
                status=200,
                payload={"id": "msg-429-ok"},
            )
            msg_id = await publisher.send_turn(session_id, turn_record)

        assert msg_id == "msg-429-ok"
        assert sleep_calls == [0.5]
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "sent"
        )


class TestT5AlreadySent:
    """T5-5: already sent → skip."""

    async def test_already_sent_skips(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        store.mark_turn_published(
            session_id,
            turn_record.turn_uuid,
            "msg-old",
            "participant_bot",
            "2026-04-21T12:00:00Z",
            state="sent",
            attempt_publisher="participant_bot",
        )
        with aioresponses() as m:
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id is None


class TestT5AlreadySentViaFallback:
    """T5-6: already sent_via_fallback → skip."""

    async def test_already_sent_via_fallback_skips(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        store.mark_turn_published(
            session_id,
            turn_record.turn_uuid,
            "msg-old",
            "coordinator_fallback",
            "2026-04-21T12:00:00Z",
            state="sent_via_fallback",
            attempt_publisher="coordinator_fallback",
        )
        with aioresponses() as m:
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id is None


class TestT5FailedRetryableRetry:
    """T5-7: failed_retryable → retry, success → sent."""

    async def test_failed_retryable_retries_and_succeeds(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        store.mark_turn_publish_failed(
            session_id,
            turn_record.turn_uuid,
            "previous error",
            retryable=True,
            attempt_publisher="bot",
        )
        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                status=200,
                payload={"id": "msg-retry"},
            )
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id == "msg-retry"
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "sent"
        )


class TestT5Nonce:
    """Acceptance criteria: nonce deterministic and ≤ 25 chars."""

    async def test_nonce_length_and_state(
        self,
        publisher: DiscordPublisher,
        store: SessionStore,
        session_id: str,
        turn_record: TurnRecord,
    ) -> None:
        store.append_turn(session_id, turn_record)
        nonce = store.generate_nonce(
            session_id, turn_record.turn_uuid, "participant_bot"
        )
        assert len(nonce) <= 25

        with aioresponses() as m:
            m.post(
                DISCORD_API_URL,
                status=200,
                payload={"id": "msg-abc"},
            )
            msg_id = await publisher.send_turn(session_id, turn_record)
        assert msg_id is not None
        assert (
            store.get_turn_publish_state(session_id, turn_record.turn_uuid)
            == "sent"
        )
