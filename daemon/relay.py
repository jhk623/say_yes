#!/usr/bin/env python3
"""Local Relay Agent 진입점. 로컬 PC에서 상시 실행한다 (§4.2).

hooks/*.py가 SSH RemoteForward를 통해 도달하는 이 HTTP 서버에 요청을 보내고,
이 서버는 ntfy(또는 dry-run)로 중계한다.
"""
import argparse
import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    # 학교/회사 네트워크의 SSL 검사 프록시처럼, OS는 신뢰하지만 Python 기본
    # 인증서 목록(certifi)에는 없는 CA를 쓰는 경우를 위해 OS 인증서 저장소를
    # 그대로 쓰도록 한다 (설치 안 돼 있으면 조용히 기존 방식으로 동작).
    import truststore

    truststore.inject_into_ssl()
    print("[relay] truststore 적용됨 (OS 인증서 저장소 사용)")
except ImportError:
    print("[relay] truststore 미설치 — 기본 인증서 목록 사용")

import yaml  # noqa: E402
from aiohttp import web  # noqa: E402

from daemon.ntfy import ConsoleNotifier, NtfyNotifier  # noqa: E402
from daemon.security import Security  # noqa: E402
from daemon.server import Relay  # noqa: E402


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_notifier(config: dict, security: Security):
    if config.get("dry_run"):
        return ConsoleNotifier(state_dir=os.path.join(REPO_ROOT, ".dryrun"))
    ntfy_cfg = config["ntfy"]
    return NtfyNotifier(ntfy_cfg["server"], ntfy_cfg["topic_prefix"], security)


def build_app(relay: Relay, notifier) -> web.Application:
    app = web.Application()

    async def handle_pending(request: web.Request) -> web.Response:
        msg = await request.json()
        result = await relay.on_pending(msg)
        return web.json_response(result)

    async def handle_session_start(request: web.Request) -> web.Response:
        msg = await request.json()
        await relay.on_session_start(msg)
        return web.json_response({"ok": True})

    async def handle_session_end(request: web.Request) -> web.Response:
        msg = await request.json()
        await relay.on_session_end(msg)
        return web.json_response({"ok": True})

    async def handle_turn_start(request: web.Request) -> web.Response:
        msg = await request.json()
        await relay.on_turn_start(msg)
        return web.json_response({"ok": True})

    async def handle_turn_stop(request: web.Request) -> web.Response:
        msg = await request.json()
        await relay.on_turn_stop(msg)
        return web.json_response({"ok": True})

    async def handle_notification(request: web.Request) -> web.Response:
        msg = await request.json()
        await relay.on_notification(msg)
        return web.json_response({"ok": True})

    app.add_routes(
        [
            web.post("/pending", handle_pending),
            web.post("/session_start", handle_session_start),
            web.post("/session_end", handle_session_end),
            web.post("/turn_start", handle_turn_start),
            web.post("/turn_stop", handle_turn_stop),
            web.post("/notification", handle_notification),
        ]
    )

    if isinstance(notifier, ConsoleNotifier):

        async def handle_dryrun_inject(request: web.Request) -> web.Response:
            msg = await request.json()
            await notifier.inject(msg)
            return web.json_response({"ok": True})

        app.add_routes([web.post("/dryrun/message", handle_dryrun_inject)])

    return app


async def main_async(config: dict) -> None:
    config.setdefault(
        "auto_approve_store_path", os.path.join(REPO_ROOT, ".state", "auto_approve.json")
    )
    security = Security(config.get("hmac_secret", ""))
    notifier = build_notifier(config, security)
    relay = Relay(config, notifier, security)

    await notifier.start(relay.on_phone_message)

    app = build_app(relay, notifier)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config["listen_host"], config["listen_port"])
    await site.start()

    print(
        f"[relay] listening on {config['listen_host']}:{config['listen_port']} "
        f"(dry_run={config.get('dry_run', False)})"
    )
    await asyncio.Event().wait()


def default_config_path() -> str:
    real = os.path.join(REPO_ROOT, "config", "relay.yaml")
    if os.path.exists(real):
        return real
    return os.path.join(REPO_ROOT, "config", "relay.example.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="auto-yes-bot Local Relay Agent")
    parser.add_argument("--config", default=default_config_path())
    args = parser.parse_args()
    config = load_config(args.config)
    try:
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
