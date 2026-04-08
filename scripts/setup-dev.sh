#!/usr/bin/env bash
# 개발 환경 세팅: live remote 추가 + pre-push hook 설치
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# 1. live remote 추가 (이미 있으면 skip)
if git remote get-url live &>/dev/null; then
    echo "live remote 이미 설정됨: $(git remote get-url live)"
else
    git remote add live https://github.com/97Wobbler/slackbox.git
    echo "live remote 추가 완료"
fi

# 2. pre-push hook 설치
cp scripts/hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
echo "pre-push hook 설치 완료 (public에 main 외 push 차단)"

# 3. 상태 확인
echo ""
echo "=== Remote 설정 ==="
git remote -v
echo ""
echo "setup 완료. CONTRIBUTING.md에서 운영 방식을 확인하세요."
