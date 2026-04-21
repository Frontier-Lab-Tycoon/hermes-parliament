# Hermes Parliament — Implementation Phases

> SPEC.md를 기반으로 한 구현 계획입니다.
> 각 Phase는 **독립적으로 개발/검증**할 수 있도록 최대한 잘게 쪼갰습니다.
> 각 Phase에는 **테스트 시나리오(Acceptance Criteria)**가 포함됩니다.

---

## Phase 0: 프로젝트 부트스트랩

### 목표
실행 가능한 Python 프로젝트 뼈대를 만든다.

### 구현 범위
- `pyproject.toml` (Python 3.11+, asyncio, pytest, pydantic, aiohttp)
- 디렉토리 구조 생성 (`parliament/`, `tests/`, `topics/`)
- CLI entrypoint (`parliament --help`가 동작)
- 기본 로깅 설정 (structlog + rich)

### 완료 기준
```bash
$ parliament --help
# help 메시지 출력
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T0-1 | `parliament --help` 실행 | exit code 0, usage 메시지 출력 |
| T0-2 | `parliament list` 실행 (세션 없음) | 빈 리스트 출력, crash 없음 |
| T0-3 | `pytest tests/` 실행 | 테스트 프레임워크 동작 (아직 테스트 없어도 OK) |

---

## Phase 1: Models + Session Store & Persistence

### 목표
핵심 데이터 모델과 파일 기반 세션 저장소를 구현한다. **이 Phase에서 persistence/publish/resume 계약을 먼저 확정한다.**

### 구현 범위

#### 데이터 모델 (`parliament/models.py`)
- `TurnRecord` (불변 content, publish 메타데이터 미포함):
  - `turn_uuid`, `seq`, `profile`, `role`
  - `content`, `structured`, `consensus_signal`
- `DeliveryEvent` (append-only 상태 이벤트):
  - `seq`: 이벤트 시퀀스 번호 (1부터 증가)
  - `turn_uuid`
  - `event_type`: `"publish_state_changed"`
  - `new_state`: `pending | in_flight | fallback_pending | sent | sent_via_fallback | failed_retryable | failed_terminal`
  - `metadata` (상태별 필수 필드):
    - `in_flight`: `nonce` (필수), `intended_publisher` (필수), `attempt_publisher` (필수)
    - `fallback_pending`: `error` (필수), `attempt_publisher` (필수)
    - `sent`: `message_id` (필수), `published_by` (필수), `published_at` (필수), `attempt_publisher` (필수)
    - `sent_via_fallback`: `message_id` (필수), `published_by` (= "coordinator_fallback", 필수), `published_at` (필수), `attempt_publisher` (필수)
    - `failed_retryable` / `failed_terminal`: `error` (필수), `attempt_publisher` (필수)
  - `timestamp`
- `PublishState`: `pending | in_flight | fallback_pending | sent | sent_via_fallback | failed_retryable | failed_terminal`
- `Checkpoint` (빠른 resume 지점):
  - `session_id`, `next_turn_index`, `next_speaker`, `last_safe_published_turn_uuid`, `pending_turn_uuid`
- `Session`: `session_id`, `status`, `config`, `turns`, `created_at`

#### 세션 저장소 구조 (`parliament/session.py`)
세션 디렉터리 낸부 저장소는 **append-only 이벤트 로그**와 **overwrite checkpoint**로 관리합니다.

| 파일 | 쓰기 패턴 | 내용 | source of truth |
|-----|----------|------|-----------------|
| `history.jsonl` | **append-only, immutable** | `type: "turn_content"` 이벤트. TurnRecord의 content, structured, consensus_signal | **turn content의 유일한 정본** |
| `delivery.jsonl` | **append-only, immutable** | `type: "publish_state_changed"` 이벤트. `DeliveryEvent` 전체 | **publish 상태의 유일한 정본** |
| `checkpoint.json` | **overwrite** | resume 지점. 마지막 안전 턴 + 다음 발언자 | **빠른 복구용 인덱스** (재구성 가능) |

> **Source of Truth 규칙**: `history.jsonl`은 content 정본, `delivery.jsonl`은 publish 상태 정본. 두 파일은 독립적이며, `load_session()` 시에만 조인됨. `history.jsonl` 안에는 publish 메타데이터를 절대 포함하지 않음.

##### delivery.jsonl 레코드 스키마 및 append 규칙
```json
{"seq": 1, "event_type": "publish_state_changed", "turn_uuid": "t-1", "new_state": "pending", "metadata": {}, "timestamp": "2026-04-21T09:00:00Z"}
{"seq": 2, "event_type": "publish_state_changed", "turn_uuid": "t-1", "new_state": "sent", "metadata": {"message_id": "msg-123", "published_by": "participant_bot"}, "timestamp": "2026-04-21T09:00:05Z"}
```
- **append 시점**: `mark_turn_publish_*` 호출 시 즉시 append
- **replay 알고리즘**: `delivery.jsonl`을 **항상 처음부터 순차 읽음**. 각 `turn_uuid`의 마지막 `new_state`를 딕셔너리에 누적. O(N) scan.
- **최적화 (향후)**: 메모리 내 캐시 유지. `last_delivery_seq` 기반 partial replay는 Phase 1 이후 최적화로 미룸.

##### SessionStore API
- `create_session(topic, participants, config) -> session_id`
- `append_turn(session_id, TurnRecord)` → `history.jsonl` append
- `generate_nonce(session_id, turn_uuid, publisher_identity) -> str`
  - **deterministic derivation**: `stable_short_hash(session_id, turn_uuid, publisher_identity)` → 25자 이하 문자열
  - 동일한 입력에 대해 항상 동일한 nonce 생성
  - Discord `enforce_nonce` dedupe는 짧은 시간 창 내 best-effort 중복 방지로만 사용. durable exactly-once 보장으로 간주하지 않음
- `mark_turn_publish_pending(session_id, turn_uuid)` → `delivery.jsonl` append (`new_state: "pending"`)
- `mark_turn_publish_in_flight(session_id, turn_uuid, nonce, intended_publisher, attempt_publisher)`
  1. `delivery.jsonl` append (`new_state: "in_flight"`, metadata에 nonce, intended_publisher, attempt_publisher)
  2. `checkpoint.json` overwrite (`pending_turn_uuid` 설정)
- `mark_turn_publish_fallback_pending(session_id, turn_uuid, error, attempt_publisher)`
  1. `delivery.jsonl` append (`new_state: "fallback_pending"`, error, attempt_publisher)
  2. `checkpoint.json` overwrite (`pending_turn_uuid` = null, `last_safe_published_turn_uuid`는 변경 없음)
- `mark_turn_published(session_id, turn_uuid, message_id, published_by, published_at, state: "sent" | "sent_via_fallback", attempt_publisher)`
  1. `delivery.jsonl` append (`new_state: state`, metadata에 message_id, published_by, published_at, attempt_publisher)
  2. `checkpoint.json` overwrite (`last_safe_published_turn_uuid` 업데이트, `pending_turn_uuid` = null)
- `mark_turn_publish_failed(session_id, turn_uuid, error, retryable, attempt_publisher)`
  1. `delivery.jsonl` append (`new_state: "failed_retryable"` 또는 `"failed_terminal"`, error, attempt_publisher)
  2. `checkpoint.json` overwrite (`pending_turn_uuid` = null, `last_safe_published_turn_uuid`는 **변경 없음**)
- `get_turn_publish_state(session_id, turn_uuid)` → 메모리 캐시 또는 delivery.jsonl 역순 scan
- `get_turn_nonce(session_id, turn_uuid, publisher_identity)` → deterministic derivation으로 재생성. delivery.jsonl에서 확인 가능
- `get_unpublished_turns(session_id)` → history의 모든 turn_uuid 중, delivery replay 결과 `sent`/`sent_via_fallback`가 아닌 것들
- `load_session(session_id)` → `history.jsonl`의 모든 turn + `delivery.jsonl` replay 결과를 조인 → Session 객체

##### delivery.jsonl 안정성 규칙
- **append 시**: 각 이벤트는 newline-terminated JSONL로 write. `\n`까지 완료되어야 유효한 줄로 간주
- **replay 시**: 마지막 줄이 완전한 JSON이 아니면(partial line) **skip + 로그 경고**. 나머지 유효한 줄들만 replay
- `history.jsonl`도 동일한 규칙 적용

##### crash-consistent resume 규칙
- **쓰기 순서**: `delivery.jsonl` append 먼저 → `checkpoint.json` overwrite 나중
- **checkpoint 갱신 시점**:
  - `mark_turn_publish_in_flight` 시: `pending_turn_uuid` = 현재 턴
  - `mark_turn_published` 시: `last_safe_published_turn_uuid` 업데이트, `pending_turn_uuid` = null
  - `mark_turn_publish_failed` 시: `pending_turn_uuid` = null (`last_safe_published_turn_uuid`는 변경 없음)
- **crash 시 복구**:
  1. `checkpoint.json` 읽기
  2. `delivery.jsonl`을 **처음부터 전체 replay**
  3. replay 결과로 현재 publish 상태 복원
- **idempotent recovery**: 동일한 `turn_uuid`에 대해 동일한 `mark_turn_published`가 여러 번 호출되어도, delivery.jsonl append는 멱등하지 않지만, replay 시 마지막 상태만 반영되므로 결과적으로 안전
- **`pending_turn_uuid` vs `failed_retryable` 역할 경계**:
  - `pending_turn_uuid`: 현재 **발송 시도 중**(in_flight)인 턴. 발송 완료/실패 후 null. checkpoint에만 존재.
  - `failed_retryable`: 발송은 끝났으나 실패하여 **재시도 대상**인 턴. delivery.jsonl에 기록. resume 시 `get_unpublished_turns()`로 확인.
  - **resume 기준**: `get_unpublished_turns()` (delivery.jsonl replay 결과)가 최종 진실. `pending_turn_uuid`는 복구 가속화용 힌트.
- **fallback_pending resume 규칙**: `fallback_pending` 상태는 **반드시 `coordinator_fallback`로 이어감**. participant bot으로 재시도하지 않음. resume 시 `fallback_pending`인 턴은 coordinator bot token으로 전송 시도.
- **오래된 `in_flight` 복구 규칙**:
  - `in_flight` 상태에서 crash 후 즉시 resume한 경우: 동일 nonce + 동일 `intended_publisher`로 재전송 가능. Discord `enforce_nonce`의 짧은 dedupe window 안에서는 중복 생성을 제한할 수 있음
  - `in_flight` 상태가 `in_flight_reconcile_after_seconds`(기본 180초)를 초과한 경우: 자동 재전송하지 않고 런타임에서 `ambiguous_in_flight`로 분류 (`delivery.jsonl`에 새 상태로 append하지 않음)
  - `ambiguous_in_flight`는 Discord message search/fetch 또는 수동 확인으로 기존 메시지 존재 여부를 reconciliation한 뒤 기존 `mark_turn_published` 또는 `mark_turn_publish_failed` API로 `sent`/`sent_via_fallback`/`failed_retryable` 상태를 기록
  - durable marker로 각 Discord 메시지에 `session_id` + `turn_uuid`를 footer/본문 메타데이터 형태로 포함하여 reconciliation 가능하게 함

#### 전역 인덱스 (`parliament/index.py`)
- 저장 위치: `~/.parliament/index.db` (세션 디렉터리 낸부 아님)
- `register_session(session_id, status, topic, created_at)`
- `list_sessions()`, `update_status(session_id, status)`

### 완료 기준
```python
store = SessionStore()
sid = store.create_session("topic", [...], {...})
turn = TurnRecord(turn_uuid="t-1", ...)
store.append_turn(sid, turn)
store.mark_turn_publish_pending(sid, "t-1")
nonce = store.generate_nonce(sid, "t-1", "participant_bot")
store.mark_turn_publish_in_flight(sid, "t-1", nonce, "participant_bot", "participant_bot")
store.mark_turn_published(sid, "t-1", "msg-123", "participant_bot", "2026-04-21T...", state="sent", attempt_publisher="participant_bot")
# checkpoint.json은 mark_turn_published 낸부에서 overwrite됨
assert store.get_turn_publish_state(sid, "t-1") == "sent"
assert len(store.get_unpublished_turns(sid)) == 0
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T1-1 | 세션 생성 후 디렉터리 확인 | `~/.parliament/sessions/<id>/` 생성, `history.jsonl` + `delivery.jsonl` 존재 |
| T1-2 | TurnRecord 3개 append 후 `history.jsonl` 읽기 | NDJSON 3라인, 파싱 가능. publish 메타데이터 없음 |
| T1-3 | `delivery.jsonl` append 완료, checkpoint overwrite 전 crash 시뮬레이션 | `load_session`으로 복원 시 `delivery.jsonl` 전체 replay로 최신 상태 복원, idempotent recovery 확인 |
| T1-4 | `index.db`에 세션 등록 후 `list_sessions` | 등록된 세션 반환, 상태 정확 |
| T1-5 | 동시에 2개 세션 생성 | 각각 독립 디렉터리, 데이터 섞이지 않음 |
| T1-6 | publish 상태 전이: pending → sent → get_unpublished_turns | `get_unpublished_turns`가 빈 리스트 반환 |
| T1-7 | publish 상태 전이: pending → failed_retryable → get_unpublished_turns | failed 턴이 unpublished 목록에 포함 |
| T1-8 | mark_turn_published 시 published_by 메타데이터 기록 | `delivery.jsonl`에 `published_by` 저장, replay 시 반영 |
| T1-9 | checkpoint overwrite 중 crash 시뮬레이션 | resume 시 `checkpoint.json` + `delivery.jsonl` 전체 replay로 일관된 상태 복원 |

---

## Phase 2: Config & Validation

### 목표
YAML 설정 파일과 Discord Registry를 파싱하고 검증한다.

### 구현 범위
- `parliament/config.py`: Pydantic 모델
  - `TopicConfig`, `ProtocolConfig`, `SynthesisConfig`, `DiscordConfig`
  - `participant_1`, `participant_2` 필드 (Discord User ID → Profile 매핑용)
- `parliament/discord_registry.py`
  - `load_registry(path)` → `DiscordRegistry` 객체
  - `resolve_profile(discord_user_id)` → `HermesProfile`
  - 토큰 환경변수 치환 (`${TOKEN}` syntax)
- 유효성 검사:
  - profile이 `~/.hermes/profiles/<name>`에 존재하는지
  - Discord Bot Token이 유효한 문자열인지

### 완료 기준
```python
cfg = load_topic("topics/example.yaml")
registry = load_registry("~/.parliament/discord-registry.yaml")
profile = registry.resolve_profile("123456789")
assert profile.hermes_profile == "architect-devil"
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T2-1 | 유효한 `topic.yaml` 파싱 | 모든 필드 정상 로드, default 값 적용 |
| T2-2 | `max_turns`가 1일 때 검증 | ValidationError (min_turns=2와 충돌) |
| T2-3 | `discord-registry.yaml` 파싱 | 환경변수 `${TOKEN}` 치환 확인 |
| T2-4 | 존재하지 않는 Hermes profile 지정 | `FileNotFoundError` 또는 명확한 에러 메시지 |
| T2-5 | participant_1과 participant_2가 같은 봇 지정 | ValidationError |

---

## Phase 3: Hermes Backend

### 목표
Hermes CLI를 subprocess로 호출하고, 출력을 정제한다.

### 구현 범위
- `parliament/backends/base.py`: `AgentBackend` ABC
  - `invoke(profile, prompt, timeout) -> BackendResult`
  - `cancel(handle)`
- `parliament/backends/hermes.py`: `HermesBackend`
  - `asyncio.create_subprocess_exec("hermes", "-p", profile, "chat", "-q", prompt)`
  - ANSI escape code stripping
  - timeout 처리 (default 120s)
- `parliament/backends/registry.py`: `BACKENDS = {"hermes": HermesBackend}`
- `parliament/models.py`: `BackendResult` (text, code, error)

### 완료 기준
```python
backend = HermesBackend()
result = await backend.invoke("architect-devil", "안녕하세요")
assert result.text is not None
assert result.code == 0
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T3-1 | 유효한 profile로 간단한 프롬프트 호출 | 응답 텍스트 반환, ANSI 코드 없음 |
| T3-2 | 150초 이상 걸리는 프롬프트 | timeout 발생, `BackendTimeoutError` |
| T3-3 | 존재하지 않는 profile 호출 | `HermesInvocationError`, stderr 포함 |
| T3-4 | ANSI color code가 포함된 출력 | `strip_ansi` 후 깨끗한 텍스트 |
| T3-5 | subprocess가 segfault 등으로 비정상 종료 | `code != 0`, error 필드에 stderr 기록 |

---

## Phase 4: Turn Loop Engine Core

### 목표
순차 턴 루프와 합의 판정 로직을 구현한다. **이 Phase에서는 실제 Discord 발송 없이, Publisher는 `NoOpPublisher` 또는 mock으로 대체하여 Engine 계약만 먼저 확정한다.**

### 구현 범위
- `parliament/engine.py`: `DebateEngine`
  - `run(session_id, config, registry, backend, publisher_mock)`
  - `determine_next_speaker(turns, ordering)` → alternating
  - `parse_output(raw_text) -> (content, consensus_signal, structured)`
    - **consensus_signal 파싱**: tail block `=== PARLIAMENT SIGNAL ===` 이후 첫 줄
    - 또는 `{"consensus_signal":"agree"}` JSON tail block
  - `check_termination(turns, config) -> bool`
    - `max_turns` 도달
    - `min_turns` 이상이고 모든 참가자 `consensus_signal == "agree"`
  - 턴 실행 시 `SessionStore`와 연동:
    - `append_turn`만 호출. **Phase 4에서는 `mark_turn_publish_*` API를 전혀 호출하지 않음**
    - publish lifecycle은 Phase 5(실제 Discord Publisher)에서만 검증
    - Engine Core 테스트에서는 순서/파싱/종료 조걧만 검증하며, delivery 상태는 관여하지 않음
- `parliament/publishers/noop.py`: `NoOpPublisher` (엔진 단위 테스트 전용)
  - `send_turn()` 호출 시 아무 동작 없이 `None` 반환
  - **실제 발송 완료 의미 없음**. 통합/복구 판단에는 사용하지 않음
  - Engine의 턴 루프 흐름(순서, 종료, consensus 파싱)만 검증

### 완료 기준
```python
engine = DebateEngine(store, NoOpPublisher())
result = await engine.run_turn("architect-devil", "하드코딩 프롬프트", backend)
assert result.content is not None
# Phase 4에서는 append_turn만 호출. publish 상태 API는 전혀 호출하지 않음.
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T4-1 | 2인 alternating 턴 4회 실행 | A→B→A→B 순서로 진행 |
| T4-2 | 참가자가 tail block `=== PARLIAMENT SIGNAL ===\nagree` 출력 | `TurnRecord.consensus_signal == "agree"` |
| T4-3 | 참가자가 tail JSON `{"consensus_signal":"agree"}` 출력 | `TurnRecord.consensus_signal == "agree"` |
| T4-4 | min_turns=2, 턴 1에서 agree | 종료되지 않음 (min_turns 미달) |
| T4-5 | min_turns=2, 턴 3에서 양측 모두 agree | `check_termination` True, Synthesis로 진행 |
| T4-6 | max_turns=4 도달 | `check_termination` True |
| T4-7 | consensus signal이 없는 경우 | `null`로 처리, 종료 조건 미충족 |
| T4-8 | Engine이 4턴 연속 실행 후 SessionStore의 turn history 조회 | 4개의 turn_content 이벤트가 `history.jsonl`에 append-only로 기록됨 |

---

## Phase 5: Discord Publisher + Publish State 전이

### 목표
Discord HTTP API로 메시지를 발송하고, publish lifecycle 상태 전이를 안전하게 관리한다.

### 구현 범위
- `parliament/publishers/base.py`: `Publisher` ABC
- `parliament/publishers/discord.py`: `DiscordPublisher`
  - `send_turn(session_id, turn_record) -> message_id`
    - 해당 profile의 bot token으로 `POST /channels/{channel_id}/messages`
    - **중복 방지**: `turn_record.turn_uuid`에서 파생한 **짧은 nonce**(25자 이하) + `enforce_nonce: true` 사용. 동일 author의 동일 nonce에 대해 Discord가 짧은 시간 창 내 중복 생성을 제한
    - **Durable reconciliation marker**: 모든 turn 메시지에 `session_id` + `turn_uuid`를 포함하여, nonce dedupe window가 지난 crash resume에서도 기존 발송 여부를 확인 가능하게 함
  - `send_final(coordinator_token, synthesis_result)`
  - **Fallback 발송**: 참가자 봇 발송 실패(403/401) 시 Coordinator Bot으로 발송
    - 성공 시 `publish_state = "sent_via_fallback"`, `published_by = "coordinator_fallback"`
    - 실패 시 `publish_state = "failed_terminal"` (retry 불가)
  - **재시도 정책**: 네트워크 오류 시 `failed_retryable`, resume 시 재발송 대상
  - **전송 흐름 (정상 경로)**: 
    1. `generate_nonce(session_id, turn_uuid, intended_publisher)`로 nonce 생성
    2. `mark_turn_publish_in_flight` (nonce + intended_publisher 기록)
    3. Discord 전송 (`nonce` + `enforce_nonce=true`)
    4. `mark_turn_published`
  - **전송 흐름 (fallback 경로)**: 
    1. participant bot 발송 실패 (403/401)
    2. `mark_turn_publish_fallback_pending` (error="403 Unauthorized", attempt_publisher="participant_bot") → fallback 필요 상태 기록
    3. `generate_nonce(session_id, turn_uuid, "coordinator_fallback")`로 새 nonce 생성
    4. `mark_turn_publish_in_flight` (nonce + intended_publisher="coordinator_fallback", attempt_publisher="coordinator_fallback" 기록)
    5. coordinator fallback Discord 전송
    6. `mark_turn_published` (state="sent_via_fallback", published_by="coordinator_fallback", attempt_publisher="coordinator_fallback")
  - **fallback resume 중복 방지**: resume 시 coordinator_fallback로 이어가는 경우는 두 가지뿐: (a) `in_flight` 상태이면서 `intended_publisher="coordinator_fallback"`인 턴, (b) `fallback_pending` 상태인 턴. 둘 다 intended_publisher 변경 금지. `sent_via_fallback`는 이미 발송 완료 상태이므로 `get_unpublished_turns()`에서 제외되어 재발송 대상이 아님. participant bot으로 전환 시 author 변경으로 인한 Discord nonce dedupe 실패 방지. 단, 오래된 `in_flight`는 자동 재전송 전에 durable marker로 reconciliation 수행
  - `publish_state` 업데이트는 `SessionStore` API를 통해 수행 (Phase 1 계약)
- Rate limit 기본 대응 (Discord HTTP 429 응답 시 retry-after 대기)

### 완료 기준
```python
publisher = DiscordPublisher(registry, store)
msg_id = await publisher.send_turn(session_id, turn_record)
assert msg_id is not None
assert store.get_turn_publish_state(session_id, turn_record.turn_uuid) == "sent"
nonce = store.generate_nonce(session_id, turn_record.turn_uuid, "participant_bot")
assert len(nonce) <= 25
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T5-1 | 유효한 bot token으로 메시지 발송 | Discord API 200, message_id 반환, `sent` 기록 |
| T5-2 | 403/401 unauthorized token | Coordinator Bot fallback 발송, `sent_via_fallback` 기록 |
| T5-3 | 네트워크 타임아웃 | 3회 재시도 후 `failed_retryable`, resume 시 재발송 대상 |
| T5-4 | Rate limit (429) 응답 | retry-after 대기 후 재시도 |
| T5-5 | `publish_state="sent"`인 턴 resume 시 | 중복 발송하지 않음 (skip) |
| T5-6 | `publish_state="sent_via_fallback"`인 턴 resume 시 | 중복 발송하지 않음 (skip) |
| T5-7 | `publish_state="failed_retryable"`인 턴 resume 시 | 재발송 시도, 성공 시 `sent`로 전이 |

---

## Phase 6: Discord Slash Command

### 목표
Coordinator Bot이 `/parliament` slash command를 받고, Orchestrator를 트리거한다.

### 구현 범위
- `parliament/discord_bot.py`: Coordinator Bot (discord.py 또는 aiohttp)
  - `on_ready`: slash command 등록
  - `/parliament` handler
    - `topic` (string, required)
    - `participant_1` (User mention, required)
    - `participant_2` (User mention, required)
    - `max_turns` (int, optional, default 10)
  - 멘션 파싱 → Registry 조회 → Profile 리스트 생성
  - ephemeral 응답: "🟢 토론 시작! ..."
  - background task로 `DebateEngine.run()` 실행
- `parliament/cli.py`에 `run-bot` 서브커맨드 추가

### 완료 기준
```bash
$ parliament run-bot
# Coordinator Bot이 Discord에 연결됨
# /parliament 명령어 등록 확인
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T6-1 | `/parliament` 명령어 등록 확인 | Discord 클라이언트에서 명령어 자동완성 노출 |
| T6-2 | `@bot1 @bot2` 멘션으로 명령어 실행 | ephemeral "토론 시작" 응답, Orchestrator 트리거 |
| T6-3 | Registry에 없는 봇 멘션 | ephemeral 에러: "등록되지 않은 봇입니다" |
| T6-4 | participant_1 == participant_2 | ephemeral 에러: "서로 다른 봇을 선택하세요" |
| T6-5 | max_turns=1 입력 | ephemeral 에러: "max_turns는 2 이상이어야 합니다" |
| T6-6 | 토론 중 Slash Command 재실행 | 별도 세션으로 생성 (동시 세션 지원) |

---

## Phase 7: Context Assembly + Summarizer

### 목표
Prompt를 조립하고, 컨텍스트 윈도우를 관리한다. **Engine/Publisher 계약이 안정화된 뒤 고도화한다.**

### 구현 범위
- `parliament/context.py`: `ContextAssembler`
  - `load_soul_md(profile)` → SOUL.md 내용 읽기
  - `build_prompt(profile, topic, history, turn_instruction) -> str`
  - History formatting (markdown 리스트 형태)
- Summarizer
  - threshold 70% 도달 시 가장 오래된 턴 요약 (drop 금지)
  - 요약 결과를 `history.jsonl`에 `event_type: "summary"`로 기록
  - **요약 실패 시 정책**:
    - threshold 80%로 완화 후 재시도
    - 그래도 실패 시 **자동 drop 금지**
    - 대안 1: 현재 턴을 진행하되, 다음 턴의 history에서 가장 오래된 턴을 제외 (soft limit)
    - 대안 2: synthesis 직전에 강제 축약 수행
  - **soft limit 추적**: prompt에 실제로 포함된 턴과 제외된 턴을 `history.jsonl`에 기록
    - `prompt_snapshot` 이벤트: 실제 모델에 전달된 prompt 텍스트 해시 또는 요약
    - `excluded_turn_uuids`: soft limit로 제외된 턴 ID 목록
    - `history_window`: [start_seq, end_seq] 범위 기록
  - **보호 구간**: 현재 참가자의 직전 발언 + User 초기 주제는 절대 요약/제외 대상 아님

### 완료 기준
```python
ctx = ContextAssembler()
prompt = ctx.build_prompt("architect-devil", topic, history, "반박하세요")
assert "SOUL.md 내용" in prompt
assert "이전 턴 내용" in prompt
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T7-1 | SOUL.md가 있는 profile로 프롬프트 조립 | SOUL.md 내용이 system prompt에 포함 |
| T7-2 | SOUL.md가 없는 profile로 프롬프트 조립 | 기본 identity prompt 사용, crash 없음 |
| T7-3 | 10턴 history 조립 | 모든 턴이 formatted history에 포함 |
| T7-4 | history가 70% threshold 초과 | 가장 오래된 턴이 요약되어 포함, 요약본이 history.jsonl에 기록 |
| T7-5 | summarizer 연속 실패 | 자동 drop 없이, soft limit로 다음 턴 진행 가능 |
| T7-6 | 보호 구간 턴이 threshold 초과에 포함 | 보호 구간은 제외되고, 그 다음 오래된 턴이 요약 대상 |

---

## Phase 8: Synthesis Step

### 목표
종료 후 Orchestrator가 최종 JSON을 생성한다.

### 구현 범위
- `parliament/synthesis.py`: `Synthesizer`
  - `run(session_id, profile, history, schema) -> SynthesisResult`
  - 전체 history를 프롬프트로 조립
  - JSON schema 강제 (```json 블록 추출)
  - 파싱 실패 시 retry (max 2회, temperature 0.1)
  - **최종 실패 시 fallback JSON 생성** (규칙 기반)
- `parliament/models.py`: `SynthesisResult` (decision, confidence, reasoning, consensus_reached)

### Synthesis Profile 전략
- `topic.yaml`의 `synthesis.profile`은 **optional이나 권장**.
- 권장 설정: 별도 `coordinator` profile을 지정하여 중립적 synthesis 수행
- 미지정 시 순위:
  1. `coordinator` profile 사용 (있을 경우)
  2. `coordinator` profile 없으면 **규칙 기반 fallback JSON 생성** (첫 번째 참가자 재사용 금지)
- validation: `synthesis.profile`이 지정되었으나 해당 profile이 존재하지 않으면 에러

### 완료 기준
```python
synth = Synthesizer(backend)
result = await synth.run(session_id, "coordinator", history, schema)
assert result.structured["consensus_reached"] in [True, False]
```

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T8-1 | 합의된 히스토리 synthesis | `consensus_reached: true`, `decision` 존재 |
| T8-2 | 불합의 히스토리 synthesis | `consensus_reached: false`, `disagreeing_profiles` 포함 |
| T8-3 | JSON 블록 파싱 실패 | retry 2회 후 규칙 기반 fallback JSON 생성 |
| T8-4 | synthesis profile 미지정, coordinator profile 존재 | coordinator profile로 synthesis 수행 |
| T8-5 | synthesis profile 미지정, coordinator profile도 없음 | 규칙 기반 fallback JSON 생성 (편향 없음) |
| T8-6 | schema에 없는 필드가 응답에 포함 | 무시하거나 strip 후 저장 |

---

## Phase 9: Integration & E2E

### 목표
전체 흐름을 통합하고, 실제 Discord 환경에서 E2E를 검증한다.

### 구현 범위
- `tests/e2e/`: E2E 테스트
  - mock Discord API server (pytest fixture)
  - mock Hermes CLI wrapper (사전 준비된 응답 반환)
- `tests/integration/`: 통합 테스트
  - Session → Engine → Publisher 흐름
  - Crash 후 resume 시나리오
- Docker Compose (선택): 테스트용 Discord mock, SQLite 등

### 테스트 시나리오
| # | 시나리오 | 기대 결과 |
|---|---------|----------|
| T9-1 | **Happy Path**: 2인 4턴 토론 후 합의 | 모든 턴 Discord 발송 완료, 최종 JSON 정상 출력 |
| T9-2 | **Early Stop**: 턴 3에서 양측 agree | 3턴까지만 진행, Synthesis 후 종료 |
| T9-3 | **Max Turns**: 10턴까지 불합의 | 10턴 진행, Synthesis 후 종료 |
| T9-4 | **Crash Recovery**: 턴 3 `mark_turn_publish_in_flight` 기록 후 Discord 전송 성공, `mark_turn_published` 기록 전 crash | `get_unpublished_turns()` 기준으로 resume. 짧은 시간 내 resume이면 동일 nonce + 동일 publisher로 재전송하여 dedupe window 내 중복 제한. dedupe window를 넘긴 오래된 `in_flight`이면 자동 재전송하지 않고 durable marker(`session_id` + `turn_uuid`)로 기존 메시지 reconciliation 후 상태 전이. 이후 턴 4부터 진행 |
| T9-4b | **Crash Recovery + Fallback**: 턴 3 participant bot 발송 실패 → `mark_turn_publish_fallback_pending`(attempt_publisher=participant_bot) 기록 → `mark_turn_publish_in_flight`(intended_publisher=coordinator_fallback) 기록 → coordinator fallback 발송 성공, `mark_turn_published` 기록 전 crash | `get_unpublished_turns()` 기준으로 resume. 턴 3이 `in_flight`(intended_publisher=coordinator_fallback) 상태. 짧은 시간 내 resume이면 coordinator_fallback로 재전송하여 author 일관성 유지. 오래된 `in_flight`이면 durable marker로 reconciliation 후 `sent_via_fallback` 또는 재시도 가능 상태로 전이 |
| T9-5 | **Bot Offline**: 참가자 봇 403 응답 | Coordinator Bot fallback 발송, 토론 계속 진행 |
| T9-6 | **Hermes Timeout**: 한 참가자가 120초 초과 | `[TIMEOUT]` 기록, 다음 턴으로 진행 |
| T9-7 | **Concurrent Sessions**: 동시에 2개 채널에서 토론 | 세션 독립 유지, 데이터 섞이지 않음 |

---

## Phase 의존성 그래프

```
Phase 0 (Bootstrap)
    │
    ├──→ Phase 1 (Models + Session Store + Global Index)
    │       │       │
    │       │       └──→ Phase 2 (Config + Registry)
    │       │               │
    │       │               ├──→ Phase 3 (Hermes Backend)
    │       │               │       │
    │       │               │       └──→ Phase 4 (Turn Loop Engine Core)
    │       │               │               │
    │       │               └──→ Phase 5 (Discord Publisher + Publish State)
    │       │                       │
    │       │                       └──→ Phase 6 (Discord Slash Command)
    │       │
    │       └──→ Phase 7 (Context Assembly + Summarizer)
    │               │           (의존: Phase 1 TurnRecord/history format,
    │               │                    Phase 2 config/profile registry)
    │               │
    │               └──→ Phase 8 (Synthesis)
    │                       │
    │                       ▼
    │                   Phase 9 (Integration & E2E)
```

> 각 Phase는 **가능한 한 독립적으로 테스트**할 수 있도록 설계했습니다.
> 단, `persistence / publish lifecycle / resume` 계약은 여러 Phase에 걸친 공통 전제입니다.
> **핵심 원칙**: publish/resume 계약을 먼저 안정화한 뒤, context summarization을 고도화합니다.

---

## 부록: 테스트 실행 가이드

### Unit Test
```bash
pytest tests/unit/ -v
```

### Integration Test
```bash
pytest tests/integration/ -v
```

### E2E Test (Mock 환경)
```bash
pytest tests/e2e/ -v --discord-mock --hermes-mock
```

### 전체 테스트
```bash
pytest tests/ -v
```
