#!/usr/bin/env python3
"""Claude Code의 PreToolUse hook.

.claude/settings.local.json에 등록해두면, 감시 대상 도구(watched_tools)를 호출하기
직전에 Claude Code가 이 스크립트를 실행하고 stdin으로 요청 정보를 준 뒤, stdout에
찍힌 JSON(hookSpecificOutput.permissionDecision)을 기다린다. 이 스크립트는:

  1. hook_allowlist에 매치되면 즉시 통과(아무 출력 없이 종료 -> Claude Code의
     원래 권한 처리로 위임, 즉 폰에 알리지 않고 조용히 넘어간다).
  2. 아니면 Local Relay Agent에 승인 요청을 보내고, 사람이 응답(폰 또는
     auto-approve)할 때까지 기다린다.
  3. relay가 명확히 allow/deny를 응답하면 그대로 permissionDecision으로 출력한다.
  4. 응답이 하나도 없으면(네트워크 문제, relay 다운, 타임아웃) 아무 것도 출력하지
     않고 조용히 종료한다 -> Claude Code 자체의 기본 동작(대개 Antigravity에서
     직접 물어보는 것)으로 자연스럽게 넘어간다. 이 hook이 죽어도 "자동으로
     허용"되는 경우는 없다 (안전 기본값).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (  # noqa: E402
    build_label,
    load_allowlist,
    load_config,
    post_json,
    read_stdin_json,
    relay_url,
)


def matches_allowlist(tool_name: str, tool_input: dict, allowlist: dict) -> bool:
    patterns = allowlist.get("patterns_by_tool", {}).get(tool_name, [])
    haystack = json.dumps(tool_input, ensure_ascii=False)
    return any(re.search(p, haystack) for p in patterns)


def emit_decision(decision: str, reason: str = None) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        output["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(output))


def main() -> None:
    payload_in = read_stdin_json()
    tool_name = payload_in.get("tool_name", "")
    tool_input = payload_in.get("tool_input", {})

    config = load_config()

    watched = config.get("watched_tools")
    if watched is not None and tool_name not in watched:
        return  # 감시 대상이 아님 -> 아무 출력 없이 기본 동작으로 위임

    allowlist = load_allowlist(config)
    if matches_allowlist(tool_name, tool_input, allowlist):
        return  # 로컬 허용 목록에 매치 -> 폰에 알리지 않고 조용히 통과

    request = {
        "session_id": payload_in.get("session_id", "unknown"),
        "label": build_label(config, payload_in),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": payload_in.get("cwd"),
        "tool_use_id": payload_in.get("tool_use_id"),
    }

    timeout = config.get("http_timeout_sec", 3500)
    try:
        result = post_json(relay_url(config, "/pending"), request, timeout=timeout)
    except Exception:
        return  # relay 무응답/타임아웃/에러 -> 조용히 기본 동작으로 위임

    decision = result.get("decision")
    if decision in ("allow", "deny"):
        emit_decision(decision, result.get("reason"))
    # 그 외 값(예: 명시적 "defer")이면 아무 것도 출력하지 않는다.


if __name__ == "__main__":
    main()
