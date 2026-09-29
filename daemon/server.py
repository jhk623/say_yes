"""Local Relay Agent 핵심 로직.

원격 서버의 hooks/*.py가 보내는 요청(HTTP, SSH RemoteForward를 통해 도달)을 받아
ntfy(또는 dry-run)로 중계한다. PreToolUse hook 하나당 요청 하나가 오고, 사람이
응답(폰 또는 auto-approve)할 때까지 그 HTTP 요청 자체를 들고 있다가 응답한다 —
그래서 세션 상태는 소켓이 아니라 "마지막으로 이 session_id에서 뭔가 왔던 시각"
기준으로 관리한다.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from .store import JsonStore


class Session:
    def __init__(self, session_id: str, label: str):
        self.session_id = session_id
        self.label = label
        self.status = "idle"
        self.auto_approve = False
        self.pending_request_id = None
        self.last_seen = time.time()
        # idle_prompt 휴리스틱(§ hooks/notification.py)에 쓰는 턴 시작/종료 시각.
        # 시작 시각이 종료 시각보다 최신이면 "턴이 안 끝난 채로 멈춰있다"는 뜻.
        self.last_turn_started_at = 0.0
        self.last_turn_stopped_at = 0.0


class Relay:
    def __init__(self, config: dict, notifier, security):
        self.config = config
        self.notifier = notifier
        self.security = security
        self.sessions: dict[str, Session] = {}
        # request_id -> {"session_id": ..., "future": asyncio.Future}
        self.pending: dict[str, dict] = {}
        self.default_auto_approve = bool(config.get("default_auto_approve", False))
        self.renotify_interval = config.get("renotify_interval_sec")
        self.max_wait_sec = config.get("max_wait_sec", 3600)
        self.auto_approve_store = JsonStore(
            config.get("auto_approve_store_path", "auto_approve.json")
        )

    def _get_or_create_session(self, session_id: str, label: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            session = Session(session_id, label or session_id)
            persisted = self.auto_approve_store.get(session_id)
            session.auto_approve = (
                bool(persisted) if persisted is not None else self.default_auto_approve
            )
            self.sessions[session_id] = session
        else:
            session.last_seen = time.time()
            if label:
                session.label = label
        return session

    # ---- hooks/*.py 쪽 (HTTP) ----
    async def on_session_start(self, msg: dict) -> None:
        self._get_or_create_session(msg.get("session_id", "unknown"), msg.get("label", ""))
        await self._publish_state()

    async def on_session_end(self, msg: dict) -> None:
        self.sessions.pop(msg.get("session_id", ""), None)
        await self._publish_state()

    async def on_turn_start(self, msg: dict) -> None:
        session = self._get_or_create_session(msg.get("session_id", "unknown"), msg.get("label", ""))
        session.last_turn_started_at = time.time()

    async def on_turn_stop(self, msg: dict) -> None:
        session = self._get_or_create_session(msg.get("session_id", "unknown"), msg.get("label", ""))
        session.last_turn_stopped_at = time.time()
        await self.notifier.publish_turn_completed(session)

    async def on_notification(self, msg: dict) -> None:
        if msg.get("notification_type") != "idle_prompt":
            return  # 이 휴리스틱은 idle_prompt(60초 무응답)에만 적용한다.
        session = self.sessions.get(msg.get("session_id", ""))
        if session is None:
            return
        if session.pending_request_id:
            return  # 이미 우리 시스템의 승인 알림이 떠 있음 -> 중복 알림 방지
        if session.last_turn_started_at <= session.last_turn_stopped_at:
            return  # 턴이 정상 종료된 뒤의 유휴 상태 -> 알릴 필요 없음
        await self.notifier.publish_attention_needed(session)

    async def on_pending(self, msg: dict) -> dict:
        session_id = msg.get("session_id", "unknown")
        session = self._get_or_create_session(session_id, msg.get("label", ""))

        summary = self._summarize(msg)

        if session.auto_approve:
            notify_payload = {
                "type": "pending_request",
                "request_id": _new_request_id(),
                "session_id": session_id,
                "session_label": session.label,
                "summary": summary,
                "tool_name": msg.get("tool_name"),
                "created_at": time.time(),
                "auto_resolved": True,
            }
            await self.notifier.publish_notify(notify_payload)
            return {"decision": "allow"}

        request_id = _new_request_id()
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self.pending[request_id] = {"session_id": session_id, "future": future}
        session.pending_request_id = request_id
        session.status = "pending"

        notify_payload = {
            "type": "pending_request",
            "request_id": request_id,
            "session_id": session_id,
            "session_label": session.label,
            "summary": summary,
            "tool_name": msg.get("tool_name"),
            "created_at": time.time(),
            "auto_resolved": False,
        }
        await self.notifier.publish_notify(notify_payload)
        await self._publish_state()
        if self.renotify_interval:
            asyncio.create_task(self._renotify_loop(request_id, notify_payload))

        try:
            decision = await asyncio.wait_for(future, timeout=self.max_wait_sec)
        except asyncio.TimeoutError:
            decision = "defer"
        finally:
            self.pending.pop(request_id, None)
            if session.pending_request_id == request_id:
                session.pending_request_id = None
            session.status = "idle"
            await self._publish_state()

        return {"decision": decision}

    def _summarize(self, msg: dict) -> str:
        tool_name = msg.get("tool_name", "")
        tool_input = msg.get("tool_input") or {}
        if tool_name == "Bash":
            return tool_input.get("command", "")
        if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            return tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
        if tool_name == "WebFetch":
            return tool_input.get("url", "")
        return str(tool_input)[:500]

    async def _renotify_loop(self, request_id: str, payload: dict) -> None:
        while True:
            await asyncio.sleep(self.renotify_interval)
            if request_id not in self.pending:
                return
            await self.notifier.publish_notify({**payload, "renotify": True})

    # ---- 폰/웹 UI 쪽 (ntfy reply 토픽) ----
    async def on_phone_message(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "reply":
            await self._on_reply(msg)
        elif mtype == "set_auto_approve":
            await self._on_set_auto_approve(msg)

    async def _on_reply(self, msg: dict) -> None:
        request_id = msg.get("request_id")
        entry = self.pending.get(request_id)
        if not entry:
            return
        if not self.security.verify_reply(msg):
            print(f"[relay] 서명 검증 실패한 reply 무시: {request_id}")
            return
        decision = msg.get("decision")
        if decision not in ("allow", "deny"):
            return
        future = entry["future"]
        if not future.done():
            future.set_result(decision)

    async def _on_set_auto_approve(self, msg: dict) -> None:
        if not self.security.verify_control(msg):
            print("[relay] 서명 검증 실패한 set_auto_approve 무시")
            return
        target = msg.get("session_id", "*")
        enabled = bool(msg.get("enabled"))
        targets = (
            list(self.sessions.values())
            if target == "*"
            else [s for s in self.sessions.values() if s.session_id == target]
        )
        for session in targets:
            session.auto_approve = enabled
            self.auto_approve_store.set(session.session_id, enabled)
        await self._publish_state()

    async def _publish_state(self) -> None:
        sessions = [
            {
                "session_id": s.session_id,
                "label": s.label,
                "status": s.status,
                "pending_request_id": s.pending_request_id,
                "auto_approve": s.auto_approve,
            }
            for s in self.sessions.values()
        ]
        await self.notifier.publish_state(
            {"type": "state_snapshot", "sessions": sessions, "generated_at": time.time()}
        )


def _new_request_id() -> str:
    return str(uuid.uuid4())
