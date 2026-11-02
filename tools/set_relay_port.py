#!/usr/bin/env python3
"""원격 서버에서 실행: config/hook.json의 relay_port를 바꾸고, 그 포트로 relay가
실제로 응답하는지 확인한다. Windows 런처가 터널을 연 직후 ssh로 호출한다.

Usage: set_relay_port.py PORT
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_JSON = os.path.join(ROOT, "config", "hook.json")


def main() -> int:
    port = int(sys.argv[1])

    with open(HOOK_JSON, "r", encoding="utf-8") as f:
        text = f.read()
    host = json.loads(text).get("relay_host", "127.0.0.1")

    # 손으로 편집한 레이아웃(빈 줄 등)을 유지하려고 json.dump 대신 숫자만 치환한다.
    new_text, n = re.subn(r'("relay_port"\s*:\s*)\d+', rf"\g<1>{port}", text)
    if n == 0:
        print(f"FAIL: {HOOK_JSON}에 relay_port 항목이 없음")
        return 1
    if new_text != text:
        with open(HOOK_JSON, "w", encoding="utf-8") as f:
            f.write(new_text)

    # relay에는 GET 라우트가 없어서, 살아있으면 404/405가 돌아온다.
    try:
        urllib.request.urlopen(f"http://{host}:{port}/", timeout=5)
    except urllib.error.HTTPError:
        print(f"relay_port={port}, relay 응답 OK")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: relay_port={port}로 relay 응답 없음 ({exc})")
        return 1
    print(f"relay_port={port}, relay 응답 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
