# agents

Agent 백엔드 추상화 및 Hermes CLI 구현체.

## 구성

| 파일 | 설명 |
|------|------|
| `base.py` | `AgentBackend` ABC. `invoke(profile, prompt, timeout)` → `BackendResult` |
| `hermes.py` | `HermesBackend`. `asyncio.create_subprocess_exec("hermes", ...)` 호출, ANSI strip, timeout 처리 |
| `registry.py` | `BACKENDS = {"hermes": HermesBackend}` |

## BackendResult

```python
BackendResult(text="...", code=0, error=None)
```

- `text`: stdout (ANSI 제거됨)
- `code`: exit code
- `error`: stderr (비정상 종료 시)
