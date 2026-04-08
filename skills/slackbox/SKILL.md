---
name: slackbox
description: Slack 워크스페이스 대화를 벌크 수집하는 MCP 도구의 라우팅 스킬. 사용자의 자연어 요청을 적절한 slackbox MCP tool로 연결합니다. 슬랙 대화 수집, 채널 대화 가져오기, 사용자 활동 조회, 키워드 검색, 멘션 수집, 스레드 수집 등을 요청할 때 이 스킬을 사용합니다. 또한 Slack 앱 설정 방법, 토큰 발급, 연결 상태 확인 등의 도움말도 제공합니다.
---

# Slackbox — Slack 벌크 수집 라우팅 스킬

사용자의 요청을 분석하여 적절한 slackbox MCP tool을 호출하거나, 도움말을 제공합니다.

## 트리거

다음과 같은 요청에서 이 스킬이 호출되어야 합니다:
- "슬랙 대화 가져와", "슬랙에서 읽어와", "슬랙 수집"
- "채널 대화", "위클리 보고서", "주간 대화"
- "홍길동 활동", "사용자 메시지 수집"
- "멘션된 대화", "키워드 검색"
- "슬랙 설정", "토큰 설정", "slackbox 뭐 할 수 있어?"
- "/slackbox"

## 동작 방식

### 인자가 없거나 도움을 요청할 때 (help/doctor 모드)

사용자에게 아래 내용을 안내합니다:

```
📦 Slackbox — Slack 대화 벌크 수집 도구

사용 가능한 기능:
  1. 채널 전체 대화 수집  — "general 채널 지난 1주일 대화 가져와"
  2. 사용자 활동 수집     — "홍길동의 최근 1개월 활동"
  3. 키워드 검색          — "배포 관련 대화 검색"
  4. 멘션 수집            — "홍길동이 멘션된 대화"
  5. 스레드 수집          — "스레드까지 포함해서 수집"
  6. 채널/사용자 목록     — "채널 목록", "사용자 목록"

초기 설정이 필요하다면:
  1. Slack 앱 생성: https://api.slack.com/apps
  2. OAuth scopes 추가: channels:history, channels:read, users:read, search:read
  3. User OAuth Token (xoxp-...) 복사
  4. `slack-fetch init` 실행하여 토큰 입력

무엇을 하고 싶으신가요?
```

### 자연어 요청이 있을 때 (라우팅 모드)

사용자의 의도를 분석하여 적절한 MCP tool을 호출합니다.

**의도 → MCP Tool 매핑:**

| 사용자 의도 | MCP Tool | 예시 |
|-------------|----------|------|
| 채널 목록 확인 | `list_channels()` | "채널 뭐 있어?", "비공개 채널도 보여줘" |
| 사용자 목록/ID 확인 | `list_users()` | "홍길동 user_id가 뭐야?" |
| 채널 대화 수집 | `crawl_channel(channel, days)` | "general 채널 지난 1주일" |
| 사용자 활동 수집 | `crawl_user(user_id, days)` | "홍길동의 최근 1개월 활동" |
| 키워드 검색 | `search_messages(query, days)` | "배포 관련 대화 검색" |
| 멘션 수집 | `crawl_mentions(user_id, days)` | "홍길동이 멘션된 대화" |
| 스레드 수집 | `crawl_threads(user_id=uid)` | "스레드도 가져와" |
| 수집 데이터 조회 | `get_collected_data(scope)` | "수집한 거 보여줘" |

**파라미터 추론 규칙:**
- 기간 언급이 없으면: 채널은 `days=7`, 사용자는 `days=30` 기본값 사용
- "전체" 또는 "모든 기간"이면: `days=0`
- 사용자 이름만 있고 ID를 모르면: 먼저 `list_users()`로 ID 확인 후 진행
- 채널에 `#`이 붙어있으면: 자동으로 제거됨
- "스레드 포함"이면: `crawl_user(..., include_threads=True)`
- 종료일 지정: `until="YYYY-MM-DD"` 파라미터 사용
- 비공개 채널: `list_channels(include_private=True)`

**모호한 요청 처리:**
- 의도가 불명확하면, 가능한 기능 목록을 보여주고 선택하게 합니다.
- 여러 단계가 필요한 작업(예: "홍길동 분석")이면, 순서대로 실행합니다:
  1. `list_users()` → user_id 확인
  2. `crawl_user(user_id, days, include_threads=True)` → 메시지+스레드 수집
  3. `get_collected_data("all")` → 수집 데이터 조회

## 주의사항

- 이 스킬은 **수집만** 담당합니다. 분석/요약은 수집 후 Claude가 직접 처리합니다.
- 대량 수집(1000+ 메시지)은 시간이 걸릴 수 있습니다. Rate limit 대응이 자동으로 동작합니다.
- Slack User Token(`xoxp-`)이 설정되어 있어야 합니다. 안 되어 있으면 설정 방법을 안내하세요.
