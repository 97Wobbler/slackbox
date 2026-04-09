"""MCP 서버 엔트리포인트 — 의존성 자동 설치 후 서버 실행."""
import subprocess
import sys


def ensure_dependencies():
    """필수 Python 패키지가 없으면 자동 설치."""
    required = ["slack_sdk", "mcp", "dotenv", "certifi"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        # pip 패키지 이름 매핑 (import 이름 != pip 이름)
        pip_names = {
            "slack_sdk": "slack-sdk",
            "mcp": "mcp",
            "dotenv": "python-dotenv",
            "certifi": "certifi",
        }
        to_install = [pip_names.get(m, m) for m in missing]
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + to_install,
        )


if __name__ == "__main__":
    ensure_dependencies()
    from slack_fetch.mcp_server import main
    main()
