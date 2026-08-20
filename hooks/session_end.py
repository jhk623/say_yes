#!/usr/bin/env python3
"""SessionEnd hook: 세션이 끝났다고 relay에 알려서 세션 목록에서 지운다.
fire-and-forget, 실패해도 무시."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import load_config, post_json, read_stdin_json, relay_url  # noqa: E402


def main() -> None:
    payload_in = read_stdin_json()
    config = load_config()
    request = {"session_id": payload_in.get("session_id", "unknown")}
    try:
        post_json(relay_url(config, "/session_end"), request, timeout=3)
    except Exception:
        pass


if __name__ == "__main__":
    main()
