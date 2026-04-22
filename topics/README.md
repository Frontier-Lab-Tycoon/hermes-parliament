# topics

토론 주제 YAML 설정 파일을 보관합니다.

## 예시 (`example-debate.yaml`)

```yaml
session:
  name: "example-debate"

protocol:
  type: "alternating"
  ordering: ["participant_1", "participant_2"]
  termination:
    min_turns: 2
    max_turns: 10
    early_stop: true

synthesis:
  enabled: true
  profile: "coordinator"
  schema_path: "schemas/decision.json"

discord:
  channel_id: "123456789"
  coordinator_bot_token: "${COORDINATOR_BOT_TOKEN}"

participant_1: "bot-user-id-1"
participant_2: "bot-user-id-2"
```

## 설정 규칙

- `participant_1` ≠ `participant_2`
- `max_turns` ≥ `min_turns` (기본값: 2)
- `${VAR}` 문법으로 환경변수 치환 지원
