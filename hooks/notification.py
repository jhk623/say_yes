#!/usr/bin/env python3
"""Notification hook: Claude Code가 사용자 주의를 필요로 할 때마다 호출된다
(권한 승인 대기, 60초 유휴 등 — notification_type으로 구분됨). 이 hook은
아무것도 결정하지 못하는 fire-and-forget이라, 여기서는 relay에 그대로
전달만 하고 판단은 relay(daemon/server.py)가 한다:

  idle_prompt(60초 무응답)인데 (1) 이미 우리 시스템의 승인 알림이 떠 있지도
  않고 (2) 이번 턴이 정상 종료(Stop)되지도 않은 채 멈춰있다면 —
  AskUserQuestion처럼 hooks로는 답할 수 없는 뭔가에 막혀있을 가능성이 높다고
  보고, "Antigravity를 직접 확인해달라"는 별도 알림을 보낸다 (SPEC.md 참고).

실패해도 Claude Code 동작에 영향 없음."""
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
        "notification_type": payload_in.get("notification_type"),
    }
    try:
        post_json(relay_url(config, "/notification"), request, timeout=3)
    except Exception:
        pass


if __name__ == "__main__":
    main()
