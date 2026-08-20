"""hooks/*.py가 공유하는 최소 유틸리티.

원격 서버에는 이 hooks/ 폴더만 있으면 되고, 표준 라이브러리만 사용하므로
pip install이 전혀 필요 없다 (PyYAML도 pyte도 불필요 — JSON 설정 파일만 사용).
"""
import json
import os
import socket
import sys
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    override = os.environ.get("AUTO_YES_BOT_HOOK_CONFIG")
    if override:
        path = override
    else:
        path = os.path.join(REPO_ROOT, "config", "hook.json")
        if not os.path.exists(path):
            path = os.path.join(REPO_ROOT, "config", "hook.example.json")
    return load_json(path)


def load_allowlist(config: dict) -> dict:
    name = config.get("allowlist_file")
    if not name:
        return {}
    path = name if os.path.isabs(name) else os.path.join(REPO_ROOT, "config", name)
    if not os.path.exists(path):
        return {}
    return load_json(path)


def read_stdin_json() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def relay_url(config: dict, path: str) -> str:
    return f"http://{config['relay_host']}:{config['relay_port']}{path}"


def post_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_label(config: dict, payload_in: dict) -> str:
    """세션 라벨을 만든다. hook.json에 "label"을 명시적으로 지정해두면 그걸
    그대로 쓰고, 아니면 "호스트명:작업 디렉터리"로 자동 조합한다 — 여러 서버를
    동시에 쓸 때 웹 UI/알림에서 어느 서버 세션인지 구분하기 위해서다."""
    override = config.get("label")
    if override:
        return override
    cwd = payload_in.get("cwd", "")
    hostname = socket.gethostname()
    return f"{hostname}:{cwd}" if cwd else hostname
