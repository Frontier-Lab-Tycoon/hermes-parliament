"""Pydantic models for YAML topic configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from parliament.models import (
    DiscordPublishMode,
    JSONObject,
    ProtocolOrdering,
    ProtocolType,
)


class TerminationConfig(BaseModel):
    max_turns: int = 10
    min_turns: int = 2
    early_stop: bool = True

    @model_validator(mode="after")
    def validate_turns(self) -> TerminationConfig:
        if self.max_turns < self.min_turns:
            raise ValueError(
                f"max_turns ({self.max_turns}) must be >= min_turns ({self.min_turns})"
            )
        return self


class ProtocolConfig(BaseModel):
    type: ProtocolType = ProtocolType.DEBATE
    ordering: ProtocolOrdering = ProtocolOrdering.ALTERNATING
    termination: TerminationConfig = Field(default_factory=TerminationConfig)


class SynthesisConfig(BaseModel):
    enabled: bool = True
    profile: str | None = None
    prompt: str | None = None
    schema_path: str | None = None
    output: JSONObject = Field(default_factory=dict)


class DiscordConfig(BaseModel):
    coordinator_bot_token: str | None = None
    channel_id: str | None = None
    publish_mode: DiscordPublishMode = DiscordPublishMode.PER_TURN
    templates: dict[str, str] = Field(default_factory=dict)
    embed: JSONObject = Field(default_factory=dict)


class TopicConfig(BaseModel):
    version: str = "1.0"
    session: JSONObject = Field(default_factory=dict)
    protocol: ProtocolConfig = Field(default_factory=ProtocolConfig)
    synthesis: SynthesisConfig = Field(default_factory=SynthesisConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    extensions: JSONObject = Field(default_factory=dict)
    participant_1: str | None = None
    participant_2: str | None = None

    @model_validator(mode="after")
    def validate_participants(self) -> TopicConfig:
        if (
            self.participant_1 is not None
            and self.participant_2 is not None
            and self.participant_1 == self.participant_2
        ):
            raise ValueError("participant_1 and participant_2 must be different")
        return self

    @property
    def topic(self) -> str:
        topic = self.session.get("topic", "")
        return topic if isinstance(topic, str) else ""


def load_topic(path: str) -> TopicConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return TopicConfig(**data)
