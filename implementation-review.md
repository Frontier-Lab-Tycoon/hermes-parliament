# IMPLEMENTATION Review

수정된 `IMPLEMENTATION.md` 기준 구현 계획 리뷰입니다. 이번 리뷰는 이전 지적이 얼마나 반영됐는지와, 현재 남아 있는 설계 충돌이 무엇인지에 초점을 둡니다.

## 총평

이전 버전에 비해 계획이 확실히 좋아졌습니다. 특히 다음 세 가지가 핵심적으로 개선됐습니다.

- `publish/resume` 계약을 Phase 1로 당겨옴
- fallback publish 상태를 `sent_via_fallback`으로 분리함
- synthesis 편향 문제를 줄이기 위해 첫 번째 참가자 fallback을 제거함

즉, 가장 위험했던 구조적 문제들은 상당 부분 정리됐습니다. 지금 문서는 이전처럼 "뒤 phase에서 다시 역설계해야 하는 계획"은 아닙니다.

다만 아직 몇 가지 중요한 충돌이 남아 있습니다.

- Engine phase가 Publisher phase보다 앞서 있는데, 실제 구현 범위는 Publisher를 이미 전제함
- Phase 1 모델 정의가 여전히 후속 acceptance criteria를 모두 담지 못함
- crash-safe resume의 마지막 저장 규칙이 문서에 완전히 못 박혀 있지 않음
- synthesis profile 정책과 summarizer soft limit 설명에 약간의 모순이 남아 있음

현재 상태는 "좋은 계획"에 가깝고, 구현 착수도 가능해 보입니다. 다만 아래 항목들을 먼저 정리하면 재작업 가능성이 더 줄어듭니다.

## 반영된 점

### 1. persistence/publish/resume 계약을 앞당긴 점

이전에는 publish lifecycle이 뒤 phase에 흩어져 있었는데, 지금은 Phase 1에서 다음을 먼저 정의하려고 합니다.

- `PublishState`
- `Checkpoint`
- `mark_turn_publish_pending`
- `mark_turn_published`
- `mark_turn_publish_failed`
- `get_unpublished_turns`

이건 아주 좋은 수정입니다. 이 프로젝트에서 가장 위험한 부분은 Discord나 Hermes 호출 자체보다, 저장과 발송과 복구가 엮이는 상태 전이였기 때문에, 이걸 앞당긴 건 맞는 결정입니다.

### 2. fallback 상태 분리

이전에는 fallback 발송이 사실상 `failed`와 섞여 있어 resume 시 중복 발송 위험이 있었습니다. 지금은 다음 상태 분리가 들어갔습니다.

- `sent`
- `sent_via_fallback`
- `failed_retryable`
- `failed_terminal`

이 변경으로 publish lifecycle이 훨씬 명확해졌습니다.

### 3. synthesis 편향 완화

이전 계획의 가장 큰 문제 중 하나는 synthesis profile이 없을 때 첫 번째 참가자를 재사용하는 것이었습니다. 지금은 그 fallback을 제거하고:

1. `synthesis.profile`
2. `coordinator` profile
3. 규칙 기반 fallback JSON

순으로 정리했습니다. 이건 설계적으로 훨씬 안전합니다.

### 4. summarizer의 automatic drop 제거

자동 drop을 금지한 것도 적절합니다. 드롭은 구현은 쉽지만, 토론형 시스템에서는 실제 근거 손실을 일으키기 때문에 제거한 방향이 맞습니다.

## 남아 있는 핵심 이슈

### 1. Phase 4가 아직 Phase 5를 선행 의존함

가장 큰 남은 문제입니다.

문서상 순서는 다음과 같습니다.

- Phase 4: Turn Loop Engine
- Phase 5: Discord Publisher + Publish State 전이

그런데 Phase 4 구현 범위에는 이미 다음이 포함됩니다.

- `run(..., publisher)`
- `append_turn -> mark_turn_publish_pending -> Discord 발송 -> mark_turn_published/failed`

즉 문서상으로는 Engine이 Publisher보다 먼저인데, 실제 구현 범위는 Publisher를 이미 요구합니다.

이건 순서를 다음 중 하나로 정리하는 편이 좋습니다.

- 방법 A: Phase 4에서는 publisher를 `NoOpPublisher` 또는 mock publisher로 제한
- 방법 B: Phase 5를 Phase 4보다 앞당김
- 방법 C: Phase 4와 5를 하나의 "turn execution + publish lifecycle" phase로 합침

현재 상태는 의존성 그래프와 실제 구현 범위가 서로 다릅니다.

### 2. Phase 1 모델 정의가 아직 불완전함

Phase 1 목표는 persistence/publish/resume 계약을 확정하는 것인데, 정작 `TurnRecord` 필드는 아직 너무 얇습니다.

현재 명시된 `TurnRecord` 필드:

- `turn_uuid`
- `seq`
- `profile`
- `content`
- `structured`
- `consensus_signal`

하지만 뒤 phase와 테스트 시나리오는 사실상 다음도 필요로 합니다.

- `publish_state`
- `published_message_id`
- `published_by`
- `published_at`
- `publish_error`

특히 다음 항목들 때문에 그렇습니다.

- Phase 4 완료 기준: `result.publish_state == "sent"`
- Phase 5 완료 기준: `get_turn_publish_state(...) == "sent"`
- T1-8: `published_by` 기록
- T9-4: crash recovery에서 중복 발송 방지

즉 상태 전이를 API로만 둘 것이 아니라, 어떤 데이터가 턴 레코드에 귀속되는지도 먼저 명시해야 합니다.

권장:

- `TurnRecord`에 publish 메타데이터 포함
- 또는 `TurnRecord`와 분리된 `TurnDeliveryRecord` 모델 도입

지금 상태는 "상태 갱신 함수는 있는데 저장 대상 구조는 덜 정의된 상태"입니다.

### 3. crash-safe resume의 마지막 규칙이 아직 문서화되지 않음

`Checkpoint`에 다음 필드가 추가된 건 좋아졌습니다.

- `last_safe_published_turn_uuid`
- `pending_turn_uuid`

하지만 아직 중요한 경계 사례가 명시되지 않았습니다.

예:

1. Discord 전송 성공
2. `mark_turn_published` 성공
3. `save_checkpoint` 전에 crash

이 경우 resume는 무엇을 기준으로 판단해야 할까요?

선택지는 보통 둘 중 하나입니다.

- `mark_turn_published`가 checkpoint까지 함께 갱신하는 단일 원자적 단계로 본다
- 또는 resume 시 `history + publish metadata`를 기준으로 checkpoint를 재구성한다

지금 문서는 둘 중 어느 쪽인지 명확하지 않습니다. T9-4를 안정적으로 만족시키려면 이 규칙을 스펙에 적어두는 편이 좋습니다.

### 4. Phase 5 완료 기준과 Phase 1 API가 다시 살짝 어긋남

Phase 5 완료 기준에는 다음이 있습니다.

```python
store.get_turn_publish_state(session_id, turn_record.turn_uuid)
```

그런데 Phase 1의 SessionStore API 목록에는 이 메서드가 없습니다.

큰 문제는 아니지만, 이런 작은 불일치는 구현 시작 후 불필요한 수정 포인트가 됩니다.

다음 중 하나로 정리하면 됩니다.

- Phase 1 API에 `get_turn_publish_state` 추가
- 또는 `load_turn`/`load_session`을 통해 상태를 읽는 방식으로 완료 기준 수정

### 5. synthesis profile 정책 설명에 작은 모순이 있음

문서에는 다음 두 문장이 함께 있습니다.

- `topic.yaml`에 `synthesis.profile`을 명시적으로 요구
- 미지정 시 `coordinator` profile 또는 규칙 기반 fallback 사용

이건 완전히 같은 정책이 아닙니다.

정리 방식은 둘 중 하나가 좋습니다.

- 정말 required라면, 미지정은 validation error
- fallback을 둘 거라면 optional 필드로 두고, 권장값이라고 표현

지금 상태는 문서 독자가 "그래서 필수인가, 권장인가"를 한 번 더 해석해야 합니다.

### 6. summarizer의 `soft limit`는 사실상 비영속적 제외이므로 추적 규칙이 필요함

자동 drop은 제거됐지만, 다음 정책은 여전히 남아 있습니다.

- 다음 턴 history에서 가장 오래된 턴을 제외

이건 영구 삭제는 아니지만, 실제로 모델에 들어간 prompt window와 저장된 `history.jsonl` 사이에 차이를 만듭니다.

이 상태에서 나중에 synthesis/debug/resume를 보면 다음 문제가 생깁니다.

- 어떤 턴이 실제로 prompt에 들어갔는지 알기 어려움
- history는 남아 있지만 모델은 못 본 턴이 존재함

따라서 최소한 다음 중 하나는 있어야 합니다.

- prompt snapshot 기록
- `history_window` 메타데이터 저장
- 제외된 턴 ID 목록을 summary event에 포함

즉 soft limit 자체보다, 그 사실을 관찰 가능하게 만드는 장치가 필요합니다.

## 권고 사항

### 우선순위 높음

1. Phase 4와 Phase 5의 순서를 맞추거나, Phase 4에서 publisher 의존성을 제거하기
2. `TurnRecord` 또는 별도 delivery 모델에 publish 메타데이터 필드를 명시하기
3. `mark_turn_published`와 `checkpoint`의 관계를 crash-safe 규칙으로 문서화하기
4. `synthesis.profile`을 required인지 optional인지 하나로 정리하기

### 우선순위 중간

1. `get_turn_publish_state`를 SessionStore API에 추가하거나 완료 기준을 수정하기
2. summarizer soft limit 사용 시 실제 prompt window를 추적하는 메타데이터 규칙 추가
3. 도입부의 "독립적으로 개발/검증" 문구를 "가능한 한 독립적으로 테스트" 수준으로 더 명확히 유지하기

## 최종 결론

수정된 `IMPLEMENTATION.md`는 이전 버전보다 명확하고 실무적입니다. 특히 publish lifecycle과 synthesis bias 관련 리스크를 많이 줄였습니다.

이제 남은 문제는 구조 자체보다 인터페이스 정합성입니다. 다시 말해, 방향은 거의 맞고, phase 경계와 데이터 모델만 조금 더 단단히 맞추면 됩니다.

가장 중요한 남은 한 줄은 이것입니다.

"`publish lifecycle`을 Phase 1로 당긴 결정은 맞았고, 이제 그 결정에 맞게 Engine phase 순서와 TurnRecord 필드를 끝까지 일관되게 맞추면 된다."
