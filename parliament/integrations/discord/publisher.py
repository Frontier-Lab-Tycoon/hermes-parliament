"""Discord HTTP API publisher with per-profile bot tokens."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog

from parliament.integrations.discord.registry import DiscordRegistry
from parliament.models import TurnRecord
from parliament.integrations.base import Publisher
from parliament.sessions.store import SessionStore

logger = structlog.get_logger()


class DiscordPublisher(Publisher):
    """Publishes turns to Discord via HTTP API using per-profile bot tokens."""

    DISCORD_API_BASE = "https://discord.com/api/v10"
    DISCORD_MSG_LIMIT = 1900

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

    @staticmethod
    def _split_discord_content(content: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
        """Split *content* into chunks that each fit within Discord's message limit."""
        if len(content) <= limit:
            return [content]
        chunks: list[str] = []
        remaining = content
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            # Prefer splitting at the last newline before the limit.
            split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at == -1:
                # Fall back to the last space.
                split_at = remaining.rfind(" ", 0, limit + 1)
            if split_at == -1:
                split_at = limit
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n ")
        return chunks

    async def _send_followup_chunks(
        self,
        url: str,
        headers: dict[str, str],
        chunks: list[str],
    ) -> None:
        """Send follow-up chunks sequentially without nonce / store tracking."""
        for chunk in chunks:
            payload = {"content": chunk}
            while True:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            url, headers=headers, json=payload
                        ) as resp:
                            if resp.status == 200:
                                break
                            if resp.status == 429:
                                retry_after = float(
                                    resp.headers.get("Retry-After", 1)
                                )
                                await asyncio.sleep(retry_after)
                                continue
                            # Other errors are non-fatal for follow-ups.
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    break

    async def send_turn(self, session_id: str, turn_record: TurnRecord) -> str | None:
        # 1. Check publish state – skip if already successfully sent
        state = self.store.get_turn_publish_state(session_id, turn_record.turn_uuid)
        if state in ("sent", "sent_via_fallback"):
            return None  # skip

        url = f"{self.DISCORD_API_BASE}/channels/{self.channel_id}/messages"
        chunks = self._split_discord_content(turn_record.content)
        logger.info(
            "send_turn",
            session_id=session_id,
            turn_uuid=turn_record.turn_uuid,
            profile=turn_record.profile,
            content_length=len(turn_record.content),
            chunks=len(chunks),
            channel_id=self.channel_id,
        )

        # 1b. Fallback pending – resume directly via coordinator
        if state == "fallback_pending":
            nonce = self.store.generate_nonce(
                session_id, turn_record.turn_uuid, "coordinator_fallback"
            )
            self.store.mark_turn_publish_in_flight(
                session_id,
                turn_record.turn_uuid,
                nonce,
                intended_publisher="coordinator_fallback",
                attempt_publisher="coordinator_fallback",
            )
            payload: dict[str, Any] = {
                "content": chunks[0],
                "nonce": nonce,
                "enforce_nonce": True,
            }
            return await self._fallback_post(url, payload, session_id, turn_record, chunks=chunks)

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
        headers: dict[str, str] = {
            "Authorization": f"Bot {profile.discord_bot_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "content": chunks[0],
            "nonce": nonce,
            "enforce_nonce": True,
        }

        # 5. POST with retry / fallback logic
        return await self._post_turn(
            url, headers, payload, session_id, turn_record, profile, chunks=chunks
        )

    async def _post_turn(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        session_id: str,
        turn_record: TurnRecord,
        profile: Any,
        chunks: list[str] | None = None,
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
                            if chunks and len(chunks) > 1:
                                await self._send_followup_chunks(
                                    url, headers, chunks[1:]
                                )
                            logger.info(
                                "send_turn_success",
                                session_id=session_id,
                                turn_uuid=turn_record.turn_uuid,
                                msg_id=msg_id,
                            )
                            return msg_id

                        if resp.status == 429:
                            retry_after = float(
                                resp.headers.get("Retry-After", 1)
                            )
                            await asyncio.sleep(retry_after)
                            continue

                        if resp.status in (403, 401):
                            body = await resp.text()
                            error = f"HTTP {resp.status}: unauthorized"
                            logger.warning(
                                "send_turn_fallback",
                                session_id=session_id,
                                turn_uuid=turn_record.turn_uuid,
                                status=resp.status,
                                body=body,
                            )
                            self.store.mark_turn_publish_fallback_pending(
                                session_id,
                                turn_record.turn_uuid,
                                error,
                                attempt_publisher=profile.discord_user_id,
                            )
                            return await self._fallback_post(
                                url, payload, session_id, turn_record, chunks=chunks
                            )

                        # Other HTTP errors – retry then terminal
                        network_attempts += 1
                        body = await resp.text()
                        logger.warning(
                            "send_turn_http_error",
                            session_id=session_id,
                            turn_uuid=turn_record.turn_uuid,
                            status=resp.status,
                            body=body,
                            attempt=network_attempts,
                        )
                        if network_attempts < max_network_attempts:
                            await asyncio.sleep(1)
                            continue

                        error = f"HTTP {resp.status}: {body[:500]}"
                        self.store.mark_turn_publish_failed(
                            session_id,
                            turn_record.turn_uuid,
                            error,
                            retryable=False,
                            attempt_publisher=profile.discord_user_id,
                        )
                        logger.error(
                            "send_turn_failed_terminal",
                            session_id=session_id,
                            turn_uuid=turn_record.turn_uuid,
                            status=resp.status,
                            body=body[:500],
                        )
                        return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                network_attempts += 1
                logger.warning(
                    "send_turn_network_error",
                    session_id=session_id,
                    turn_uuid=turn_record.turn_uuid,
                    error=str(exc),
                    attempt=network_attempts,
                )
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
                logger.error(
                    "send_turn_failed_retryable",
                    session_id=session_id,
                    turn_uuid=turn_record.turn_uuid,
                    error=str(exc),
                )
                return None

    async def _fallback_post(
        self,
        url: str,
        payload: dict[str, Any],
        session_id: str,
        turn_record: TurnRecord,
        chunks: list[str] | None = None,
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
                        if chunks and len(chunks) > 1:
                            await self._send_followup_chunks(
                                url, headers, chunks[1:]
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
        chunks = self._split_discord_content(content)

        msg_id = None
        for chunk in chunks:
            payload: dict[str, Any] = {"content": chunk}
            while True:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            url, headers=headers, json=payload
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if msg_id is None:
                                    msg_id = data.get("id")
                                break
                            if resp.status == 429:
                                retry_after = float(
                                    resp.headers.get("Retry-After", 1)
                                )
                                await asyncio.sleep(retry_after)
                                continue
                            # Non-retryable errors stop the chain.
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    break
        return msg_id
