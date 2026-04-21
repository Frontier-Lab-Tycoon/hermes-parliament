"""Discord HTTP API publisher with per-profile bot tokens."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp

from parliament.discord_registry import DiscordRegistry
from parliament.models import TurnRecord
from parliament.publishers.base import Publisher
from parliament.session import SessionStore


class DiscordPublisher(Publisher):
    """Publishes turns to Discord via HTTP API using per-profile bot tokens."""

    DISCORD_API_BASE = "https://discord.com/api/v10"

    def __init__(
        self,
        registry: DiscordRegistry,
        store: SessionStore,
        channel_id: str | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.channel_id = channel_id or registry.coordinator.get("channel_id")
        if not self.channel_id:
            raise ValueError("channel_id is required")

    async def send_turn(self, session_id: str, turn_record: TurnRecord) -> str | None:
        # 1. Check publish state – skip if already successfully sent
        state = self.store.get_turn_publish_state(session_id, turn_record.turn_uuid)
        if state in ("sent", "sent_via_fallback"):
            return None  # skip

        # 2. Resolve profile and generate deterministic nonce
        profile = self.registry.resolve_by_hermes_profile(turn_record.profile)
        nonce = self.store.generate_nonce(
            session_id, turn_record.turn_uuid, profile.discord_user_id
        )

        # 3. Mark in-flight
        self.store.mark_turn_publish_in_flight(
            session_id,
            turn_record.turn_uuid,
            nonce,
            intended_publisher=profile.discord_user_id,
            attempt_publisher=profile.discord_user_id,
        )

        # 4. Build request payload
        url = f"{self.DISCORD_API_BASE}/channels/{self.channel_id}/messages"
        headers: dict[str, str] = {
            "Authorization": f"Bot {profile.discord_bot_token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "content": turn_record.content,
            "nonce": nonce,
            "enforce_nonce": True,
        }

        # 5. POST with retry / fallback logic
        return await self._post_turn(
            url, headers, payload, session_id, turn_record, profile
        )

    async def _post_turn(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        session_id: str,
        turn_record: TurnRecord,
        profile: Any,
    ) -> str | None:
        max_network_attempts = 4
        network_attempts = 0

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, headers=headers, json=payload
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            msg_id = data["id"]
                            published_at = (
                                datetime.now(timezone.utc)
                                .isoformat()
                                .replace("+00:00", "Z")
                            )
                            self.store.mark_turn_published(
                                session_id,
                                turn_record.turn_uuid,
                                msg_id,
                                published_by=profile.discord_user_id,
                                published_at=published_at,
                                state="sent",
                                attempt_publisher=profile.discord_user_id,
                            )
                            return msg_id

                        if resp.status == 429:
                            retry_after = float(
                                resp.headers.get("Retry-After", 1)
                            )
                            await asyncio.sleep(retry_after)
                            continue

                        if resp.status in (403, 401):
                            error = f"HTTP {resp.status}: unauthorized"
                            self.store.mark_turn_publish_fallback_pending(
                                session_id,
                                turn_record.turn_uuid,
                                error,
                                attempt_publisher=profile.discord_user_id,
                            )
                            return await self._fallback_post(
                                url, payload, session_id, turn_record
                            )

                        # Other HTTP errors – retry then terminal
                        network_attempts += 1
                        if network_attempts < max_network_attempts:
                            await asyncio.sleep(1)
                            continue

                        error = f"HTTP {resp.status}"
                        self.store.mark_turn_publish_failed(
                            session_id,
                            turn_record.turn_uuid,
                            error,
                            retryable=False,
                            attempt_publisher=profile.discord_user_id,
                        )
                        return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                network_attempts += 1
                if network_attempts < max_network_attempts:
                    await asyncio.sleep(1)
                    continue

                self.store.mark_turn_publish_failed(
                    session_id,
                    turn_record.turn_uuid,
                    str(exc),
                    retryable=True,
                    attempt_publisher=profile.discord_user_id,
                )
                return None

    async def _fallback_post(
        self,
        url: str,
        payload: dict[str, Any],
        session_id: str,
        turn_record: TurnRecord,
    ) -> str | None:
        coord_token = self.registry.coordinator.get("bot_token")
        if not coord_token:
            self.store.mark_turn_publish_failed(
                session_id,
                turn_record.turn_uuid,
                "No coordinator token for fallback",
                retryable=False,
                attempt_publisher="coordinator_fallback",
            )
            return None

        headers: dict[str, str] = {
            "Authorization": f"Bot {coord_token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        msg_id = data["id"]
                        published_at = (
                            datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                        self.store.mark_turn_published(
                            session_id,
                            turn_record.turn_uuid,
                            msg_id,
                            published_by="coordinator_fallback",
                            published_at=published_at,
                            state="sent_via_fallback",
                            attempt_publisher="coordinator_fallback",
                        )
                        return msg_id
                    else:
                        error = f"Fallback HTTP {resp.status}"
                        self.store.mark_turn_publish_failed(
                            session_id,
                            turn_record.turn_uuid,
                            error,
                            retryable=False,
                            attempt_publisher="coordinator_fallback",
                        )
                        return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self.store.mark_turn_publish_failed(
                session_id,
                turn_record.turn_uuid,
                f"Fallback network error: {exc}",
                retryable=False,
                attempt_publisher="coordinator_fallback",
            )
            return None

    async def send_final(
        self, coordinator_token: str, synthesis_result: Any
    ) -> str | None:
        url = (
            f"{self.DISCORD_API_BASE}/channels/{self.channel_id}/messages"
        )
        headers: dict[str, str] = {
            "Authorization": f"Bot {coordinator_token}",
            "Content-Type": "application/json",
        }
        content = (
            synthesis_result
            if isinstance(synthesis_result, str)
            else str(synthesis_result)
        )
        payload: dict[str, Any] = {"content": content}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("id")
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
