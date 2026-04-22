# tests

테스트는 3개 계층으로 구성됩니다.

```
unit/          # 단일 모듈 단위 테스트 (mock 위주)
integration/   # 다중 모듈 통합 테스트 (Session→Engine→Publisher)
e2e/           # 엔드투엔드 테스트 (전체 흐름, mock Discord/Hermes)
```

## 실행

```bash
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v
uv run pytest tests/e2e/ -v
uv run pytest tests/ -v
```

## 주요 픽스처 (`conftest.py`)

- `store` — 임시 디렉토리 기반 `SessionStore`
- `mock_backend` — 미리 정의된 응답을 반환하는 백엔드 mock
- `mock_discord_api` — `aioresponses`로 Discord API mock
