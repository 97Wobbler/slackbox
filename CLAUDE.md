# CLAUDE.md

Slack 워크스페이스에서 대화를 벌크 수집하는 MCP 서버. 수집만 담당하고 분석은 하지 않음.

## 구조

```
slack_fetch/
├── mcp_server.py    # MCP Tools (7개): list_channels, crawl_channel, crawl_user,
│                    #   search_messages, crawl_threads, crawl_mentions, get_collected_data
├── config.py        # CrawlerConfig — SLACK_USER_TOKEN만 필수
├── client.py        # WebClient 팩토리
├── channels.py      # 채널 목록 수집
├── messages.py      # 메시지 수집 (search + history 이중 전략)
├── threads.py       # 스레드 수집 (공유 캐시: data/raw/threads/)
├── mentions.py      # 멘션 수집
├── rate_limit.py    # Tier 감지 + 429 대기
├── text_cleaner.py  # Slack mrkdwn → plaintext
└── cli.py           # init/serve/status
```

## 데이터 저장 경로

```
data/raw/
├── channels.json              # 채널 목록 (공유)
├── threads/                   # 스레드 (공유 캐시)
├── channels/{channel_id}/     # 채널 전체 대화
├── search/{query}.jsonl       # 키워드 검색 결과
└── {user_id}/                 # 사용자별 메시지
```

## 운영 방식

CONTRIBUTING.md 참조. 요약: origin(private)에서 개발, public에 main만 squash push.

## 실행

```bash
pip install -e .
slack-fetch init    # SLACK_USER_TOKEN 입력
slack-fetch serve   # MCP 서버
```
