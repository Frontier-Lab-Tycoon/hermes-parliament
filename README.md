# Hermes Parliament

Discord 기반 다중 AI 에이전트 토론 오케스트레이터. 두 명의 Hermes 프로필이 주어진 주제에 대해 교대로 발언하고, 합의에 도달하면 최종 결론을 JSON으로 생성합니다.

## 동작 원리

```
사용자가 /parliament slash command 호출
         │
         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Discord Bot    │────▶│  DebateEngine   │────▶│ HermesBackend   │
│  (Coordinator)  │     │  (Turn Loop)    │     │  (subprocess)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                       │                       │
         │                       ▼                       │
         │              ┌─────────────────┐              │
         │              │  SessionStore   │              │
         │              │  (append-only)  │              │
         │              └─────────────────┘              │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ DiscordPublisher│◀────│  TurnRecord     │◀────│  Agent response │
│  (HTTP API)     │     │  (history.jsonl)│     │  (raw text)     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │
         ▼
   Discord Channel
         │
         ▼
┌─────────────────┐
│  Synthesizer    │
│  (final JSON)   │
└─────────────────┘
```

1. **Discord Slash Command** (`/parliament`)로 토론을 시작합니다.
2. **DebateEngine**이 교대 발언자를 선정하고, **ContextAssembler**가 프롬프트를 조립합니다.
3. **HermesBackend**가 `hermes` CLI를 subprocess로 실행해 응답을 받습니다.
4. 각 턴은 **SessionStore**에 append-only로 기록되고, **DiscordPublisher**가 Discord 채널에 발송합니다.
5. `max_turns` 도달 또는 모든 참가자가 `consensus_signal: agree`를 본면 토론이 종료됩니다.
6. **Synthesizer**가 전체 히스토리를 요약해 최종 JSON을 생성합니다.

## 핵심 설계

- **Event Sourcing**: 세션 상태는 `history.jsonl`(turn content)과 `delivery.jsonl`(publish 상태) 두 개의 append-only 로그로 관리됩니다.
- **Crash Recovery**: `checkpoint.json`으로 빠른 복구 지점을 제공하며, crash 후에는 `delivery.jsonl`을 replay해 상태를 복원합니다.
- **Exactly-once Publish**: Discord `nonce` + `enforce_nonce`로 중복 발송을 방지하고, durable marker(`session_id` + `turn_uuid`)로 reconciliation을 지원합니다.
- **Fallback**: 참가자 봇 발송 실패(403/401) 시 Coordinator Bot이 메시지를 대신 발송합니다.

## 프로젝트 구조

```
parliament/
├── agents/             # AgentBackend 인터페이스와 Hermes CLI 구현체
├── debate/             # DebateEngine, ContextAssembler, Synthesizer
├── integrations/       # Publisher 인터페이스와 Discord 연동 구현체
├── sessions/           # SessionStore, append-only logs, SQLite index
├── topics/             # YAML TopicConfig 파싱 및 검증
├── models.py           # 공용 Pydantic 모델
├── cli.py              # Click CLI
└── logging_config.py   # structlog + rich 설정

tests/
├── unit/               # 단위 테스트
├── integration/        # 통합 테스트 (Session→Engine→Publisher)
└── e2e/                # E2E 테스트

topics/                 # 토론 주제 YAML 설정
```

## 빠른 시작

```bash
# 의존성 설치
uv sync

# 테스트
uv run pytest tests/ -v

# CLI
uv run parliament --help
uv run parliament run-bot
```

## 설정

- `~/.parliament/discord-registry.yaml` — Discord 봇 레지스트리
- `~/.hermes/profiles/<name>/SOUL.md` — 각 에이전트의 정체성 파일
- `topics/*.yaml` — 토론 주제 설정
