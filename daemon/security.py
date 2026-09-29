"""Reply/제어 메시지 서명 및 검증.

핵심 설계(§13.1): PendingRequest(notify) 자체에는 서명이 필요 없다 — 그걸 읽을 수
있다고 해서 어떤 행동을 취할 권한이 생기지 않기 때문이다. 대신 Reply와
SetAutoApprove처럼 실제로 세션에 영향을 주는 메시지만 비밀키로 서명해야 유효하다.

승인 결정은 항상 allow/deny 둘 중 하나뿐이므로(§ hooks 기반 재설계 — AskUserQuestion
자유 응답은 지원하지 않음), ntfy 알림의 두 버튼 모두 relay가 publish 시점에 이미
직접 서명해서 body에 박아넣는다 — 그래서 폰 쪽은 비밀키 없이도 버튼을 누를 수
있다. 컴패니언 웹 UI에서 세션의 auto-approve를 토글하는 것만 브라우저가 직접
서명해야 하므로, 그 기능을 쓰려면 최초 설정 시 비밀키를 웹 UI에 입력해둔다.
"""
import hashlib
import hmac


class Security:
    def __init__(self, secret: str):
        self.secret = secret.encode("utf-8")

    def sign_reply(self, request_id: str, decision: str) -> str:
        return self._sign(request_id, decision)

    def verify_reply(self, msg: dict) -> bool:
        expected = self.sign_reply(msg.get("request_id", ""), msg.get("decision", ""))
        return hmac.compare_digest(expected, str(msg.get("sig", "")))

    def sign_control(self, session_id: str, enabled: bool) -> str:
        # 웹 UI(JS)의 String(bool) 표기("true"/"false")와 맞추기 위해 소문자로 고정.
        return self._sign(session_id, "true" if enabled else "false")

    def verify_control(self, msg: dict) -> bool:
        expected = self.sign_control(msg.get("session_id", ""), msg.get("enabled", False))
        return hmac.compare_digest(expected, str(msg.get("sig", "")))

    def _sign(self, *parts: str) -> str:
        payload = "|".join(parts).encode("utf-8")
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
