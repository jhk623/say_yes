#!/usr/bin/env python3
"""SessionStart hook: 세션이 시작됐다고 relay에 알려서, 아직 승인 요청이 없어도
컴패니언 웹 UI의 세션 목록에 "연결됨"으로 뜨게 한다. 실패해도 Claude Code 동작에는
전혀 영향이 없다 (fire-and-forget, 아주 짧은 타임아웃)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import build_label, load_config, post_json, read_stdin_json, relay_url  # noqa: E402


def main() -> None:
    payload_in = read_stdin_json()
    config = load_config()
    request = {
        "session_id": payload_in.get("session_id", "unknown"),
        "label": build_label(config, payload_in),
    }
    try:
        post_json(relay_url(config, "/session_start"), request, timeout=3)
    except Exception:
        pass


if __name__ == "__main__":
    main()
