"""MCP 서버 엔트리포인트 — 의존성 자동 설치 후 서버 실행."""
import subprocess
import sys


def ensure_dependencies():
    """필수 Python 패키지가 없으면 자동 설치."""
    required = ["slack_sdk", "mcp", "dotenv", "certifi", "tzdata"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    # Windows: tzdata가 import는 되지만 IANA DB가 없는 경우 체크
    if "tzdata" not in missing:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("Asia/Seoul")
        except (KeyError, Exception):
            missing.append("tzdata")

    if missing:
        # pip 패키지 이름 매핑 (import 이름 != pip 이름)
        pip_names = {
            "slack_sdk": "slack-sdk",
            "mcp": "mcp",
            "dotenv": "python-dotenv",
            "certifi": "certifi",
            "tzdata": "tzdata",
        }
        to_install = [pip_names.get(m, m) for m in missing]
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "--force-reinstall"]
            + to_install,
        )


if __name__ == "__main__":
    ensure_dependencies()
    from slack_fetch.mcp_server import main
    main()
