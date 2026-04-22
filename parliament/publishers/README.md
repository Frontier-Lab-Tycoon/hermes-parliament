# publishers

Discord 메시지 발송 계층.

## 구성

| 파일 | 설명 |
|------|------|
| `base.py` | `Publisher` ABC. `send_turn()`, `send_final()` |
| `discord.py` | `DiscordPublisher`. aiohttp로 Discord HTTP API 호출. nonce 중복 방지, fallback, rate limit 대응 |
| `noop.py` | `NoOpPublisher`. 아무 동작 없음 (엔진 단위 테스트 전용) |

## Publish State 전이

```
pending → in_flight → sent
    ↓           ↓
fallback_pending  failed_retryable → sent (재시도)
    ↓
sent_via_fallback
```

모든 상태 전이는 `SessionStore`의 `mark_turn_*` API를 통해 `delivery.jsonl`에 append-only로 기록됩니다.
