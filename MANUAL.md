# 사용 매뉴얼 — 자동 승인 켜기 / 안 물어봐도 되는 명령 추가하기

설치/배포 방법은 [README.md](./README.md), 설계는 [SPEC.md](./SPEC.md) 참고. 이 문서는
"평소에 어떻게 조정하며 쓰는지"만 다룹니다.

## 1. 특정 세션의 자동 승인(auto-approve) 켜고 끄기

**어디서**: 컴패니언 웹 UI (`https://jhk623.github.io/say_yes/`)

1. 웹 UI를 엽니다.
2. "세션" 목록에서 켜고 싶은 프로젝트를 찾습니다 (라벨이 그 프로젝트의 경로로 표시됩니다,
   예: `/home/jhkim/intermediate_leaerning/real_data`).
3. 그 줄 오른쪽의 "자동 승인" 토글을 켭니다.

**동작 방식**
- 세션 단위로 적용되고, `daemon/relay.py`가 로컬 PC의 `.state/auto_approve.json`에
  저장하기 때문에 relay를 재시작해도 유지됩니다.
- 켜져 있는 동안은 **권한 승인(y/n류) 요청만** 사람에게 묻지 않고 바로 허용됩니다.
  다만 폰 알림에는 `[자동 승인됨]`으로 기록이 남아서 나중에 뭐가 자동 승인됐는지
  확인할 수 있습니다.
- `AskUserQuestion`(메뉴/자유 텍스트로 되묻는 것)은 auto-approve와 무관하게
  **항상** 직접 Antigravity에서 답해야 합니다 (SPEC.md §2 참고 — 이 도구는 공식
  hooks API로 가로챌 수 없습니다).
- **주의**: 켜두면 위험한 명령(`rm -rf` 등)까지 묻지 않고 그대로 실행됩니다.
  오래 켜둘 세션이 아니라면 끝나고 바로 꺼두는 걸 권장합니다.

즉시 반영됩니다 — relay나 원격 서버 쪽에서 따로 재시작할 필요 없습니다.

## 2. 매번 안 물어봐도 되는 명령 추가하기 (hook_allowlist.json)

**어디서**: **원격 서버**(hubble)의 `auto_yes_bot/config/hook_allowlist.json`
(로컬 PC가 아니라 hooks가 실제로 실행되는 원격 서버에 있는 파일입니다.)

파일이 없으면 `config/hook_allowlist.example.json`을 복사해서
`config/hook_allowlist.json`으로 만드세요.

### 기본 형태

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

- `Bash`, `Write`, `Edit` 등 도구 이름별로 정규식 목록을 넣습니다.
- 각 정규식은 그 도구 호출의 `tool_input`을 JSON 문자열로 바꾼 것에 대해 검사됩니다.
  예를 들어 Bash 명령 `git status`는 내부적으로
  `{"command": "git status", ...}` 형태의 문자열이 되므로,
  `"command"\s*:\s*"git status` 처럼 그 문자열 안에 나타날 패턴을 적으면 됩니다.
- 하나라도 매치되면 **폰에 알리지 않고 조용히 통과**됩니다 (Claude Code의 원래
  권한 처리로 넘어감 — 이미 allow-list 되어 있으면 그냥 실행, 아니면 Antigravity가
  직접 물어봄).

### 자주 쓰는 예시

```json
{
  "patterns_by_tool": {
    "Bash": [
      "\"command\"\\s*:\\s*\"git status",
      "\"command\"\\s*:\\s*\"git diff",
      "\"command\"\\s*:\\s*\"git log",
      "\"command\"\\s*:\\s*\"ls\\b",
      "\"command\"\\s*:\\s*\"pwd\\b",
      "\"command\"\\s*:\\s*\"cat\\b"
    ],
    "Write": [
      "\"file_path\"\\s*:\\s*\".*\\.log\""
    ],
    "Edit": [
      "\"file_path\"\\s*:\\s*\"/home/jhkim/scratch/"
    ]
  }
}
```

- `Write`/`Edit` 예시는 각각 "파일명이 `.log`로 끝나면", "`/home/jhkim/scratch/`
  아래 파일이면" 조용히 통과시키는 패턴입니다. 필요에 맞게 경로/확장자를 바꿔 쓰세요.
- 정규식이라서 `.`, `(`, `)` 같은 특수문자는 실제 그 문자를 매치하려면 `\.`처럼
  이스케이프해야 합니다 (위 `.log` 예시에서 `\\.log`가 그 이유입니다).

### 반영 확인

파일을 저장하면 **바로 다음 도구 호출부터 적용**됩니다 — hook은 매 호출마다
새로 실행되면서 이 파일을 다시 읽기 때문에, relay나 Antigravity를 재시작할
필요가 없습니다.

## 3. 아예 감시 대상에서 빼고 싶은 도구가 있다면 (watched_tools)

**어디서**: 원격 서버의 `auto_yes_bot/config/hook.json`

```json
"watched_tools": ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch"]
```

이 목록에 없는 도구(예: `Read`, `Glob`, `Grep`, `TodoWrite`)는 애초에 이 시스템을
거치지 않고 항상 Claude Code 기본 동작대로 처리됩니다. 특정 도구를 통째로 이
시스템에서 빼고 싶으면 이 목록에서 지우면 됩니다. 이것도 저장 즉시 적용됩니다.

## 4. "Claude가 직접 확인이 필요한 걸 묻고 있어요" 알림

`AskUserQuestion`(메뉴/자유 텍스트로 되묻는 도구)은 여전히 원격으로 답할 수는
없지만(§1 참고), **최소한 "지금 뭔가 막혀있으니 Antigravity를 열어봐야 한다"는
신호**는 폰으로 옵니다. 제목이 `🔔 확인 필요 — <세션 이름>`으로, 승인/거부
버튼이 있는 알림과는 다르게 보여서 구분됩니다.

**동작 원리**: Claude Code가 60초 동안 응답이 없으면 발생하는 신호(idle_prompt)를
보되, "이번 턴이 정상적으로 끝난 뒤라서 그냥 조용한 것"인지 "뭔가에 막혀서
멈춘 것"인지를 구분해서, 후자일 때만 알림을 보냅니다. 이미 승인 대기 알림이
떠 있는 경우엔 중복으로 오지 않습니다.

**한계**: 이건 "확실한 신호"가 아니라 추정입니다. Claude Code가 진짜로
`AskUserQuestion`을 띄운 게 아니라 다른 이유로 60초 넘게 조용한 경우에도
(드물게) 이 알림이 올 수 있습니다 — 그럴 땐 그냥 무시하고 평소처럼 확인하시면
됩니다.

## 5. "응답 완료" 알림 (다음 작업 지시 타이밍 알려주기)

Claude가 한 턴의 응답을 끝내고 다음 지시를 기다리는 상태가 될 때마다
`✅ 응답 완료 — <세션 이름>` 알림이 옵니다. 승인/거부나 확인 필요 알림과는
다른 제목이라 구분됩니다.

**주의**: 승인 알림처럼 특별한 상황에서만 오는 게 아니라, **턴이 끝날 때마다**
옵니다 — 대화를 빠르게 주고받는 중이면 그만큼 자주 울릴 수 있습니다. 자리를
비우고 긴 작업을 시켜둘 때 "지금 끝났다"를 알기 위한 용도로 쓰시면 좋습니다.

## 6. 확인이 필요할 때

- **지금 뭐가 대기 중인지**: 웹 UI 열어서 "승인 대기" 목록 확인.
- **이 세션이 자동 승인 켜져 있는지**: 웹 UI의 세션 목록에서 토글 상태 확인.
- **allowlist/hook.json이 제대로 됐는지 감이 안 잡히면**: 저(Claude)한테 파일
  내용을 봐달라고 하면 원격 서버에서 바로 확인해드릴 수 있습니다.
