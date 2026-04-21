# Hermes Parliament — Multi-Agent Turn-Based Orchestrator

## 1. 개요 (Overview)

**Hermes Parliament**는 Nous Research의 [Hermes Agent](https://github.com/NousResearch/hermes-agent)를 기반으로 한 **멀티 에이전트 턴 기반 대화 오케스트레이터**입니다. 서로 다른 `profile`을 가진 Hermes 에이전트들이 서로의 출력을 입력으로 삼아 추론하며, 지정한 턴 수 안에서 최종 결론을 도출합니다.

**Discord가 진입점이자 물리입니다.** 사용자는 Slash Command로 참가할 Discord 봇들을 멘션하고 주제를 던지면, Parliament Orchestrator가 서버 낸부에서 대화를 진행하고, **각 턴의 결과는 해당 Profile에 연결된 Discord Bot이 직접 메시지로 전송**합니다. 실제 추론은 Orchestrator가 서버 낸부에서 처리되며, Discord는 입력 채널이자 각 에이전트의 표현(Output) 채널입니다.

> **핵심 철학**: "Trigger on Discord, orchestrate inside, speak back as bots."

### 참고 프로젝트
- [Hermes Agent #344](https://github.com/NousResearch/hermes-agent/issues/344): L3 Live Dialogue, Shared Memory Pools, Adversarial Debate Mode
- [CAMEL-AI](https://github.com/camel-ai/camel): Inception Prompting, RolePlaying paradigm
- [acpx](https://github.com/openclaw/acpx): Session lifecycle, Prompt queueing, NDJSON structured output, Flow system, Graceful cancel
- [AgentEnsemble](https://github.com/irfanalidv/AgentEnsemble): DebateOrchestrator, TraceHooks
- [MoFA](https://www.mintlify.com/mofa-org/mofa/multi-agent/debate): DebateProtocol, CollaborationMessage

---

## 2. 목표와 범위 (Goals & Non-Goals)

### Goals (Phase 1)
- [ ] Discord Slash Command (`/parliament`)로 **2인** 참가자 봇 멘션 + 주제 입력 → 토론 시작
- [ ] 멘션된 Discord Bot → Hermes Profile 자동 매핑
- [ ] 각 턴 결과를 **해당 Profile의 Discord Bot이 공개 채널에 직접 발송** (봇 아이콘/이름 그대로)
- [ ] **고정 순서** 턴 기반 대화 (멘션 순서 또는 alternating). **자유 텍스트 발화**.
- [ ] 종료 후 **Orchestrator가 별도 synthesis step**으로 최종 JSON 생성
- [ ] 토론자의 성격/역할은 **Profile의 `SOUL.md`에 의해 자연스럽게 결정** (Orchestrator가 stance를 주입하지 않음)
- [ ] **Session 디렉터리 기반 영속성** + **전역 인덱스**: crash 후 중복 없이 auto-resume
- [ ] Hermes의 기존 Profile 시스템을 그대로 활용 (별도 에이전트 구현 없음)

### Non-Goals (Phase 1)
- 에이전트 간 실시간 병렬 실행 (parallel turns)
- 에이전트 간 Tool 호출 공유/위임
- 자체 메모리/스킬 시스템 구현 (Hermes에 위임)
- Discord 내에서 태그 기반 트리거로 에이전트 호출
- Parliament 자체가 별도의 Discord Gateway WebSocket을 유지하는 것 (HTTP API만 사용)
- **Private / Ephemeral 세션**
- **Judge, Mediator, Flow runtime, Shared scratchpad**
- **3인 이상 토론, Dynamic handoff**
- **Multi-backend 일반화** (Hermes만 지원)

---

## 3. 시스템 아키텍처 (System Architecture)

```
┌─────────────────────────────────────────────────────────────┐
│  User: /parliament @bot1 @bot2 "주제"                       │
└──────────────────────┬──────────────────────────────────────┘
                       │  Discord Slash Command
                       ▼
┌─────────────────────────────────────────────────────────────┐
│         Parliament Coordinator Bot (Discord HTTP)            │
│              - Slash command 처리                            │
│              - 멘션 파싱 → Profile 매핑                     │
└──────────────────────┬──────────────────────────────────────┘
                       │  IPC / internal API
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Hermes Parliament Orchestrator                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Loader    │  │   Engine    │  │  Discord Publisher  │  │
│  │  (YAML+CTX) │  │ (Turn Loop) │  │  (Multi-Bot HTTP)   │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                     │             │
│         └────────────────┼─────────────────────┘             │
│                          │                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Session Store (File-based)               │   │
│  │  ~/.parliament/sessions/<session_id>/                │   │
│  │  ├── config.yaml     (세션 설정)                      │   │
│  │  ├── history.jsonl   (NDJSON turn records)           │   │
│  │  └── checkpoint.json (복원 지점 + publish state)     │   │
│  │                                                       │   │
│  │  전역 인덱스: ~/.parliament/index.db                   │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────┘
                           │  hermes -p <profile> chat -q "..."
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │ Profile: A │  │ Profile: B │  │ Profile: C │
    │ (Hermes)   │  │ (Hermes)   │  │ (Hermes)   │
    │ - config   │  │ - config   │  │ - config   │
    │ - memory   │  │ - memory   │  │ - memory   │
    │ - skills   │  │ - skills   │  │ - skills   │
    └────────────┘  └────────────┘  └────────────┘
           │               │               │
           │  Discord HTTP API (send message)
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │ Discord    │  │ Discord    │  │ Discord    │
    │  Bot A     │  │  Bot B     │  │  Bot C     │
    │ (@bot1)    │  │ (@bot2)    │  │ (@bot3)    │
    └────────────┘  └────────────┘  └────────────┘
```

### 3.1 컴포넌트 설명

#### Orchestrator Engine (`parliament/engine.py`)
- 턴 루프의 중앙 제어자
- YAML 설정을 읽어 `Session` 객체 초기화
- 매 턴 맞는 Profile의 Hermes CLI를 호출하여 응답 획득
- 컨텍스트 조립 (System Prompt + Conversation History + Turn Instruction)
- 종료 조건 판정 (max_turns 도달 / early_termination trigger)
- **Checkpoint auto-flush**: 매 턴 완료 후 즉시 디스크에 기록

#### Session Store (`parliament/session.py`)
하나의 주제(topic)에 대한 전체 대화 상태를 파일 기반으로 보관합니다. **acpx의 session 디렉터리 구조를 차용**합니다.

```
~/.parliament/sessions/<session_id>/
├── config.yaml          # 해당 세션의 실행 설정 (topic.yaml 복사)
├── history.jsonl        # NDJSON turn records (append-only)
├── checkpoint.json      # 다음 턴 인덱스, 다음 발언자, 상태
└── state.db             # SQLite: 인덱싱, 검색, 메타데이터
```

- `history.jsonl`: append-only NDJSON로 복원 및 관찰 용이
- `checkpoint.json`: SIGINT/crash 시 복원 지점
- `state.db`: `parliament list`, `parliament status` 쿼리용

#### Discord Publisher (`parliament/publishers/discord.py`)
- **멀티 봇 발송**: 각 Profile에 등록된 Discord Bot Token으로 HTTP API 호출
- 턴 단위 전송 (실시간) 또는 최종 결과만 전송 (설정 가능)
- Rich Embed 포맷 지원 (Profile 아바타, 턴 번호, 발화 시간)
- Coordinator Bot은 Slash Command 처리만 담당, 발언은 참가자 봇들이 직접 수행

#### Discord Bot Registry (`parliament/discord_registry.py`)
- Discord User ID / Mention → Hermes Profile 매핑 관리
- `~/.parliament/discord-registry.yaml`에 봇 토큰 및 프로필 매핑 저장
- Slash Command 파싱 시 멘션된 봇들을 Profile 리스트로 변환

---

## 4. 커뮤니케이션 프로토콜 (Communication Protocol)

### 4.1 메시지 구조 (`TurnRecord`)

```python
class TurnRecord:
    turn_uuid: str         # 고유 ID (resume 시 중복 방지)
    seq: int               # 전체 대화 내 전역 시퀀스 (0-based, acpx style)
    stream: str            # "turn"
    turn_id: int           # 턴 번호
    profile: str           # Hermes profile name
    role: str              # 절차적 책임만 정의 (debater). 실제 관점/성격은 profile이 결정
    
    # Content
    content: str           # 에이전트의 자연어 출력 (markdown)
    structured: dict       # 선택적 구조화 필드 (consensus_signal 등)
    
    # Consensus signal (매 턴 종료 시 판정)
    consensus_signal: str  # "agree" | "continue" | null
    
    # Publish idempotency
    publish_state: str     # "pending" | "sent" | "failed"
    published_message_id: str | null
    published_at: str | null
    
    metadata: dict         # {
                           #   "model": "anthropic/claude-sonnet-4",
                           #   "tokens_in": 2048,      # optional (backend 제공 시)
                           #   "tokens_out": 512,      # optional
                           #   "latency_ms": 3200,
                           #   "timestamp": "2026-04-21T09:30:00+09:00"
                           # }
```

**NDJSON 기록 예시:**
```jsonl
{"turn_uuid":"t-0","seq":0,"stream":"turn","turn_id":0,"profile":"user","content":"주제: 마이크로서비스 vs 모놀리스","metadata":{"timestamp":"2026-04-21T09:00:00Z"}}
{"turn_uuid":"t-1","seq":1,"stream":"turn","turn_id":1,"profile":"architect-devil","content":"모놀리스가 옳다...","consensus_signal":"continue","publish_state":"sent","published_message_id":"123456789","metadata":{"latency_ms":3200}}
```

### 4.2 컨텍스트 조립 규칙 (Context Assembly)

Hermes는 원샷 모드(`chat -q`)만 사용하므로, **모든 히스토리는 Orchestrator가 프롬프트로 조립**하여 전달합니다.

```
[SYSTEM PROMPT]
├─ Base Identity (Profile의 SOUL.md + config 기반)
├─ Session Context (주제, 목표, 제약사항)
├─ Role Instruction (Inception Prompt: 역할, 톤, 출력 형식)
└─ Rules (발언 길이 제한, 금지어, JSON 출력 규칙)

[CONVERSATION HISTORY]
├─ Turn 0 (User): "주제 및 초기 프롬프트"
├─ Turn 1 (Profile A): "..."
├─ Turn 2 (Profile B): "..."
└─ ... (최대 N개, 초과 시 요약)

[CURRENT TURN INSTRUCTION]
├─ "당신은 N번째 발언자입니다."
├─ "이전 발언자의 핵심 주장: ..."
├─ "이 턴에서 해야 할 것: [rebuttal|elaborate|synthesize]"
└─ "출력은 반드시 아래 JSON Schema를 따르세요."
```

### 4.3 Context Window 관리

- **Threshold**: 모델 컨텍스트의 70% 도달 시 가장 오래된 턴 요약
- **Summarizer**: 동일 프로필로 요약 요청 (경량 모델 Phase 2). 요약 결과는 history.jsonl에 별도 이벤트로 기록 (`event_type: "summary"`)
- **Rule**: 현재 참가자의 직전 발언과 User의 초기 주제는 **절대 요약 대상에서 제외**
- **Resume 시**: 요약본을 재사용. Summarizer 실패 시 threshold를 80%로 완화하고 재시도, 그래도 실패 시 가장 오래된 턴을 생략 (drop)

---

## 5. 설정 스펙 (Configuration Specification)

### 5.1 메인 설정 파일 (`topic.yaml`)

> Phase 1 참가자는 Discord Slash Command의 **고정 슬롯**으로 받습니다. `participants`는 테스트용 fallback입니다.

```yaml
# parliament topic configuration v1
version: "1.0"
session:
  id: "auto"                 # auto | UUID (resume 시 사용)
  topic: "마이크로서비스 vs 모놀리스: 스타트업 초기에 뭘 써야 하는가?"
  objective: "참가자들이 합의를 이루거나 최대 턴까지 대화를 나눈 뒤 최종 결론 도출"
  max_turns: 10              # default: 10. Slash Command에서 오버라이드 가능

# (선택) 테스트용 Fallback: Slash Command 없이 CLI로 실행할 때만 사용
# participants:
#   - profile: "architect-devil"
#   - profile: "architect-angel"

protocol:
  type: "debate"             # Phase 1: debate만 지원
  ordering: "alternating"    # Phase 1: alternating 또는 mention-order만 지원

  # Phase 1에서는 phases 없이 단순 자유 토론. 필요시 아래 optional 필드 사용
  # phases: []

  termination:
    max_turns: 10
    min_turns: 2             # 최소 턴 수 (합의도 이 턴 이후부터 유효)
    early_stop: true         # consensus_signal 기반 조기 종료

# 참가자 턴은 자유 텍스트. 최종 결과는 Orchestrator synthesis step에서 생성
synthesis:
  enabled: true              # Phase 1 필수
  profile: "coordinator"     # synthesis를 수행할 profile (fallback: 첫 번째 참가자 profile)
  prompt: |
    아래 토론 내용을 바탕으로 최종 결론을 JSON 형식으로 정리하세요.
  output:
    format: "json"
    schema:
      type: object
      properties:
        decision:
          type: string
        confidence:
          type: number
          minimum: 0
          maximum: 1
        reasoning:
          type: string
        consensus_reached:
          type: boolean
      required: ["decision", "confidence", "reasoning", "consensus_reached"]

# Discord Bot Registry: 멘션된 봇 → Profile 매핑
discord:
  coordinator_bot_token: "${COORDINATOR_BOT_TOKEN}"
  publish_mode: "per_turn"   # per_turn | final_only
  
  templates:
    turn: |
      **🎙️ 턴 {turn_id}**
      ─────────────────────
      {content}
    final: |
      **📊 최종 결론**
      ```json
      {structured}
      ```
    
  embed:
    color_by_profile: true
    show_timestamp: true

# Phase 2 이후 확장 포인트 (Phase 1에서는 무시)
extensions:
  shared_scratchpad: false
  tool_sharing: false
  judge_profile: null
  enable_flows: false
```

### 5.2 프로필 기반 Hermes 활용 및 Discord Bot 매핑

Hermes의 기존 프로필 시스템을 그대로 사용합니다.

```bash
# 프로필 생성 (기존 Hermes CLI 사용)
hermes profile create architect-devil --clone
hermes profile create architect-angel --clone

# 각 프로필에 SOUL.md 작성 (Personality/역할은 여기서 자연스럽게 결정)
# ~/.hermes/profiles/architect-devil/SOUL.md
# -> "당신은 신속한 프로토타이핑을推崇하는 실용주의 아키텍트입니다..."
# ~/.hermes/profiles/architect-angel/SOUL.md
# -> "당신은 확장성과 장기적 관점을 중시하는 엔터프라이즈 아키텍트입니다..."
```

**Orchestrator는 stance를 주입하지 않습니다.** 토론자의 성격, 톤, 입장은 전적으로 Profile의 `SOUL.md`와 `personality` 설정에 따릅니다. Orchestrator는 주제와 대화 맥락만 제공하고, 각 에이전트가 자신의 정체성에 맞춰 자연스럽게 발언하도록 유도합니다.

#### Discord Bot Registry (`~/.parliament/discord-registry.yaml`)

Parliament는 멘션된 Discord Bot이 어떤 Hermes Profile에 대응하는지 알아야 합니다. 별도 레지스트리 파일로 관리합니다.

```yaml
# ~/.parliament/discord-registry.yaml
profiles:
  architect-devil:
    hermes_profile: "architect-devil"
    discord_bot_token: "${DEVIL_BOT_TOKEN}"
    discord_user_id: "123456789012345678"
    discord_name: "악마의 대변인"
    avatar_url: "https://cdn.discordapp.com/avatars/..."
    
  architect-angel:
    hermes_profile: "architect-angel"
    discord_bot_token: "${ANGEL_BOT_TOKEN}"
    discord_user_id: "987654321098765432"
    discord_name: "천사의 대변인"
    avatar_url: "https://cdn.discordapp.com/avatars/..."

# Coordinator Bot (Slash Command 처리용)
coordinator:
  bot_token: "${COORDINATOR_BOT_TOKEN}"
  application_id: "..."
```

**보안 고려사항**:
- Bot Token은 환경변수나 `.env`로 주입 (`${...}` syntax 지원)
- Registry 파일은 `chmod 600`으로 제한
- Coordinator Bot은 `applications.commands` scope 필요

---

## 6. 백엔드 추상화 (Backend Abstraction) — acpx 차용

acpx는 `codex`, `claude`, `pi` 등 여러 에이전트를 통일된 인터페이스로 다룹니다. Parliament도 유사하게 **Backend Registry**를 두고 확장합니다.

```python
# parliament/backends/base.py
class AgentBackend(ABC):
    @abstractmethod
    async def invoke(self, profile: str, prompt: str, config: dict) -> BackendResult: ...
    
    @abstractmethod
    async def cancel(self, handle: str) -> None: ...

# parliament/backends/hermes.py
class HermesBackend(AgentBackend):
    # Phase 1: subprocess invoke
    # Phase 2+: persistent session queue (acpx-style)

# parliament/backends/registry.py
BACKENDS = {
    "hermes": HermesBackend,
    # "codex": CodexBackend,   # future
    # "claude": ClaudeBackend, # future
}
```

### 6.1 HermesBackend (Phase 1: Subprocess)

```python
async def invoke(self, profile: str, prompt: str, config: dict) -> BackendResult:
    proc = await asyncio.create_subprocess_exec(
        "hermes", "-p", profile, "chat", "-q", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=config.get("timeout", 120))
    # ANSI strip, JSON block extract
    return BackendResult(text=strip_ansi(stdout.decode()), code=proc.returncode)
```

### 6.2 향후 QueuedBackend (Phase 2+)

acpx의 prompt queueing을 차용하여, Hermes 프로세스를 persistent하게 유지하고 IPC 큐로 프롬프트를 전달하는 방식으로 전환 가능합니다.

---

## 7. 프롬프트 엔지니어링 (Inception Prompting)

### 7.1 시스템 프롬프트 템플릿

각 턴에서 Hermes에게 전달하는 최종 프롬프트는 다음 템플릿으로 구성됩니다. **Orchestrator는 토론자의 입장(stance)을 주입하지 않으며**, Profile의 `SOUL.md`와 `personality` 설정에 따라 자연스럽게 역할을 수행하도록 합니다.

```markdown
# IDENTITY
당신은 "{{profile_name}}"입니다.
{{soul_md_content}}

# SESSION CONTEXT
주제: {{topic}}
목표: {{objective}}
현재 Phase: {{phase}}
당신의 턴: {{turn_id}} / {{max_turns}}

# CONVERSATION HISTORY (최근 {{history_limit}}개)
{{formatted_history}}

# YOUR TURN
{{turn_specific_instruction}}

# OUTPUT FORMAT
{{output_format_instruction}}

# RULES
- 당신의 성격, 가치관, 전문성은 IDENTITY 섹션에 명시된 바에 따릅니다.
- Orchestrator가 입장을 정해주지 않습니다. 자연스럽게 당신의 관점에서 발언하세요.
- 다른 참가자의 주장에 반박하거나 동의할 수 있습니다.
- 합의에 도달했다고 판단되면 `consensus_signal: "agree"`를 structured 필드에 포함해 주세요.
```

### 7.2 역할별 프롬프트 예시

> **원칙**: `role`은 토론 절차상의 책임만 정의합니다(debater). 실제 관점, 성격, 논조는 **Profile의 SOUL.md와 personality가 결정**합니다. Orchestrator는 stance를 주입하지 않습니다.

**Debater:**
```markdown
- 당신의 관점에서 주제에 대해 발언하세요.
- 이전 발언자의 논점을 정확히 파악한 뒤 반박하거나 보완하세요.
- 감정적 표현 없이 논리와 데이터(가상 포함 가능)로 설득하세요.
- 각 발언은 500 토큰 내외로 제한합니다.
- 합의에 도달하면 마지막에 `consensus_signal: "agree"`를 JSON에 포함해 주세요.
```

**Judge (Phase 2):**
```markdown
당신은 중재자입니다. 참가자들의 발언을 객관적으로 평가하세요.
- 각 참가자의 핵심 논점 3가지를 요약하세요.
- 가장 논리적이고 근거가 충분한 입장을 선택하세요.
- 최종 결론은 반드시 JSON 형식으로 출력하세요.
```

### 7.3 출력 강제 메커니즘

**Phase 1 참가자 턴**: 자유 텍스트만 출력. JSON 강제 없음.

**Synthesis Step**: Orchestrator가 최종 JSON schema를 강제합니다.

```markdown
아래 토론 내용을 바탕으로 최종 결론을 JSON 형식으로 정리하세요.
```json
{{schema_example}}
```
```

Orchestrator는 출력에서 ```json ... ``` 블록을 추출하여 파싱합니다. 실패 시:
1. JSON 블록이 없으면 전체 텍스트를 `content`로 저장, `structured`는 null
2. JSON 파싱 실패 시 재시도 (Retry: max 2회, temperature 0.1로 하향)
3. 2회 재시도 실패 시 Orchestrator가 판단하여 fallback JSON 생성

---

## 8. 실행 흐름 (Execution Flow)

```
[Discord Slash Command]
  │
  ▼
[/parliament topic:... participant_1:@bot1 participant_2:@bot2 max_turns:10]
  │
  ▼
[Parse Command]
  ├─ 멘션된 봇들을 Discord Bot Registry에서 조회 → Profile 리스트 생성 (2인 고정)
  ├─ 주제 추출
  └─ max_turns (default 10, 옵션으로 오버라이드)
  │
  ▼
[Initialize Session]
  ├─ 유효성 검사 (profile 존재 여부, Hermes CLI 확인)
  ├─ Session ID 생성 (uuid4)
  ├─ Session 디렉터리 생성 (~/.parliament/sessions/<id>/)
  ├─ 전역 index.db에 세션 등록
  └─ Dynamic participants 기록 (멘션 순서 유지)
  │
  ▼
[Turn Loop]
  │
  ├─ Determine next speaker (ordering rule, default alternating)
  │
  ├─ [Assemble Context]
  │   ├─ Profile의 SOUL.md 로드 (stance 주입 없음)
  │   ├─ History 조립 (요약 필요 시 Summarizer 호출)
  │   ├─ Turn instruction 생성 (자유 토론, 역할은 SOUL.md에 맡김)
  │   └─ 최종 prompt 구성
  │
  ├─ [Invoke AgentBackend]
  │   HermesBackend.invoke(profile, prompt)
  │   ├─ timeout: 120s (configurable)
  │   └─ stdout capture → ANSI strip
  │
  ├─ [Parse Output]
  │   ├─ `consensus_signal` 필드 추출 (agree | continue | null)
  │   ├─ TurnRecord 생성 (publish_state="pending")
  │   └─ history.jsonl append + checkpoint.json flush
  │
  ├─ [Publish to Discord]
  │   └─ 해당 Profile의 Discord Bot Token으로 채널에 메시지 발송
  │   └─ 성공 시 TurnRecord.publish_state="sent", published_message_id 기록
  │   └─ 실패 시 publish_state="failed", Coordinator Bot fallback 발송
  │
  ├─ [Check Termination]
  │   ├─ max_turns 도달 → Synthesis
  │   ├─ min_turns 이상이고 모든 참가자 consensus_signal="agree" → Synthesis
  │   └─ 그 외 → Turn Loop
  │
  └─ [Not terminated] → Turn Loop
  │
  ▼
[Synthesis Step] (Phase 1 필수)
  ├─ 지정된 synthesis profile로 전체 히스토리 전달
  ├─ 최종 JSON schema 강제
  └─ SynthesisResult 생성
  │
  ▼
[Publish Final]
  ├─ 최종 결과를 Coordinator Bot으로 Discord 발송 (Embed)
  │
  ▼
[Checkpoint Save & Session Close]
  ├─ checkpoint.json: status="completed", last_published_turn_uuid 기록
  ├─ index.db 업데이트
  └─ graceful shutdown
  │
  ▼
[End]
```

---

## 9. 데이터 모델 (Data Models)

### 9.1 Session

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running | completed | failed | paused",
  "config": { /* topic.yaml 내용 */ },
  "created_at": "2026-04-21T00:00:00Z",
  "completed_at": null,
  "final_output": { /* parsed JSON */ }
}
```

### 9.2 Checkpoint Format (acpx-style)

```json
{
  "version": 1,
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "next_turn_index": 3,
  "next_speaker": "architect-angel",
  "checkpointed_at": "2026-04-21T00:05:00Z",
  "last_safe_published_turn_uuid": "t-2",
  "pending_turn_uuid": "t-3"
}
```

> **Idempotency 계약**: `last_safe_published_turn_uuid`는 "Discord에 안전하게 발송 완료된 마지막 턴"을 가리킵니다. resume 시 이 턴 이후부터만 재발송을 시도합니다.

---

## 10. Flow System (Python Escape Hatch) — Phase 2

> **Phase 1 Non-Goal**. 아래 내용은 Phase 2 이후 확장을 위한 설계 참고용입니다.

acpx는 `acpx flow run ./my-flow.ts`로 TypeScript 기반 워크플로우를 실행합니다. Parliament도 복잡한 조건/분기/루프가 필요할 때 **Python Flow**를 지원할 예정입니다.

```python
# topics/custom-debate.py (Phase 2 예시)
from parliament.flows import Flow, Turn

async def main(flow: Flow):
    await flow.load_config("topics/base-debate.yaml")
    await flow.turn("architect-devil")
    await flow.turn("architect-angel")
    if not flow.has_consensus():
        await flow.turn("judge", role="judge")
```

실행:
```bash
parliament flow run topics/custom-debate.py
```

---

## 11. 확장성 로드맵 (Extensibility Roadmap)

### Phase 1: MVP (현재 스펙)
- **2인 토론** (Debate)만 지원
- **고정 순서** (alternating 또는 mention-order)
- **공개 채널** Discord 세션만 지원
- **자유 텍스트 발화** (JSON 강제 없음)
- 종료 후 **Orchestrator Synthesis**로 최종 JSON 생성
- **Session 디렉터리 + 전역 index.db + checkpoint auto-flush**
- **Publish idempotency** (resume 시 중복 발송 방지)

### Phase 2: 고급 오케스트레이션
- **3인 이상 Panel / RoundTable**
- **Judge/Mediator 역할**
- **Dynamic Handoff**: LLM 기반 다음 발언자 선택
- **Flow System**: Python 스크립트 기반 동적 워크플로우
- **Private / Ephemeral 세션**

### Phase 3: 메모리 & 도구 공유 (L2/L3)
- **Shared Scratchpad**
- **Tool Sharing**
- **Sub-delegation**

### Phase 4: Workflow DAG & Multi-Backend
- **Workflow DAG**: `A -> [B, C] -> D`
- **Conditionally Branching**
- **Multi-backend**: Codex, Claude 등 Hermes 외 엔진 지원
- **Human-in-the-loop**

---

## 12. 구현 스택 (Implementation Stack)

| 영역 | 기술 | 이유 |
|-----|------|------|
| 언어 | Python 3.11+ | Hermes 생태계와의 친화성, asyncio 지원 |
| 설정 | Pydantic + YAML | 타입 안전성, Hermes의 config.yaml와 통일감 |
| CLI 호출 | `asyncio.create_subprocess_exec` | Hermes 프로세스의 비동기 실행 |
| 영속성 | File-based (NDJSON) + SQLite | acpx 스타일, 별도 서버 불필요, grep-friendly |
| Discord | discord.py 또는 aiohttp (Webhook) | 유연한 통합 |
| 로깅 | structlog + rich | 가독성 높은 콘솔 출력, structured logging |
| 테스팅 | pytest + pytest-asyncio | 비동기 코드 테스트 |

---

## 13. 프로젝트 구조 (Project Structure)

```
hermes-parliament/
├── parliament/
│   ├── __init__.py
│   ├── cli.py                  # 엔트리포인트: parliament start topic.yaml
│   ├── config.py               # Pydantic 모델 (TopicConfig, Protocol, Output...)
│   ├── engine.py               # Turn loop, orchestration core
│   ├── session.py              # SessionStore, checkpointing, history.jsonl
│   ├── index.py                # 전역 index.db 관리
│   ├── context.py              # Prompt assembly, history management, summarization
│   ├── models.py               # TurnRecord, Session dataclasses
│   ├── parser.py               # Output parsing (JSON extraction, schema validation)
│   ├── exceptions.py           # 커스텀 예외
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── base.py             # AgentBackend ABC
│   │   ├── registry.py         # BACKENDS map
│   │   └── hermes.py           # SubprocessBackend (Phase 1)
│   ├── publishers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── discord.py          # Multi-Bot HTTP 발송
│   ├── discord_registry.py     # Bot Mention → Profile 매핑
│   ├── protocols/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── debate.py           # Phase 1 구현
│   │   └── panel.py            # Phase 2 구현
│   └── flows/
│       ├── __init__.py
│       └── runtime.py          # Python Flow escape hatch
├── topics/
│   ├── example-debate.yaml     # 샘플 설정
│   └── custom-debate.py        # 샘플 Flow
├── tests/
│   └── ...
├── pyproject.toml
└── SPEC.md                     # 이 문서
```

---

## 14. 인터페이스

### 14.1 Discord Slash Commands

Parliament는 **Coordinator Bot**을 통해 Discord Slash Command를 제공합니다.

```
/parliament topic: <string>
          participant_1: <mention>
          participant_2: <mention>
          [max-turns: <integer>]
```

| 옵션 | 타입 | 필수 | 기본값 | 설명 |
|-----|------|------|--------|------|
| `topic` | string | ✅ | — | 토론 주제 |
| `participant_1` | User/Bot mention | ✅ | — | 참가자 1 (Discord Bot) |
| `participant_2` | User/Bot mention | ✅ | — | 참가자 2 (Discord Bot) |
| `max-turns` | integer | ❌ | 10 | 최대 턴 수 (2~20) |

**예시:**
```
/parliament topic: "스타트업 초기 아키텍처: 모놀리스 vs 마이크로서비스"
          participant_1: @악마의대변인
          participant_2: @천사의대변인
          max-turns: 8
```

**응답 흐름:**
1. Coordinator Bot이 즉시 "🟢 토론 시작! 참가자: @bot1, @bot2 / 주제: ..." ephemeral 응답
2. Orchestrator가 백그라운드에서 턴 루프 실행
3. 각 턴 완료 시 해당 Profile의 Bot이 채널에 메시지 전송
4. 종료 후 Orchestrator Synthesis → Coordinator Bot이 최종 결과 Embed 발송

> **Phase 1 제약**: 공개 채널만 지원. Private/ephemeral 세션은 Non-Goal.

### 14.2 로컬 CLI (관리/디버깅용)

```bash
# 세션 목록 (전역 index.db 조회)
parliament list

# 세션 결과 출력
parliament show <session_id> --format json
parliament show <session_id> --format markdown

# checkpoint resume (crash 복원)
parliament resume <session_id>
```

---

## 15. 에러 처리 및 복원력

| 시나리오 | 처리 방식 |
|---------|----------|
| Hermes CLI timeout (120s) | 해당 턴을 `"[TIMEOUT] 응답 없음"`으로 기록, 다음 턴으로 진행 |
| JSON 파싱 실패 (최종 턴) | temperature 0.1로 2회 재시도, 그래도 실패 시 Orchestrator fallback |
| Profile 미존재 | 세션 시작 전 검증, 실패 시 Coordinator Bot이 ephemeral로 에러 알림 |
| **Discord Bot 발송 실패** (403/401) | Coordinator Bot이 **fallback 발송** (원래 봇 이름 명시). `publish_state="failed"` 기록. Registry 설정 확인 알림 |
| Discord 전송 실패 (네트워크) | 로컬에 로그 저장, retry queue에 보관 (3회 재시도). resume 시 재시도 |
| **SIGINT (Ctrl+C)** | **graceful cancel**: 현재 turn 완료 → publish → checkpoint flush → exit |
| **Crash / SIGKILL** | **auto-resume**: `last_safe_published_turn_uuid` 기준으로 중복 없이 이어감 |
| Context window 초과 | 가장 오래된 턴 요약 (Summarizer 호출). 요약 결과를 별도 history 이벤트로 기록 |

---

## 16. 보안 및 격리

- 각 Hermes Profile은 **논리적으로 분리된 config와 memory**를 가집니다. (물리적 HERMES_HOME 분리는 Hermes 설정에 따름)
- Orchestrator는 Profile의 config를 읽기 전용으로 사용하며, 수정하지 않습니다.
- 민감 정보(Discord Bot Token, API Key)는 `.env` 또는 환경변수로 주입합니다.
- `.parliament/` 디렉토리는 `chmod 700`으로 제한합니다.
- Discord Bot Token은 `~/.parliament/discord-registry.yaml`에 저장하며, 반드시 `chmod 600`으로 제한합니다.

---

## Appendix A: 용어 정의

| 용어 | 정의 |
|-----|------|
| **Profile** | Hermes Agent의 독립된 인스턴스 정의 (`~/.hermes/profiles/<name>/`) |
| **Turn** | 한 명의 에이전트가 한 번 발언하는 단위 |
| **Round** | 모든 참가자가 각각 한 번씩 발언 완료한 주기 |
| **Phase** | 대화의 단계 (opening, debate, rebuttal, closing 등) |
| **Protocol** | 대화의 진행 방식 (debate, panel, chain 등) |
| **Session** | 하나의 주제에 대한 전체 대화 및 상태 |
| **Publisher** | 외부 채널(Discord 등)로 결과를 낸송하는 컴포넌트 |
| **Flow** | Python 스크립트로 작성한 동적 워크플로우 |
| **Backend** | 에이전트 실행 엔진 (Hermes, Codex, Claude 등) |

---

## Appendix B: Hermes CLI 호출 예시

```python
import asyncio
import re

def strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

async def invoke_hermes(profile: str, prompt: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "hermes", "-p", profile, "chat", "-q", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    if proc.returncode != 0:
        raise HermesInvocationError(stderr.decode())
    return strip_ansi(stdout.decode())
```

> 참고: Hermes의 `chat -q` 출력에는 터미널 제어 문자(ANSI color codes)가 포함될 수 있으므로, 파싱 전 `strip_ansi` 처리가 필요합니다.
