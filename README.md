# slackbox

Slack 워크스페이스에서 채널/사용자별 대화를 **벌크로 가져오는** Claude Code 플러그인.

공식 Slack MCP는 쿼리 기반 실시간 검색이라 대량 데이터에 느립니다. slackbox는 벌크 수집 → 로컬 캐시 → Markdown 정제를 한 번에 처리하여 **위클리 보고서, 조직 분석, 대화 아카이브** 등에 적합합니다.

## 시작하기

### Step 1. Slack 앱 만들기

1. [Slack API](https://api.slack.com/apps)에서 **Create New App** > **From scratch**
2. **OAuth & Permissions** > **User Token Scopes**에 아래 4개 추가:
   - `channels:history` — 채널 메시지 읽기
   - `channels:read` — 채널 목록 조회
   - `users:read` — 사용자 정보 조회
   - `search:read` — 메시지 검색
3. **Install to Workspace** 클릭
4. **User OAuth Token** (`xoxp-...`로 시작) 복사해두기

### Step 2. 플러그인 설치

```bash
# 마켓플레이스 등록
claude plugin marketplace add 97Wobbler/slackbox

# 플러그인 설치
claude plugin install slackbox
```

### Step 3. 토큰 설정

설치 후 Claude Code에서 `/plugins` → slackbox 선택 → **Configure options** → Step 1에서 복사한 `xoxp-...` 토큰 입력.

또는 CLI에서:
```bash
claude plugin configure slackbox
```

이제 Claude Code에서 "슬랙 대화 가져와"라고 말하면 동작합니다.

## 주요 기능

- **벌크 수집**: `search.messages` → `conversations.history` 이중 전략
- **스레드/멘션/키워드 검색**: 완전한 대화 맥락 확보
- **체크포인트/재시작**: 중단 시 이어서 수집
- **Rate Limit 자동 대응**: Tier 자동 감지 + 429 대기
- **DM/Private 채널 지원**: 토큰 scope에 따라 확장 가능
- **8개 MCP Tool + `/slackbox` 라우팅 스킬**

## MCP Tools

| Tool | 설명 |
|------|------|
| `list_channels` | 채널 목록 (`include_private`, `include_dm` 지원) |
| `list_users` | 사용자 목록 (이름 → user_id 매핑) |
| `crawl_channel` | 채널 전체 대화 수집 |
| `crawl_user` | 사용자 활동 수집 (`include_threads=True` 지원) |
| `search_messages` | 키워드 검색 수집 |
| `crawl_threads` | 스레드 전문 수집 |
| `crawl_mentions` | 특정 사용자 멘션 수집 |
| `get_collected_data` | 수집 데이터를 Markdown/JSON으로 조회 |

모든 수집 tool은 `days`(기간, 0=전체)와 `until`(종료일, YYYY-MM-DD)을 지원합니다.

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

"비공개 채널도 포함해서 채널 목록 보여줘"
→ list_channels(include_private=True)
```

또는 `/slackbox`를 입력하면 라우팅 스킬이 자연어 요청을 적절한 tool로 연결해줍니다.

## 수동 설치 (플러그인 없이)

```bash
pip install git+https://github.com/97Wobbler/slackbox.git
slack-fetch init    # Slack User Token 입력
claude mcp add slackbox -- slack-fetch serve
```

## License

MIT
