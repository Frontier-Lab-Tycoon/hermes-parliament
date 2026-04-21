# SPEC Review

`SPEC.md` 기준 스펙 리뷰입니다. 관점은 다음 3가지입니다.

- 구현 가능성: 지금 정의만으로 MVP를 안정적으로 만들 수 있는가
- 발전 가능성: 이후 확장 시 구조가 버틸 수 있는가
- 수정 필요 지점: 실제 구현 전에 스펙을 더 명확히 해야 하는가

## 총평

방향성은 좋습니다. 특히 `backend`, `protocol`, `publisher`, `session`을 분리한 구조는 이후 확장에 유리합니다. Discord를 진입점으로 쓰고, 실제 추론은 내부 Orchestrator가 수행하며, 결과만 각 봇이 발화하는 모델도 제품 컨셉이 분명합니다.

다만 현재 스펙은 "데모로는 빠르게 만들 수 있지만, 운영 가능한 MVP로 바로 가기에는 몇 가지 핵심 계약이 비어 있는 상태"입니다. 특히 Discord interaction 제약, 최종 결과 생성 규칙, resume 시 중복 발송 방지, 합의 판정 방식은 구현 전에 스펙을 먼저 다듬는 편이 맞습니다.

추천 결론은 다음과 같습니다.

- Phase 1은 범위를 더 줄여도 됩니다.
- Discord 인터페이스는 실제 제약에 맞게 다시 정의하는 게 좋습니다.
- 최종 결과 생성은 "마지막 참가자 발화"와 분리해야 합니다.
- persistence는 저장 자체보다 "복구 후 중복 없이 이어가기"까지 계약에 포함해야 합니다.

## 구현 가능성 평가

### 구현 가능한 부분

- Hermes CLI를 subprocess로 호출하는 Phase 1 구조
- 파일 기반 세션 저장소 (`history.jsonl`, `checkpoint.json`)
- 순차 턴 루프
- Discord를 통한 slash command 진입
- 봇별 토큰을 사용한 개별 발화

이 정도는 Python 3.11 + asyncio 기반으로 충분히 구현 가능합니다.

### 구현 전에 반드시 정리해야 할 부분

#### 1. Slash command 인터페이스 정의가 Discord 모델과 맞지 않음

현재 스펙은 다음 형태를 가정합니다.

```text
/parliament <topic> [participants...]
```

하지만 Discord application command는 가변 길이 인자를 자연스럽게 받는 CLI 형태가 아닙니다. 옵션은 미리 고정된 개수와 타입으로 정의되어야 합니다.

따라서 다음 중 하나로 바꾸는 편이 좋습니다.

- `participant_1`, `participant_2`, `participant_3`, `participant_4` 같은 고정 슬롯 제공
- `participants_csv` 문자열을 입력받아 내부에서 파싱
- `/parliament` 이후 modal을 열어서 참가자를 입력받음

Phase 1 기준으로는 고정 슬롯 방식이 가장 단순합니다.

#### 2. `public=false`와 "참가자 봇들이 직접 발화" 모델이 충돌함

스펙에는 private 실행을 위해 `public: false` 옵션이 있지만, 동시에 각 참가자 봇이 채널에 직접 메시지를 보내는 모델을 전제로 합니다.

이 둘은 잘 맞지 않습니다. ephemeral 응답은 slash command interaction 맥락에 묶여 있고, 일반적인 채널 메시지처럼 여러 봇이 각자 흩어 보내는 방식과 동일하게 다루기 어렵습니다.

Phase 1에서는 다음 중 하나로 정리하는 편이 좋습니다.

- `public` 옵션 삭제, 공개 채널 세션만 지원
- private 모드는 Coordinator Bot만 응답하고 참가자 봇 직접 발화는 비활성화
- private 모드는 DM 기반으로 별도 분기

MVP라면 공개 채널만 지원하는 쪽이 가장 현실적입니다.

#### 3. 최종 결과 생성 규칙이 불안정함

현재는 `enforce_schema_on_last_turn_only: true`인데, 동시에 `early_stop`도 존재합니다. 이 경우 "마지막 턴"이 곧 "최종 결론 생성 턴"이라는 보장이 없습니다.

예를 들어 참가자가 중간에 `<AGREE>`를 출력해 종료되면, 최종 JSON이 없는 상태로 종료될 수 있습니다.

그래서 final output은 반드시 별도 단계로 분리하는 편이 좋습니다.

권장 방식:

1. 참가자 턴은 자유 텍스트 중심
2. 종료 조건 충족 시 Orchestrator가 별도 synthesis step 실행
3. synthesis step에서만 최종 JSON schema 강제

이 구조가 훨씬 안정적입니다.

#### 4. resume는 가능해 보여도 idempotency 계약이 없음

스펙에는 checkpoint와 auto-resume가 있지만, 실제로는 다음 문제가 남아 있습니다.

- Hermes 응답은 저장했는데 Discord 전송 전에 죽은 경우
- Discord는 전송했는데 checkpoint 갱신 전에 죽은 경우
- `assistant_text`, `structured_output`이 따로 append되는 동안 중간 crash가 난 경우

지금 구조대로면 resume 시 중복 발송이나 누락이 생길 수 있습니다.

그래서 턴/이벤트 저장 모델에 다음 필드가 필요합니다.

- `turn_uuid`
- `publish_state`: `pending | sent | failed`
- `published_message_id`
- `published_at`

그리고 checkpoint는 "다음 speaker"뿐 아니라 "마지막으로 안전하게 publish 완료된 turn"까지 알아야 합니다.

## 발전 가능성 평가

## 좋은 점

### 1. 컴포넌트 분리가 확장에 유리함

다음 분리는 타당합니다.

- `backends/`
- `protocols/`
- `publishers/`
- `flows/`

이 구조면 나중에 Hermes 외 backend를 붙이거나, Discord 외 채널로 확장하는 것도 가능합니다.

### 2. session 디렉터리 기반 영속성은 유지보수에 유리함

`history.jsonl`과 `checkpoint.json`을 파일로 남기는 방향은 디버깅, grep, 장애 복구에 유리합니다. 운영 초기에 DB 중심 설계보다 훨씬 낫습니다.

### 3. Flow escape hatch 아이디어는 맞음

정적 YAML만으로는 복잡한 handoff나 조건 분기를 감당하기 어렵기 때문에, Python Flow를 escape hatch로 두는 방향 자체는 좋습니다.

다만 이것은 Phase 2 이후가 더 적절합니다. Phase 1 스펙에서 너무 전면에 두면 구현 범위가 과도하게 커질 수 있습니다.

## 확장 전에 손봐야 할 점

### 1. 멀티 봇 모델은 기술보다 운영이 더 어려움

profile마다 Discord app/token/install 상태를 관리해야 하므로, participant 수가 늘수록 운영 복잡도가 크게 증가합니다.

즉 병목은 추론 엔진이 아니라 bot fleet 관리가 될 가능성이 큽니다.

그래서 Phase 1에는 다음 제약을 명시하는 것이 좋습니다.

- curated profile만 지원
- 봇 수 상한 설정
- registry는 수동 관리
- 동적 bot onboarding은 Phase 2 이후

### 2. `state.db`를 세션마다 두는 설계는 효율이 낮음

세션별 `state.db`는 세션 내부 조회에는 도움이 되지만, `parliament list` 같은 글로벌 조회에는 오히려 불리합니다.

더 나은 구조는 다음과 같습니다.

- 세션 디렉터리 내부: `config.yaml`, `history.jsonl`, `checkpoint.json`
- 전역 인덱스: `~/.parliament/index.db`

이렇게 하면 세션 데이터와 인덱싱 책임이 분리됩니다.

### 3. backend abstraction은 좋지만 capability 계약이 필요함

TurnRecord에는 `model`, `tokens_in`, `tokens_out` 같은 값이 들어가는데, Hermes CLI가 항상 이런 메타데이터를 제공한다는 보장이 없습니다.

따라서 backend 확장성을 살리려면 다음 중 하나가 필요합니다.

- 전부 optional metadata로 취급
- backend capability 선언 추가

예:

```python
class BackendCapabilities(TypedDict):
    structured_output: bool
    token_usage: bool
    cancellation: bool
    streaming: bool
```

이렇게 해야 backend별 계약이 깔끔해집니다.

## 수정 권고 사항

## 우선순위 높음

### 1. Phase 1 범위를 더 줄이기

추천 MVP 범위:

- 공개 채널 세션만 지원
- 2인 토론만 지원
- ordering은 fixed 또는 mention order만 지원
- per-turn 자유 텍스트
- 종료 후 Orchestrator synthesis로만 최종 JSON 생성
- Flow/Judge/shared scratchpad는 제외

이렇게 줄이면 구현 성공 가능성이 크게 올라갑니다.

### 2. 합의 판정 방식을 문자열 태그에서 구조화 필드로 바꾸기

현재 `<AGREE>` 기반 판정은 취약합니다.

- 인용 중 등장할 수 있음
- 모델이 실수로 누락할 수 있음
- 2인/3인 이상에서 quorum 규칙이 애매함

더 나은 방식:

- 매 턴 끝에 아주 작은 구조화 필드 추가
- 예: `consensus_signal: "agree" | "continue" | "uncertain"`

또는 마지막 줄 규약을 명시적으로 두는 것도 가능합니다.

### 3. thinking 저장을 기본 계약에서 제거하기

스펙에 `thinking` 이벤트와 `THINKING:` 출력 패턴이 포함되어 있는데, 이건 외부 계약으로 두지 않는 편이 낫습니다.

이유:

- 모델별 지원 편차가 큼
- 내부 추론 노출 이슈가 생김
- parser 복잡도만 증가함

권장:

- 외부 저장 계약은 `content`, `structured`, `error` 정도만 유지
- 디버그 trace는 별도 opt-in 로깅으로 분리

### 4. 보안/격리 문구를 현실에 맞게 수정하기

현재는 "각 Hermes Profile은 별도의 `HERMES_HOME`을 가진다"고 되어 있지만, 실제 backend 예시에는 그 보장이 없습니다.

따라서 둘 중 하나를 해야 합니다.

- 실제로 환경 격리 구현 추가
- 또는 스펙 문구를 "논리적 profile 분리" 수준으로 완화

지금 상태라면 보안 문구가 과장되어 있습니다.

## 우선순위 중간

### 5. Discord publish 실패 fallback 정책 재정의

현재는 참가자 봇 발송 실패 시 Coordinator Bot이 대신 발송한다고 되어 있습니다.

이 fallback 자체는 실용적이지만, 제품 경험상 다음 정보가 같이 필요합니다.

- 원래 누구의 발화였는지 명확히 표시
- fallback 발송이 일어났다는 메타데이터 기록
- 추후 재발송 여부 정책

그렇지 않으면 사용자 입장에서 persona continuity가 깨집니다.

### 6. Summarizer 정책을 더 명확히 하기

지금은 70% threshold와 요약 제외 규칙이 있지만, 누가 요약하는지와 요약 결과를 어디에 저장하는지가 모호합니다.

최소한 다음은 정해야 합니다.

- 요약 결과를 history에 이벤트로 남길지
- resume 시 요약본을 재사용할지
- summarizer failure 시 어떻게 fallback할지

### 7. `role`과 `stance-less` 철학의 관계를 정리하기

스펙은 orchestrator가 stance를 주입하지 않는다고 하면서도 `role`, `judge`, `mediator`, `debater`를 적극적으로 사용합니다.

이건 완전한 모순은 아니지만, 경계가 불분명합니다.

정리 권장:

- `role`은 토론 상의 절차적 책임만 정의
- 실제 관점/성격/논조는 profile이 결정

이 문장을 스펙에 명시하면 혼선이 줄어듭니다.

## 제안하는 스펙 수정 방향

아래 정도로 재정리하면 훨씬 단단해집니다.

### Phase 1 재정의

- Coordinator Bot이 slash command를 받는다.
- 참가자는 `participant_1`, `participant_2` 고정 슬롯으로 받는다.
- 세션은 공개 채널에서만 실행한다.
- 각 참가자는 순차적으로 자유 텍스트를 발화한다.
- 합의 여부는 짧은 구조화 필드로만 판정한다.
- 종료 후 Orchestrator가 별도 synthesis step으로 final JSON을 생성한다.
- 모든 턴은 append-only로 저장하고, publish state를 함께 기록한다.
- resume는 "중복 발송 없이 이어가기"를 목표 계약으로 정의한다.

### Phase 2 이후로 미루는 항목

- private/ephemeral session
- Judge/Mediator
- Flow runtime
- dynamic handoff
- shared scratchpad
- tool sharing
- multi-backend 일반화

## 최종 결론

이 스펙은 컨셉과 구조는 좋습니다. 특히 Discord를 입구로 두고, 내부에서 오케스트레이션한 뒤 각 봇이 직접 말하는 UX는 충분히 매력적입니다.

하지만 현재 상태는 "만들 수 있느냐"보다 "어디서 바로 흔들리느냐"가 더 잘 보이는 스펙입니다. 핵심 문제는 기술 난이도보다 계약의 불명확성입니다.

따라서 다음 순서가 적절합니다.

1. Discord command/response 모델을 실제 플랫폼 제약에 맞게 줄인다.
2. final output 생성을 participant turn과 분리한다.
3. persistence에 publish idempotency를 포함한다.
4. Phase 1 범위를 더 좁힌다.

이 네 가지만 먼저 반영하면, 구현 가능성과 발전 가능성이 모두 좋아집니다.
