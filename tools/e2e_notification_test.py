#!/usr/bin/env python3
"""idle_prompt 휴리스틱(hooks/notification.py + daemon/server.py) 스모크 테스트.

시나리오:
  1) turn_start 없이/턴 정상 종료(stop) 후 idle_prompt -> 알림 없어야 함
  2) turn_start 후 stop 없이 idle_prompt -> "확인 필요" 알림이 떠야 함
  3) pending_request가 떠 있는 동안의 idle_prompt -> 중복 알림 없어야 함
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY_PORT = 18795
SECRET = "e2e-notification-secret"
DRYRUN_STATE_DIR = os.path.join(REPO_ROOT, ".dryrun")
SESSION_ID = "e2e-notification-session"


def run_hook(script: str, stdin_payload: dict, env: dict, timeout: float = 5):
    subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "hooks", script)],
        input=json.dumps(stdin_payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
    )


def read_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    return json.loads(content) if content else None


def wait_until(predicate, timeout: float, description: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError(f"타임아웃: {description}")


def main() -> int:
    notify_path = os.path.join(DRYRUN_STATE_DIR, "notify.json")
    if os.path.exists(notify_path):
        os.remove(notify_path)

    workdir = tempfile.mkdtemp(prefix="ayb_notif_e2e_")
    relay_config_path = os.path.join(workdir, "relay.yaml")
    with open(relay_config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "listen_host": "127.0.0.1",
                "listen_port": RELAY_PORT,
                "hmac_secret": SECRET,
                "default_auto_approve": False,
                "renotify_interval_sec": None,
                "max_wait_sec": 30,
                "dry_run": True,
                "auto_approve_store_path": os.path.join(workdir, "auto_approve.json"),
            },
            f,
        )

    hook_config_path = os.path.join(workdir, "hook.json")
    with open(hook_config_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "relay_host": "127.0.0.1",
                "relay_port": RELAY_PORT,
                "label": "e2e-notification-test",
                "http_timeout_sec": 10,
                "watched_tools": ["Bash"],
                "allowlist_file": "hook_allowlist.example.json",
            },
            f,
        )

    print("=== relay.py (dry-run) 기동 ===")
    relay_proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO_ROOT, "daemon", "relay.py"), "--config", relay_config_path],
        cwd=REPO_ROOT,
    )
    time.sleep(1.5)

    env = dict(os.environ)
    env["AUTO_YES_BOT_HOOK_CONFIG"] = hook_config_path

    ok = False
    try:
        print("\n--- [0] 세션 등록 ---")
        run_hook("session_start.py", {"session_id": SESSION_ID, "cwd": "/tmp/e2e"}, env)

        print("--- [1] 턴 시작 -> 정상 종료(stop) -> idle_prompt: 알림 없어야 함 ---")
        run_hook("user_prompt_submit.py", {"session_id": SESSION_ID, "cwd": "/tmp/e2e"}, env)
        time.sleep(0.3)
        run_hook("stop.py", {"session_id": SESSION_ID, "cwd": "/tmp/e2e"}, env)
        time.sleep(0.3)
        run_hook(
            "notification.py",
            {"session_id": SESSION_ID, "cwd": "/tmp/e2e", "notification_type": "idle_prompt"},
            env,
        )
        time.sleep(1)
        after_normal_stop = read_json(notify_path)
        assert after_normal_stop is None or after_normal_stop.get("type") != "attention_needed", (
            f"정상 종료 뒤인데도 attention_needed 알림이 발생: {after_normal_stop}"
        )
        print("[OK] 정상 종료 뒤의 idle_prompt는 무시됨")

        print("\n--- [2] 턴 시작만 하고 stop 없이 idle_prompt -> 알림 떠야 함 ---")
        run_hook("user_prompt_submit.py", {"session_id": SESSION_ID, "cwd": "/tmp/e2e"}, env)
        time.sleep(0.3)
        run_hook(
            "notification.py",
            {"session_id": SESSION_ID, "cwd": "/tmp/e2e", "notification_type": "idle_prompt"},
            env,
        )
        payload = wait_until(
            lambda: (lambda p: p if p and p.get("type") == "attention_needed" else None)(
                read_json(notify_path)
            ),
            timeout=5,
            description="attention_needed 알림 발생",
        )
        assert payload["session_id"] == SESSION_ID, payload
        print(f"[OK] attention_needed 알림 확인: {payload}")

        print("\n--- [3] pending_request가 떠 있는 동안 idle_prompt -> 중복 알림 없어야 함 ---")
        # 새 턴 시작 (stop 없이) 후, pending_request를 만들어 세션을 'pending' 상태로 만든다.
        run_hook("user_prompt_submit.py", {"session_id": SESSION_ID, "cwd": "/tmp/e2e"}, env)
        pending_proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO_ROOT, "hooks", "pre_tool_use.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        pending_proc.stdin.write(
            json.dumps(
                {
                    "session_id": SESSION_ID,
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo pending-during-idle"},
                    "cwd": "/tmp/e2e",
                    "tool_use_id": "t1",
                }
            ).encode("utf-8")
        )
        pending_proc.stdin.close()

        wait_until(
            lambda: (lambda p: p if p and p.get("type") == "pending_request" else None)(
                read_json(notify_path)
            ),
            timeout=5,
            description="pending_request 알림 발생",
        )
        before = read_json(notify_path)
        run_hook(
            "notification.py",
            {"session_id": SESSION_ID, "cwd": "/tmp/e2e", "notification_type": "idle_prompt"},
            env,
        )
        time.sleep(1)
        after = read_json(notify_path)
        assert after == before, f"pending 중인데도 notify.json이 바뀜 (중복 알림 의심): before={before} after={after}"
        print("[OK] pending 중에는 idle_prompt로 중복 알림이 안 감")

        pending_proc.kill()

        ok = True
    finally:
        relay_proc.terminate()
        try:
            relay_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            relay_proc.kill()

    print("\n모든 notification e2e 검증 통과!" if ok else "\n검증 실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
