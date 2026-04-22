# discord

Discord slash command, bot config, message publisher 구현체.

## 구성

| 파일 | 설명 |
|------|------|
| `bot.py` | `ParliamentBot`. `/parliament` slash command 핸들링 |
| `registry.py` | Bot config 로더. Discord User ID → Hermes profile 매핑과 `${TOKEN}` 환경변수 치환 |
| `publisher.py` | `DiscordPublisher`. aiohttp로 Discord HTTP API 호출. nonce 중복 방지, fallback, rate limit 대응 |
