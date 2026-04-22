"""Context assembly tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parliament.debate.context import ContextAssembler, Summarizer
from parliament.models import TurnRecord
from parliament.sessions.store import SessionStore
from parliament.topics.config import TopicConfig


class MockSummarizer(Summarizer):
    def summarize(self, turns: list[TurnRecord]) -> str:
        return "요약 내용"


class TestContextAssembly:
    @pytest.fixture
    def store(self, tmp_path: Path) -> SessionStore:
        return SessionStore(base_dir=tmp_path / ".parliament")

    @pytest.fixture
    def soul_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        fake_home = tmp_path / "home"
        profile_dir = fake_home / ".hermes" / "profiles" / "architect-devil"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SOUL.md").write_text("실용주의 아키텍트", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

    @pytest.fixture
    def empty_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        return fake_home

    @pytest.fixture
    def overflow_session(self, store: SessionStore) -> tuple[str, list[TurnRecord]]:
        sid = store.create_session("topic", ["p1", "p2"], {})
        history = [
            TurnRecord(
                turn_uuid="t-0",
                seq=0,
                profile="user",
                role="user",
                content="topic: " + "a" * 1180,
            )
        ]
        store.append_turn(sid, history[0])

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

        return sid, history

    async def test_prompt_includes_identity_topic_history_and_instruction(
        self, soul_home: Path
    ) -> None:
        prompt = ContextAssembler().build_prompt(
            "architect-devil",
            TopicConfig(session={"topic": "모놀리스 vs 마이크로서비스"}),
            [
                TurnRecord(
                    turn_uuid="t-1",
                    seq=1,
                    profile="architect-angel",
                    role="debater",
                    content="운영 복잡도를 고려해야 합니다.",
                )
            ],
            "반박하세요",
        )

        assert "실용주의 아키텍트" in prompt
        assert "모놀리스 vs 마이크로서비스" in prompt
        assert "운영 복잡도를 고려해야 합니다." in prompt
        assert "반박하세요" in prompt

    async def test_overflow_summarizes_oldest_unprotected_turn(
        self,
        store: SessionStore,
        empty_home: Path,
        overflow_session: tuple[str, list[TurnRecord]],
    ) -> None:
        sid, history = overflow_session

        prompt = ContextAssembler(
            store=store,
            session_id=sid,
            summarizer=MockSummarizer(),
            token_threshold=4000,
        ).build_prompt("p1", TopicConfig(session={"topic": "test"}), history, "반박하세요")

        summary_events = [
            json.loads(line)
            for line in store._history_path(sid).read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("type") == "summary"
        ]
        assert summary_events[0]["turn_uuid"] == "t-1"
        assert "[요약] 요약 내용" in prompt
