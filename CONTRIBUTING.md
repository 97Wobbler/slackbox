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

## 브랜치 구조

```
dev/x.y.z     ← 개발 브랜치 (버전별). 여기서 작업.
main          ← 배포용. squash하여 정갈한 커밋만.
```

## 개발 워크플로우

```bash
# 1. 개발 브랜치에서 작업
git checkout dev/0.1.0
# ... 작업 + 커밋
git push origin dev/0.1.0     # private에 push

# 2. 새 기능이 커지면 하위 브랜치 생성
git checkout -b dev/0.1.0/search-tool
# ... 작업 후 dev/0.1.0에 merge
git checkout dev/0.1.0
git merge dev/0.1.0/search-tool
git branch -d dev/0.1.0/search-tool
```

## 배포 (public에 릴리스)

```bash
git checkout main

# dev 브랜치의 변경사항을 squash merge
git merge --squash dev/0.1.0
git commit -m "feat: v0.1.0 — 초기 릴리스"

git push public main
```

**주의:**
- public에는 **main만** push 가능 (pre-push hook이 차단)
- public push 전에 커밋 히스토리를 정리하세요

## pre-push hook

`scripts/hooks/pre-push`에 있고, `setup-dev.sh`가 `.git/hooks/`에 설치합니다.
public remote에 main 외 브랜치 push를 차단합니다.

## 커밋 메시지 규칙

```
feat: 새 기능                    feat(Q1): list_users tool 추가
fix: 버그 수정                   fix(D1): 스레드 공유 캐시
refactor: 리팩터링               refactor(R1a): formatting.py 분리
docs: 문서
chore: 유지보수
simplify: 간소화
```

scope `(이슈ID)`는 선택사항. 복수 이슈는 콤마 구분: `feat(Q1,Q2,Q3)`.
