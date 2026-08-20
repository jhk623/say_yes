#!/usr/bin/env python3
"""UserPromptSubmit hook: 새 턴이 시작됐다고 relay에 알린다. 나중에 idle_prompt
알림이 왔을 때 "정상적으로 끝난 뒤의 유휴"인지 "뭔가에 막혀서 멈춘 것"인지
구분하는 기준으로 쓰인다 (hooks/notification.py, SPEC.md 참고).
fire-and-forget, 실패해도 Claude Code 동작에 영향 없음."""
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
        post_json(relay_url(config, "/turn_start"), request, timeout=3)
    except Exception:
        pass


if __name__ == "__main__":
    main()
