# auto-yes-bot

Claude Code(Antigravity 내장 에이전트 패널로 실행)의 도구 권한 요청(`PreToolUse`)을
감지해서 Android 휴대폰(ntfy)으로 알리고, 승인/거부를 폰에서 그대로 처리하는 도구.
설계 배경과 아키텍처는 [SPEC.md](./SPEC.md) 참고.

**중요한 제약**: `AskUserQuestion`(메뉴/자유 텍스트로 되묻는 도구)은 Claude Code의
공식 hooks API로는 가로챌 수 없어서 지원하지 않는다 (SPEC.md §2, §9). 이 도구는
승인/거부만 다룬다.

## 구성 요소

| 위치 | 무엇 | 실행 위치 |
|---|---|---|
| `hooks/` | PreToolUse/SessionStart/SessionEnd hook 스크립트 | **원격** 서버 (Claude Code가 실행되는 곳) |
| `daemon/` | Local Relay Agent (HTTP) | **로컬** PC (인터넷 접근 가능한 곳) |
| `web/` | 컴패니언 웹 UI (승인/거부, 세션 목록, auto-approve 토글) | GitHub Pages 등 정적 호스팅 |
| `tools/` | 로컬 테스트용 스크립트 (실제 ntfy/폰 불필요) | 어디서나 |

`hooks/`는 표준 라이브러리만 사용하므로 원격 서버에 **pip install이 전혀 필요
없다**. `daemon/`은 로컬 PC에서 `pip install -r requirements-relay.txt`만 하면 된다.

## 1. 로컬에서 먼저 검증하기 (실제 ntfy/폰 없이)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-relay.txt

python3 tools/e2e_hook_test.py
```

`relay.py`를 `dry_run: true`로 띄우고, 실제 `hooks/*.py` 스크립트를 서브프로세스로
직접 실행해서 세션 등록 → 승인 → 거부 → 로컬 allowlist 통과 → auto-approve →
무응답 시 fallback → 세션 해제까지 전체 왕복을 검증한다.

## 2. 배포

### 2.1 ntfy 준비

1. 랜덤 문자열 두 개 생성:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(16))"
   ```
   하나는 `topic_prefix`, 하나는 `hmac_secret`으로 쓴다.
2. `config/relay.example.yaml`을 `config/relay.yaml`로 복사하고 `ntfy.topic_prefix`,
   `hmac_secret`을 채운다 (`.gitignore`에 이미 포함되어 git에 안 올라감).

### 2.2 로컬 PC: Local Relay Agent

```bash
pip install -r requirements-relay.txt
python3 daemon/relay.py --config config/relay.yaml
```

상시 실행되어야 하므로 systemd user service(Linux) 또는 launchd(macOS) 등록 권장.

### 2.3 SSH 터널

`~/.ssh/config`에서 원격 서버로 접속하는 Host 항목에 추가:

```
Host my-dev-server
    RemoteForward 8765 127.0.0.1:8765
```

`8765`는 `relay.yaml`의 `listen_port`와 같은 값. Antigravity가 이 Host로 접속할 때
터널이 자동으로 함께 열린다.

### 2.4 원격 서버: hooks 등록 (root 권한 불필요)

1. 이 리포지토리를 원격 서버에도 clone/복사한다.
2. `config/hook.example.json` → `config/hook.json`으로 복사하고 `relay_port`가
   위 SSH 터널 포트와 일치하는지 확인. 필요하면 `watched_tools`를 조정.
3. (선택) `config/hook_allowlist.example.json` → `config/hook_allowlist.json`으로
   복사하고, 매번 묻지 않아도 되는 명령 패턴을 추가.
4. 승인받고 싶은 프로젝트 폴더의 `.claude/settings.local.json`에
   `config/settings.snippet.json`의 내용을 병합한다. `/path/to/auto_yes_bot`을
   실제 경로로 바꾸고, hook의 `"timeout"`을 `hook.json`의 `http_timeout_sec`보다
   크게 맞춘다 (예: hook.json이 3500이면 settings.json은 3600).

이걸로 끝이다 — claude 바이너리를 건드리거나 이름을 바꾸는 작업은 전혀 필요 없다.

### 2.5 컴패니언 웹 UI 배포 (GitHub Pages 권장)

`web/` 폴더를 GitHub Pages로 올린다. 최초 접속 시 설정(⚙) 화면에서 ntfy 서버 URL,
`topic_prefix`, `hmac_secret`을 입력 — 브라우저 로컬 스토리지에만 저장되고 페이지
코드에는 포함되지 않는다.

### 2.6 휴대폰

1. Play Store에서 **ntfy** 설치.
2. `<topic_prefix>-notify`, `<topic_prefix>-state` 토픽을 구독.
3. 컴패니언 웹 UI 주소를 홈 화면에 바로가기로 추가.

## 3. 사용 흐름

- 평소에는 아무것도 안 해도 됨. `watched_tools`에 해당하는 도구가 호출될 때만
  이 시스템을 거친다.
- 자리를 비운 사이 승인이 필요한 상황이 생기면 ntfy 알림이 오고, 알림에서 바로
  승인/거부 탭 가능. 컴패니언 웹 UI에서도 동일하게 가능.
- 세션별 "자동 승인" 토글은 컴패니언 웹 UI의 세션 목록에서 켜고 끌 수 있다.
- `AskUserQuestion`(메뉴/자유 텍스트 질문)이 뜨면 이 시스템은 관여하지 않으니
  Antigravity 앞에서 직접 답해야 한다.
- relay가 죽어있거나 응답이 없으면, 그냥 Claude Code의 원래 동작(Antigravity가
  직접 물어보는 것)으로 넘어간다 — 아무것도 자동으로 허용/거부되지 않는다.

## 4. 알려진 제약 (요약, 상세는 SPEC.md §9)

- `AskUserQuestion`은 지원하지 않는다 (공식 hooks API에 노출되어 있지 않음).
- `watched_tools`에 넣은 도구는 Claude Code 자체의 allow-list 여부와 무관하게
  매번 이 시스템을 거친다 — 자주 쓰는 안전한 명령은 `hook_allowlist.json`에
  추가해서 조용히 통과시키는 걸 권장.
- ntfy 알림의 승인/거부 버튼은 항상 2개뿐이다 (allow/deny 이분법이라 더 필요 없음).
