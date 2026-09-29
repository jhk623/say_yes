"""ntfy 기반 알림 전송/수신, 그리고 로컬 테스트용 ConsoleNotifier."""
import asyncio
import json
import os
import time

import aiohttp


class NtfyNotifier:
    """실제 ntfy 서버(self-host 또는 ntfy.sh)와 통신한다."""

    def __init__(self, server: str, topic_prefix: str, security):
        self.server = server.rstrip("/")
        self.notify_topic = f"{topic_prefix}-notify"
        self.reply_topic = f"{topic_prefix}-reply"
        self.state_topic = f"{topic_prefix}-state"
        self.security = security
        self._session = None

    async def start(self, on_phone_message) -> None:
        self._session = aiohttp.ClientSession()
        asyncio.create_task(self._subscribe_loop(on_phone_message))

    async def publish_notify(self, payload: dict) -> None:
        await self._publish(
            self.notify_topic,
            payload,
            title=self._title(payload),
            actions=self._actions(payload),
        )

    async def publish_state(self, payload: dict) -> None:
        await self._publish(self.state_topic, payload)

    async def publish_attention_needed(self, session) -> None:
        """승인/거부로 답할 수 없는 뭔가(AskUserQuestion 등)에 막혀있는 것 같을 때
        보내는 정보성 알림. 버튼이 없다 — 그냥 Antigravity를 열어봐야 한다는 신호."""
        payload = {
            "type": "attention_needed",
            "session_id": session.session_id,
            "session_label": session.label,
            "created_at": time.time(),
        }
        await self._publish(
            self.notify_topic, payload, title=f"🔔 확인 필요 — {session.label}"
        )

    async def publish_turn_completed(self, session) -> None:
        """Claude가 이번 턴 응답을 끝내고 사용자 입력을 기다리는 상태가 될 때마다
        보내는 정보성 알림 — 다음 작업을 지시할 수 있다는 신호. 버튼은 없다."""
        payload = {
            "type": "turn_completed",
            "session_id": session.session_id,
            "session_label": session.label,
            "created_at": time.time(),
        }
        await self._publish(
            self.notify_topic, payload, title=f"✅ 응답 완료 — {session.label}"
        )

    async def _publish(self, topic: str, payload: dict, title=None, actions=None) -> None:
        body = {"topic": topic, "message": json.dumps(payload, ensure_ascii=False)}
        if title:
            body["title"] = title
        if actions:
            body["actions"] = actions
        try:
            async with self._session.post(
                self.server + "/", json=body, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    print(f"[ntfy] publish 실패({topic}): HTTP {resp.status} {text}")
        except Exception as exc:  # noqa: BLE001 - 알림 실패는 세션을 막으면 안 됨
            print(f"[ntfy] publish 실패({topic}): {exc!r}")

    async def _subscribe_loop(self, on_phone_message) -> None:
        url = f"{self.server}/{self.reply_topic}/json"
        backoff = 1.0
        while True:
            try:
                async with self._session.get(
                    url, timeout=aiohttp.ClientTimeout(total=None)
                ) as resp:
                    backoff = 1.0
                    async for raw_line in resp.content:
                        line = raw_line.strip()
                        if not line:
                            continue
                        envelope = self._safe_json(line)
                        if not envelope or envelope.get("event") != "message":
                            continue
                        msg = self._safe_json(envelope.get("message", "").encode("utf-8"))
                        if msg:
                            await on_phone_message(msg)
            except Exception as exc:  # noqa: BLE001
                print(f"[ntfy] 구독 재접속 예정: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    @staticmethod
    def _safe_json(raw: bytes):
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _title(self, payload: dict) -> str:
        if payload.get("type") == "pending_request":
            prefix = "[자동 승인됨]" if payload.get("auto_resolved") else "[승인 필요]"
            return f"{prefix} {payload.get('session_label', '')}"
        return "auto-yes-bot"

    def _actions(self, payload: dict):
        """승인/거부 버튼에 미리 서명된 응답 body를 박아넣는다 (§security.py 설명).
        결정은 항상 allow/deny 둘 중 하나뿐이라 옵션 개수를 고려할 필요가 없다."""
        if payload.get("type") != "pending_request" or payload.get("auto_resolved"):
            return None

        request_id = payload["request_id"]
        actions = []
        for decision, label in (("allow", "승인"), ("deny", "거부")):
            sig = self.security.sign_reply(request_id, decision)
            reply_body = json.dumps(
                {"type": "reply", "request_id": request_id, "decision": decision, "sig": sig},
                ensure_ascii=False,
            )
            actions.append(
                {
                    "action": "http",
                    "label": label,
                    "url": f"{self.server}/{self.reply_topic}",
                    "method": "POST",
                    "body": reply_body,
                }
            )
        return actions


class ConsoleNotifier:
    """실제 ntfy/폰 없이 로컬에서 왕복을 테스트하기 위한 드라이런 구현.
    콘솔/파일에 알림을 출력하고, relay.py가 등록하는 /dryrun/message HTTP 라우트를
    통해 '폰 응답'을 주입받는다 (별도 포트 없이 메인 relay 앱에 얹힌다)."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self._on_phone_message = None

    async def start(self, on_phone_message) -> None:
        self._on_phone_message = on_phone_message

    async def inject(self, msg: dict) -> None:
        if self._on_phone_message:
            await self._on_phone_message(msg)

    async def publish_notify(self, payload: dict) -> None:
        self._write("notify", payload)
        print(f"\n[dry-run NOTIFY]\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n")

    async def publish_state(self, payload: dict) -> None:
        self._write("state", payload)

    async def publish_attention_needed(self, session) -> None:
        payload = {
            "type": "attention_needed",
            "session_id": session.session_id,
            "session_label": session.label,
            "created_at": time.time(),
        }
        self._write("notify", payload)
        print(f"\n[dry-run ATTENTION]\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n")

    async def publish_turn_completed(self, session) -> None:
        payload = {
            "type": "turn_completed",
            "session_id": session.session_id,
            "session_label": session.label,
            "created_at": time.time(),
        }
        self._write("notify", payload)
        print(f"\n[dry-run TURN_COMPLETED]\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n")

    def _write(self, kind: str, payload: dict) -> None:
        path = os.path.join(self.state_dir, f"{kind}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
