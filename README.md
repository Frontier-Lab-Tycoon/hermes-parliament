# Hermes Parliament

Discord 기반 다중 AI 에이전트 토론 오케스트레이터. 두 명의 Hermes 프로필이 주어진 주제에 대해 교대로 발언하고, 합의에 도달하면 최종 결론을 JSON으로 생성합니다.

## 내 서버에서 바로 쓰기

Parliament는 원격 Hermes 서버를 호출하지 않고, 실행 중인 서버의 `hermes` CLI를 직접 실행합니다. 따라서 사용자는 **Hermes agent가 설치되어 있고 `~/.hermes/profiles/<profile>/SOUL.md`가 있는 같은 서버/계정**에서 Parliament를 켜면 됩니다.

운영 서버에서는 한 번 설치한 뒤 `parliament start`만 계속 실행하는 흐름을 권장합니다.

```bash
# 한 번만 설치합니다. 배포 방식에 맞게 pip install . 을 써도 됩니다.
uv tool install .

# Hermes profile이 이 서버에서 동작하는지 확인합니다.
hermes -p architect-devil chat -q "짧게 자기소개해줘"
```

## Discord 사전 준비

Discord에서 `/parliament` 같은 custom slash command를 쓰려면 반드시 Discord Application이 필요합니다. 이 프로젝트에서는 그 Application에 붙은 Bot을 **Parliament Bot**이라고 부릅니다. Parliament Bot은 서버 프로세스와 Discord를 이어주는 입구이며, `/parliament` 명령을 받고 토론을 시작합니다.

각 캐릭터가 실제로 다른 봇처럼 말하는 UX를 위해 참가자별 Bot도 필요합니다. 따라서 기본 구성은 Discord Developer Portal에서 아래 3개를 만드는 것입니다.

| Discord Application/Bot | 역할 |
|---|---|
| Parliament Bot | `/parliament` slash command 수신, 토론 시작, fallback/최종 결론 발송 |
| Character Bot 1 | 첫 번째 Hermes profile의 발언자로 메시지 발송 |
| Character Bot 2 | 두 번째 Hermes profile의 발언자로 메시지 발송 |

세 Bot을 모두 같은 Discord 서버에 초대해야 합니다. Discord Application/Bot 생성과 서버 초대는 Discord 권한 작업이라 Parliament가 대신 만들 수 없습니다.

토큰은 환경 변수로 넘겨도 되고, `~/.parliament/bots.yaml`에 직접 적어도 됩니다. 환경 변수는 토큰을 설정 파일에 남기지 않기 위한 권장 방식일 뿐, slash command 사용을 위한 필수 입력은 아닙니다.

```yaml
# ~/.parliament/bots.yaml
profiles:
  architect-devil:
    hermes_profile: "architect-devil"
    discord_bot_token: "${DEVIL_BOT_TOKEN}"
    discord_user_id: "123456789012345678"
  architect-angel:
    hermes_profile: "architect-angel"
    discord_bot_token: "${ANGEL_BOT_TOKEN}"
    discord_user_id: "234567890123456789"

parliament_application:
  bot_token: "${PARLIAMENT_BOT_TOKEN}"
```

`bots.yaml`을 직접 만들었다면 실행할 때는 Parliament Bot token만 있으면 됩니다.

```bash
export PARLIAMENT_BOT_TOKEN="..."

parliament start
```

처음 설정을 자동 생성하고 싶다면 Character Bot token도 한 번만 알려주면 됩니다. `parliament start`가 각 Character Bot의 Discord user ID를 조회해서 `bots.yaml`을 만듭니다.

```bash
export PARLIAMENT_BOT_TOKEN="..."
export DEVIL_BOT_TOKEN="..."
export ANGEL_BOT_TOKEN="..."
export PARLIAMENT_AGENTS="architect-devil=DEVIL_BOT_TOKEN,architect-angel=ANGEL_BOT_TOKEN"

parliament start
```

이미 bot config가 있으면 `PARLIAMENT_AGENTS`, `DEVIL_BOT_TOKEN`, `ANGEL_BOT_TOKEN`은 필요하지 않습니다.

Discord에서는 Parliament Bot과 Character Bot들을 초대한 뒤 바로 호출합니다.

```text
/parliament topic:"초기 아키텍처는 모놀리스가 좋은가?" p1:@ArchitectDevil p2:@ArchitectAngel turns:10
```

`p1`, `p2`는 Discord의 Character Bot 멘션입니다. Parliament Bot은 멘션된 봇의 Discord user ID를 `bots.yaml`에서 찾고, 그 항목의 `hermes_profile`을 로컬 Hermes 호출에 사용합니다. 각 턴은 해당 Character Bot token으로 발송되어 캐릭터 봇이 직접 말한 것처럼 보입니다.

Parliament는 토론자의 성격을 규정하지 않습니다. 캐릭터의 말투와 관점은 Hermes profile의 `SOUL.md` 등 Hermes agent 설정이 담당하고, Parliament는 선택된 봇을 어떤 Hermes profile로 호출할지만 매핑합니다.

토론 중에는 각 턴마다 로컬에서 다음 형태로 Hermes agent가 호출됩니다.

```bash
hermes -p <hermes_profile> chat -q <assembled_prompt>
```

세션 로그와 체크포인트는 기본적으로 `~/.parliament/` 아래에 저장됩니다. 개발 중 저장소에서 직접 실행할 때만 `uv run parliament start`를 사용하면 됩니다.

## 동작 원리

```
사용자가 /parliament slash command 호출
         │
         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Discord Bot    │────▶│  DebateEngine   │────▶│ HermesBackend   │
│  (Parliament)   │     │  (Turn Loop)    │     │  (subprocess)   │
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

1. **Parliament Bot**의 Discord Slash Command (`/parliament`)로 토론을 시작합니다.
2. **DebateEngine**이 교대 발언자를 선정하고, **ContextAssembler**가 프롬프트를 조립합니다.
3. **HermesBackend**가 `hermes` CLI를 subprocess로 실행해 응답을 받습니다.
4. 각 턴은 **SessionStore**에 append-only로 기록되고, **DiscordPublisher**가 캐릭터별 Bot token으로 Discord 채널에 발송합니다.
5. `max_turns` 도달 또는 모든 참가자가 `consensus_signal: agree`를 본면 토론이 종료됩니다.
6. **Synthesizer**가 전체 히스토리를 요약해 최종 JSON을 생성합니다.

## 핵심 설계

- **Event Sourcing**: 세션 상태는 `history.jsonl`(turn content)과 `delivery.jsonl`(publish 상태) 두 개의 append-only 로그로 관리됩니다.
- **Crash Recovery**: `checkpoint.json`으로 빠른 복구 지점을 제공하며, crash 후에는 `delivery.jsonl`을 replay해 상태를 복원합니다.
- **Exactly-once Publish**: Discord `nonce` + `enforce_nonce`로 중복 발송을 방지하고, durable marker(`session_id` + `turn_uuid`)로 reconciliation을 지원합니다.
- **Fallback**: 참가자 봇 발송 실패(403/401) 시 Parliament Bot이 메시지를 대신 발송합니다.

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
# 개발 환경 의존성 설치
uv sync

# 테스트
uv run pytest tests/ -v

# CLI
uv run parliament --help
uv run parliament start
```

## 설정

- `~/.parliament/bots.yaml` — Parliament Bot과 Character Bot 매핑 설정
- `~/.hermes/profiles/<name>/SOUL.md` — Hermes가 관리하는 각 에이전트의 정체성 파일
- `topics/*.yaml` — 토론 주제 설정
