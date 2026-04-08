# slack-fetch-mcp

Slack 워크스페이스에서 채널/사용자별 대화를 **벌크로 가져오는** MCP 서버.

공식 Slack MCP는 쿼리 기반 실시간 검색이라 대량 데이터에 느립니다. 이 도구는 벌크 수집 → 로컬 캐시 → Markdown 정제를 한 번에 처리하여 **위클리 보고서, 조직 분석, 대화 아카이브** 등에 적합합니다.

## 주요 기능

- **벌크 수집**: `search.messages` (빠름) → `conversations.history` (fallback) 이중 전략
- **스레드 전문 수집**: 스레드 대화까지 완전히 가져옴
- **체크포인트/재시작**: 중단 시 이어서 수집 가능
- **Rate Limit 자동 대응**: Tier 자동 감지 + 429 대기
- **텍스트 정제**: `<@U...>` → `@이름`, 링크/이모지 정리 → 깔끔한 Markdown
- **MCP 서버**: Claude Code에서 직접 호출 가능한 7개 Tool 제공

## MCP Tools

| Tool | 설명 |
|------|------|
| `list_channels` | 워크스페이스 채널 목록 |
| `crawl_channel` | 특정 채널 N일간 **전체 대화** 수집 (사용자 필터 없이 채널 내 모든 메시지) |
| `crawl_user` | 특정 사용자 N일간 활동 수집. `include_threads=True`로 스레드까지 한 번에 수집 가능 |
| `search_messages` | 키워드로 Slack 메시지 검색 수집 (Slack 검색 문법 지원) |
| `crawl_mentions` | 특정 사용자가 @멘션된 메시지 수집 (본인 발송 제외) |
| `crawl_threads` | 스레드 전문 수집 |
| `get_collected_data` | 수집 데이터를 Markdown/JSON으로 조회 |

## 설치 및 사용

### Claude Code 플러그인 (권장)

```bash
claude mcp add --from https://github.com/97Wobbler/slack-fetch-mcp
```

### 수동 설치

```bash
pip install git+https://github.com/97Wobbler/slack-fetch-mcp.git

# 초기 설정
slack-fetch init

# MCP 서버 실행
slack-fetch serve
```

### Claude Code에 MCP 서버 등록

```json
// .claude/settings.json
{
  "mcpServers": {
    "slack-fetch": {
      "command": "slack-fetch",
      "args": ["serve"]
    }
  }
}
```

## 사전 준비: Slack App 설정

1. [Slack API](https://api.slack.com/apps)에서 앱 생성
2. **OAuth & Permissions** > User Token Scopes에 추가:
   - `channels:history` — 채널 메시지 읽기
   - `channels:read` — 채널 목록 조회
   - `users:read` — 사용자 정보 조회
   - `search:read` — 메시지 검색
3. **Install to Workspace** 후 User OAuth Token (`xoxp-...`) 복사

## 환경 변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `SLACK_USER_TOKEN` | O | Slack User Token (`xoxp-...`) |
| `TARGET_USER_IDS` | | 수집 대상 사용자 ID (콤마 구분). `crawl_user`/`crawl_mentions` 시 사용. 채널 수집/키워드 검색 시에는 불필요 |
| `TIMEZONE` | | 타임존 (기본: `Asia/Seoul`) |
| `SLACK_FETCH_DATA_DIR` | | 데이터 저장 경로 (기본: `data`) |

## 사용 예시

Claude Code에서:

```
"#general 채널 지난 1주일 대화 가져와서 위클리 보고서 만들어줘"
→ crawl_channel("general", 7) → get_collected_data("channel:general")

"홍길동의 최근 1개월 활동 분석해줘"
→ crawl_user("UXXXXXXXXXX", 30) → get_collected_data("all")

"배포 관련 대화 검색해줘"
→ search_messages("배포", 7) → get_collected_data("search:배포")

"홍길동이 멘션된 대화 수집해줘"
→ crawl_mentions("UXXXXXXXXXX", 30)

"스레드까지 포함해서 홍길동 활동 전부 가져와줘"
→ crawl_user("UXXXXXXXXXX", 30, include_threads=True)
```

## License

MIT
