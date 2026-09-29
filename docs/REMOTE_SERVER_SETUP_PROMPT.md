# (재사용용 보관 문서) 다른 원격 서버에 auto-yes-bot hooks 설치 — Claude Code에게 줄 지시문

아래 "=== 여기부터 ===" ~ "=== 여기까지 ===" 사이 전체를, 새 서버에서 돌아가는
Claude Code 세션에게 그대로 붙여넣어서 시키면 된다.

=== 여기부터 ===

당신은 지금 이 서버에서 실행되고 있는 Claude Code에 "원격 승인 시스템"을
설치하는 작업을 맡았습니다. 이미 다른 서버(들)에서 같은 시스템이 운영 중이고,
당신은 거기에 새 서버 하나를 추가하는 것뿐입니다. 아래 순서대로 정확히
진행해주세요.

## 배경 (참고만 하세요)

- 이 시스템은 Claude Code의 `PreToolUse`/`SessionStart`/`SessionEnd`/
  `UserPromptSubmit`/`Stop`/`Notification` hook을 이용해서, 도구 실행 승인이
  필요할 때 사용자의 휴대폰으로 알림을 보내고 승인/거부를 받아옵니다.
- hook 스크립트들은 사용자의 로컬 PC에서 돌고 있는 "Local Relay Agent"라는
  프로그램에 HTTP 요청을 보내는데, 그 Local Relay Agent는 이미 다른 서버용으로
  설정되어 있고 그대로 재사용합니다. **비밀키나 ntfy 설정 같은 건 이 서버에
  전혀 필요 없습니다** — hook 쪽 설정 파일에는 relay의 주소/포트만 있으면
  됩니다.
- 이 hook들은 실패해도(네트워크 문제 등) 항상 조용히 아무 출력 없이 종료되도록
  만들어져 있어서, 설치가 잘못돼도 Claude Code 사용 자체가 막히는 일은 없습니다.
  안심하고 진행하세요.

## 0단계: 설치 위치 확인

`$HOME/auto_yes_bot` 에 설치합니다. 아래 명령으로 실제 경로를 확인하고, 이후
모든 단계에서 이 경로를 그대로 쓰세요 (홈 디렉터리가 다르면 알아서 치환).

```bash
echo "설치 경로: $HOME/auto_yes_bot"
mkdir -p "$HOME/auto_yes_bot/hooks" "$HOME/auto_yes_bot/config"
python3 --version   # 3.x 버전이 나오는지 확인. 안 나오면 python3가 있는 다른 경로를 찾아서 이후 명령의 python3를 그걸로 바꾸세요.
```

## 1단계: hooks/ 파일 7개 생성

아래 각 파일을 `$HOME/auto_yes_bot/hooks/` 아래에 **정확히 이 내용 그대로**
만드세요 (Write 도구 등으로).

### `hooks/common.py`

```python
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
```

### `hooks/pre_tool_use.py`

```python
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
```

### `hooks/session_start.py`

```python
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
```

### `hooks/session_end.py`

```python
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
```

### `hooks/user_prompt_submit.py`

```python
#!/usr/bin/env python3
"""UserPromptSubmit hook: 새 턴이 시작됐다고 relay에 알린다. 나중에 idle_prompt
알림이 왔을 때 "정상적으로 끝난 뒤의 유휴"인지 "뭔가에 막혀서 멈춘 것"인지
구분하는 기준으로 쓰인다.
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
```

### `hooks/stop.py`

```python
#!/usr/bin/env python3
"""Stop hook: 이번 턴이 정상적으로(더 이상 할 일 없이) 끝났다고 relay에 알린다.
hooks/notification.py의 idle_prompt 휴리스틱에서 "정상 종료 뒤의 유휴"와
"뭔가에 막혀서 멈춘 것"을 구분하는 기준으로 쓰인다. 여기에 응답해서 relay가
"응답 완료" 알림도 함께 보낸다.
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
        post_json(relay_url(config, "/turn_stop"), request, timeout=3)
    except Exception:
        pass


if __name__ == "__main__":
    main()
```

### `hooks/notification.py`

```python
#!/usr/bin/env python3
"""Notification hook: Claude Code가 사용자 주의를 필요로 할 때마다 호출된다
(권한 승인 대기, 60초 유휴 등 — notification_type으로 구분됨). 이 hook은
아무것도 결정하지 못하는 fire-and-forget이라, 여기서는 relay에 그대로
전달만 하고 판단은 relay가 한다.

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
```

## 2단계: config/hook.json 생성

`$HOME/auto_yes_bot/config/hook.json` 을 아래 내용으로 만드세요. **`relay_port`는
반드시 `18432`여야 합니다** (이미 운영 중인 로컬 relay가 그 포트에서 듣고
있습니다 — 임의로 바꾸지 마세요):

```json
{
  "relay_host": "127.0.0.1",
  "relay_port": 18432,

  "label": null,

  "http_timeout_sec": 3550,

  "watched_tools": ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch"],

  "allowlist_file": "hook_allowlist.json"
}
```

## 3단계: config/hook_allowlist.json 생성 (매번 안 물어봐도 되는 명령)

`$HOME/auto_yes_bot/config/hook_allowlist.json` 을 아래 내용으로 만드세요
(필요하면 나중에 패턴을 더 추가할 수 있습니다 — 각 정규식은 도구 호출의
`tool_input`을 JSON으로 직렬화한 문자열에 대해 매치됩니다):

```json
{
  "patterns_by_tool": {
    "Bash": [
      "\"command\"\\s*:\\s*\"git status",
      "\"command\"\\s*:\\s*\"git diff",
      "\"command\"\\s*:\\s*\"ls\\b"
    ]
  }
}
```

## 4단계: hook 스크립트 문법 확인

```bash
python3 -m py_compile "$HOME/auto_yes_bot/hooks/"*.py && echo "SYNTAX OK"
python3 -c "import json; json.load(open('$HOME/auto_yes_bot/config/hook.json')); json.load(open('$HOME/auto_yes_bot/config/hook_allowlist.json')); print('JSON OK')"
```

두 명령 다 에러 없이 "OK"가 나와야 합니다. 에러가 나면 해당 파일을 다시
확인해서 고치세요 (원본과 다르게 타이핑됐을 가능성이 큽니다).

## 5단계: `~/.claude/settings.json`에 hook 등록

**주의**: `~/.claude/settings.json` 파일이 이미 존재할 수 있습니다. 먼저
확인하세요:

```bash
cat ~/.claude/settings.json 2>/dev/null || echo "(파일 없음, 새로 만들면 됨)"
```

- **파일이 없으면**: 아래 내용을 그대로 `~/.claude/settings.json`으로 만드세요
  (단, `$HOME`은 실제 절대 경로로 바꿔서 — 예를 들어 `python3 $HOME/auto_yes_bot/...`가
  아니라 `python3 /실제/홈/경로/auto_yes_bot/...` 처럼 완전한 절대경로로 넣으세요).
- **파일이 이미 있으면**: 기존 내용의 다른 키(설정)는 그대로 두고, `"hooks"` 키
  안에 아래 6개 hook 항목만 **추가/병합**하세요. 기존에 이미 같은 이벤트(예:
  `PreToolUse`)에 다른 hook이 등록되어 있다면, 배열 안에 새 항목을 **추가**하는
  것이지 기존 것을 지우면 안 됩니다.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /실제/홈/경로/auto_yes_bot/hooks/pre_tool_use.py",
            "timeout": 3600
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /실제/홈/경로/auto_yes_bot/hooks/session_start.py",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /실제/홈/경로/auto_yes_bot/hooks/session_end.py",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /실제/홈/경로/auto_yes_bot/hooks/user_prompt_submit.py",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /실제/홈/경로/auto_yes_bot/hooks/stop.py",
            "timeout": 5
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /실제/홈/경로/auto_yes_bot/hooks/notification.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

저장한 뒤 JSON 문법이 올바른지 확인하세요:

```bash
python3 -c "import json; json.load(open(__import__('os').path.expanduser('~/.claude/settings.json'))); print('VALID')"
```

## 6단계: 안전성 확인 (지금은 relay 터널이 아직 없어도 됨)

아래처럼 실행했을 때 **에러 없이 즉시 조용히 끝나야** 정상입니다 (relay에
연결이 안 되니 아무 출력도 없이 종료하는 게 맞는 동작입니다 — 이게 "안전
기본값"입니다):

```bash
echo '{"session_id":"install-check","tool_name":"Bash","tool_input":{"command":"echo hi"},"cwd":"'"$HOME"'","tool_use_id":"t1"}' \
  | AUTO_YES_BOT_HOOK_CONFIG="$HOME/auto_yes_bot/config/hook.json" python3 "$HOME/auto_yes_bot/hooks/pre_tool_use.py"
echo "종료 코드: $?"
```

`종료 코드: 0`이고 그 위에 다른 출력이 없으면 정상입니다.

## 7단계: 완료 보고

다음 내용을 사용자에게 요약해서 보고하세요:
- 설치 경로: `$HOME/auto_yes_bot`
- `~/.claude/settings.json`이 원래 있었는지, 새로 만들었는지, 병합했는지
- 6단계 확인이 통과했는지
- **아직 안 되는 것**: 이 서버에서 로컬 PC로 가는 SSH 역방향 터널이 아직 안
  열려 있어서, 실제로 폰까지 알림이 가려면 **로컬 PC에서** 아래 명령을 별도
  터미널 창에서 실행해야 합니다 (이건 당신이 원격 서버에서 할 수 있는 일이
  아니고, 사용자가 로컬 Windows PC에서 직접 해야 합니다):
  ```
  ssh -N -R 18432:127.0.0.1:18432 <이 서버에 접속할 때 쓰는 SSH Host 이름 또는 user@ip>
  ```
  이 창을 relay.py 창, 기존 다른 서버용 터널 창과 함께 계속 열어둬야 합니다.

=== 여기까지 ===
