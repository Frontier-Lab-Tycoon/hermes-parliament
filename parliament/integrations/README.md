# integrations

외부 서비스 연동 계층.

## 구성

| 파일/디렉터리 | 설명 |
|------|------|
| `base.py` | `Publisher` ABC. 외부 채널로 턴과 최종 결과를 발행하는 공통 계약 |
| `noop.py` | `NoOpPublisher`. 테스트/로컬 실행용 무동작 publisher |
| `discord/` | Discord slash command, registry, message publisher 구현체 |
