# slackbox

Slack 워크스페이스에서 채널/사용자별 대화를 **벌크로 가져오는** MCP 서버.

공식 Slack MCP는 쿼리 기반 실시간 검색이라 대량 데이터에 느립니다. 이 도구는 벌크 수집 → 로컬 캐시 → Markdown 정제를 한 번에 처리하여 **위클리 보고서, 조직 분석, 대화 아카이브** 등에 적합합니다.

## 주요 기능

- **벌크 수집**: `search.messages` (빠름) → `conversations.history` (fallback) 이중 전략
- **스레드 전문 수집**: 스레드 대화까지 완전히 가져옴
- **체크포인트/재시작**: 중단 시 이어서 수집 가능
- **Rate Limit 자동 대응**: Tier 자동 감지 + 429 대기
- **텍스트 정제**: `<@U...>` → `@이름`, 링크/이모지 정리 → 깔끔한 Markdown
- **MCP 서버**: Claude Code에서 직접 호출 가능한 8개 Tool 제공

## MCP Tools

| Tool | 설명 |
|------|------|
| `list_channels` | 채널 목록 (public/private/DM 지원: `include_private`, `include_dm`) |
| `list_users` | 사용자 목록 (이름 → user_id 매핑) |
| `crawl_channel` | 채널 전체 대화 수집 (`days`, `until` 지정 가능) |
| `crawl_user` | 사용자 활동 수집 (`include_threads=True`로 스레드 포함) |
| `search_messages` | 키워드 검색 수집 (Slack 검색 문법 지원) |
| `crawl_threads` | 스레드 전문 수집 (자동 발견 또는 수동 지정) |
| `crawl_mentions` | 특정 사용자 멘션 수집 |
| `get_collected_data` | 수집 데이터를 Markdown/JSON으로 조회 |

모든 수집 tool은 `days`(기간, 0=전체)와 `until`(종료일, YYYY-MM-DD) 파라미터를 지원합니다.

## 설치 및 사용

### Claude Code 플러그인 (권장)

```bash
claude plugin add --from https://github.com/97Wobbler/slackbox
```

설치 시 Slack User Token 입력을 요청받습니다. MCP 서버 + `/slackbox` 스킬이 함께 설치됩니다.

### 수동 설치

```bash
pip install git+https://github.com/97Wobbler/slackbox.git
slack-fetch init    # Slack User Token 입력
claude mcp add slackbox -- slack-fetch serve
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
| `SLACK_FETCH_DATA_DIR` | | 데이터 저장 경로 (기본: `data`) |

## 사용 예시

Claude Code에서:

```
"#general 채널 지난 1주일 대화 가져와"
→ crawl_channel("general", 7) → get_collected_data("channel:general")

"홍길동 user_id가 뭐지?"
→ list_users()

"홍길동의 최근 1개월 활동 분석해줘"
→ crawl_user("UXXXXXXXXXX", 30, include_threads=True)

"2024년 상반기 배포 관련 대화 검색"
→ search_messages("배포", days=0, until="2024-06-30")

"홍길동이 멘션된 대화 수집"
→ crawl_mentions("UXXXXXXXXXX", 30)

"비공개 채널도 포함해서 채널 목록 보여줘"
→ list_channels(include_private=True)
```

## License

MIT
