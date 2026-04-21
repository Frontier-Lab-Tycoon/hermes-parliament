"""Phase 7 acceptance criteria: Context Assembly + Summarizer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parliament.config import TopicConfig
from parliament.context import ContextAssembler, Summarizer, load_soul_md
from parliament.models import TurnRecord
from parliament.session import SessionStore


@pytest.fixture
def tmp_parliament_dir(tmp_path: Path) -> Path:
    return tmp_path / ".parliament"


@pytest.fixture
def store(tmp_parliament_dir: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_parliament_dir)


class TestT7SoulPresent:
    """T7-1: SOUL.md present → included in prompt."""

    def test_soul_md_included(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        profile_dir = fake_home / ".hermes" / "profiles" / "architect-devil"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text(
            "당신은 신속한 프로토타이핑을推崇하는 실용주의 아키텍트입니다。"
        )

        ctx = ContextAssembler()
        topic = TopicConfig(session={"topic": "마이크로서비스 vs 모놀리스"})
        history: list[TurnRecord] = []
        prompt = ctx.build_prompt("architect-devil", topic, history, "반박하세요")

        assert "identity" in prompt
        assert "신속한 프로토타이핑" in prompt


class TestT7SoulAbsent:
    """T7-2: SOUL.md absent → default prompt, no crash."""

    def test_default_prompt_no_crash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        ctx = ContextAssembler()
        topic = TopicConfig(session={"topic": "마이크로서비스 vs 모놀리스"})
        history: list[TurnRecord] = []
        prompt = ctx.build_prompt("architect-devil", topic, history, "반박하세요")

        assert "identity" in prompt
        assert isinstance(prompt, str)


class TestT7TenTurns:
    """T7-3: 10 turns formatted correctly."""

    def test_ten_turns_formatted(self) -> None:
        ctx = ContextAssembler()
        topic = TopicConfig(session={"topic": "test"})
        history = [
            TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=f"p{i % 2}",
                role="debater",
                content=f"content {i}",
            )
            for i in range(10)
        ]
        prompt = ctx.build_prompt("p0", topic, history, "반박하세요")

        for i in range(10):
            assert f"content {i}" in prompt
        assert "이전 턴 내용" in prompt


class TestT7ThresholdExceeded:
    """T7-4: 70% threshold exceeded → oldest turn summarized, summary recorded in history.jsonl."""

    def test_oldest_turn_summarized(
        self,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        sid = store.create_session("topic", ["p1", "p2"], {})

        # 10 turns × ~1200 chars = ~12000 chars → ~3000 tokens.
        # 70% of 4000 = 2800. 3000 > 2800 → summarization triggered.
        history: list[TurnRecord] = []
        for i in range(10):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=f"p{i % 2}",
                role="debater",
                content=f"turn-{i}: " + "a" * 1180,
            )
            store.append_turn(sid, turn)
            history.append(turn)

        class MockSummarizer(Summarizer):
            def summarize(self, turns: list[TurnRecord]) -> str:
                return "요약 내용"

        topic = TopicConfig(session={"topic": "test"})
        ctx = ContextAssembler(
            store=store,
            session_id=sid,
            summarizer=MockSummarizer(),
            token_threshold=4000,
        )
        prompt = ctx.build_prompt("p0", topic, history, "반박하세요")

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        summary_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "summary"
        ]
        assert len(summary_events) == 1
        assert summary_events[0]["turn_uuid"] == "t-0"
        assert summary_events[0]["content"] == "요약 내용"
        assert "[요약] 요약 내용" in prompt


class TestT7SummarizerFailsTwice:
    """T7-5: summarizer fails twice → soft limit applied, no auto-drop."""

    def test_soft_limit_applied(
        self,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        sid = store.create_session("topic", ["p1", "p2"], {})

        # 12 turns × ~1200 chars = ~14400 chars → ~3600 tokens.
        # 80% of 4000 = 3200. 3600 > 3200 → retry at 80% threshold, then soft limit.
        history: list[TurnRecord] = []
        for i in range(12):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=f"p{i % 2}",
                role="debater",
                content=f"turn-{i}: " + "a" * 1180,
            )
            store.append_turn(sid, turn)
            history.append(turn)

        class FailingSummarizer(Summarizer):
            def summarize(self, turns: list[TurnRecord]) -> str:
                raise RuntimeError("summarizer failed")

        topic = TopicConfig(session={"topic": "test"})
        ctx = ContextAssembler(
            store=store,
            session_id=sid,
            summarizer=FailingSummarizer(),
            token_threshold=4000,
        )
        prompt = ctx.build_prompt("p0", topic, history, "반박하세요")

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )

        # No summary events written
        summary_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "summary"
        ]
        assert len(summary_events) == 0

        # Prompt snapshot records soft limit
        snapshot_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "prompt_snapshot"
        ]
        assert len(snapshot_events) >= 1
        assert "t-0" in snapshot_events[-1]["excluded_turn_uuids"]

        # Original turns must still be in history.jsonl (no drop)
        turn_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "turn_content"
        ]
        assert len(turn_events) == 12

        # Excluded turn content must not appear in prompt
        assert history[0].content not in prompt


class TestT7ProtectedTurn:
    """T7-6: protected turn included in threshold → next oldest turn summarized instead."""

    def test_next_oldest_turn_summarized(
        self,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        sid = store.create_session("topic", ["p1", "p2"], {})

        # First turn is the user topic turn → protected.
        turn0 = TurnRecord(
            turn_uuid="t-0",
            seq=0,
            profile="user",
            role="user",
            content="turn-0: " + "a" * 1180,
        )
        store.append_turn(sid, turn0)
        history: list[TurnRecord] = [turn0]

        for i in range(1, 11):
            turn = TurnRecord(
                turn_uuid=f"t-{i}",
                seq=i,
                profile=f"p{i % 2}",
                role="debater",
                content=f"turn-{i}: " + "a" * 1180,
            )
            store.append_turn(sid, turn)
            history.append(turn)

        class MockSummarizer(Summarizer):
            def summarize(self, turns: list[TurnRecord]) -> str:
                return "요약 내용"

        # Current participant is p1.
        # p1 turns: t-1, t-3, t-5, t-7, t-9. Most recent (protected) is t-9.
        # t-0 is also protected as the first user topic turn.
        # Therefore the oldest non-protected turn is t-1.
        topic = TopicConfig(session={"topic": "test"})
        ctx = ContextAssembler(
            store=store,
            session_id=sid,
            summarizer=MockSummarizer(),
            token_threshold=4000,
        )
        prompt = ctx.build_prompt("p1", topic, history, "반박하세요")

        lines = (
            store._history_path(sid).read_text(encoding="utf-8").strip().split("\n")
        )
        summary_events = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("type") == "summary"
        ]
        assert len(summary_events) == 1
        assert summary_events[0]["turn_uuid"] == "t-1"
        assert "[요약] 요약 내용" in prompt
