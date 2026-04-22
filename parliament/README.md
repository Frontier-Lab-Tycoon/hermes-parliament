# parliament package

Hermes Parliament의 핵심 패키지. 토론 오케스트레이션, 지속성, 발행, 설정을 담당합니다.

## 모듈 개요

| 모듈 | 역할 |
|------|------|
| `models.py` | Pydantic v2 데이터 모델. `TurnRecord`, `DeliveryEvent`, `PublishState`, `SynthesisResult` 등 |
| `session.py` | `SessionStore`. `~/.parliament/sessions/<id>/` 아래 `history.jsonl`, `delivery.jsonl`, `checkpoint.json` 관리 |
| `index.py` | SQLite 기반 글로벌 세션 인덱스 (`~/.parliament/index.db`) |
| `engine.py` | `DebateEngine`. 턴 루프, 발언자 선정, consensus 파싱, 종료 조건 판정 |
| `context.py` | `ContextAssembler` + `Summarizer`. SOUL.md 로드, prompt 조립, 컨텍스트 윈도우 관리 |
| `synthesis.py` | `Synthesizer`. 토론 종료 후 최종 JSON 생성 및 fallback |
| `config.py` | `TopicConfig` Pydantic 모델. YAML 파싱 및 검증 |
| `discord_registry.py` | Discord User ID → Hermes profile 매핑. `${TOKEN}` 환경변수 치환 |
| `discord_bot.py` | `ParliamentBot`. `/parliament` slash command 핸들링 |
| `cli.py` | Click 기반 CLI. `parliament run-bot` 등 |
| `logging_config.py` | `structlog` + `rich` 로깅 설정 |

## 서브패키지

- `backends/` — 에이전트 백엔드 (Hermes CLI subprocess 호출)
- `publishers/` — Discord 메시지 발송 (Publisher ABC, DiscordPublisher, NoOpPublisher)
