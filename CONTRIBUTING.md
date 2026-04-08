# Contributing — 개발 및 배포 운영 방식

## 원격 레포 구조

| remote | URL | 공개 | 용도 |
|--------|-----|------|------|
| `origin` | https://github.com/97Wobbler/slack-fetch-mcp-dev.git | private | 개발용. 모든 브랜치 push 가능 |
| `public` | https://github.com/97Wobbler/slack-fetch-mcp.git | public | 배포용. **main 브랜치만** push |

## 초기 세팅 (새 PC에서)

```bash
git clone https://github.com/97Wobbler/slack-fetch-mcp-dev.git slack-fetch-mcp
cd slack-fetch-mcp
bash scripts/setup-dev.sh
```

## 개발 워크플로우

```bash
# 1. 브랜치 생성
git checkout -b feature/xxx

# 2. 작업 + 커밋 (자유롭게)
git commit -m "wip: ..."
git push origin feature/xxx     # private에 push (다른 PC 동기화용)

# 3. 완성되면 main에 merge
git checkout main
git merge feature/xxx
git push origin main            # private에 push

# 4. feature 브랜치 정리
git branch -d feature/xxx
git push origin --delete feature/xxx
```

## 배포 (public에 릴리스)

```bash
# main에서 squash하여 정갈한 커밋으로 배포
git checkout main

# 방법 A: 최근 N개 커밋을 squash
git reset --soft HEAD~N
git commit -m "feat: v0.2.0 — 주요 변경 설명"

# 방법 B: 또는 그냥 현재 main을 push (커밋이 이미 정리된 경우)

git push public main
```

**주의:**
- public에는 **main만** push 가능 (pre-push hook이 차단)
- public push 전에 커밋 히스토리를 정리하세요
- 실수로 public에 push한 경우: `git push public --delete <branch>`

## pre-push hook

`scripts/hooks/pre-push`에 있고, `setup-dev.sh`가 `.git/hooks/`에 설치합니다.
public remote에 main 외 브랜치 push를 차단합니다.

## 커밋 메시지 규칙

```
feat: 새 기능
fix: 버그 수정
refactor: 리팩터링
docs: 문서
chore: 유지보수
simplify: 간소화
```
