# parliament package

Hermes Parliament의 핵심 패키지. 토론 오케스트레이션, 지속성, 발행, 설정을 담당합니다.

## 모듈 개요

| 모듈 | 역할 |
|------|------|
| `models/` | 도메인별 데이터 모델과 enum. `TurnRecord`, `DeliveryEvent`, `PublishState`, `SynthesisResult` 등 |
| `cli.py` | Click 기반 CLI. `parliament run-bot` 등 |
| `logging_config.py` | `structlog` + `rich` 로깅 설정 |

## 서브패키지

- `agents/` — 에이전트 호출 인터페이스와 Hermes CLI 구현체
- `debate/` — 턴 루프, prompt 조립, consensus 파싱, 최종 synthesis
- `integrations/` — 외부 연동 계약과 구현체 (`Publisher`, `NoOpPublisher`, Discord bot/registry/publisher)
- `sessions/` — `SessionStore`, append-only 로그, SQLite 글로벌 인덱스
- `topics/` — `TopicConfig` Pydantic 모델과 YAML 로더
