"use strict";

// app.js와 같은 LS_KEY를 써서, 메인 페이지에서 이미 저장한 설정(서버/prefix/secret)을
// 그대로 공유한다 (같은 오리진의 localStorage이므로 자동으로 보인다).
const LS_KEY = "auto_yes_bot_config";

const els = {
  settingsBtn: document.getElementById("settingsBtn"),
  settingsPanel: document.getElementById("settingsPanel"),
  cfgServer: document.getElementById("cfgServer"),
  cfgPrefix: document.getElementById("cfgPrefix"),
  cfgSecret: document.getElementById("cfgSecret"),
  saveSettings: document.getElementById("saveSettings"),
  statusBar: document.getElementById("statusBar"),
  sessionList: document.getElementById("sessionList"),
};

let config = loadConfig();
let source = null;

// session_id -> {label, status, pending_request_id, auto_approve}
let sessions = [];

init();

function init() {
  if (config) {
    els.cfgServer.value = config.server;
    els.cfgPrefix.value = config.prefix;
    els.cfgSecret.value = config.secret;
    connect();
  } else {
    els.settingsPanel.classList.remove("hidden");
  }

  els.settingsBtn.addEventListener("click", () => {
    els.settingsPanel.classList.toggle("hidden");
  });

  els.saveSettings.addEventListener("click", () => {
    config = {
      server: els.cfgServer.value.trim().replace(/\/$/, ""),
      prefix: els.cfgPrefix.value.trim(),
      secret: els.cfgSecret.value,
    };
    if (!config.server || !config.prefix || !config.secret) {
      alert("서버/토픽 접두사/비밀키를 모두 입력하세요.");
      return;
    }
    saveConfig(config);
    els.settingsPanel.classList.add("hidden");
    connect();
  });
}

function loadConfig() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY));
  } catch {
    return null;
  }
}

function saveConfig(cfg) {
  localStorage.setItem(LS_KEY, JSON.stringify(cfg));
}

function connect() {
  if (source) source.close();
  // 승인 대기 알림은 이 페이지에서 다루지 않으므로 -state 토픽만 구독한다.
  const topics = `${config.prefix}-state`;
  const url = `${config.server}/${topics}/sse?since=all`;
  source = new EventSource(url);
  setStatus(`연결 중... (${config.server})`);

  source.onopen = () => setStatus(`연결됨 — ${config.server}`);
  source.onerror = () => setStatus(`연결 끊김, 재시도 중... — ${config.server}`);

  source.onmessage = (evt) => {
    let envelope;
    try {
      envelope = JSON.parse(evt.data);
    } catch {
      return;
    }
    if (envelope.event !== "message" || !envelope.message) return;
    let payload;
    try {
      payload = JSON.parse(envelope.message);
    } catch {
      return;
    }
    if (payload.type === "state_snapshot") {
      sessions = payload.sessions || [];
      renderSessions();
    }
  };
}

function renderSessions() {
  els.sessionList.innerHTML = "";
  if (sessions.length === 0) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "연결된 세션이 없습니다.";
    els.sessionList.appendChild(li);
    return;
  }
  for (const s of sessions) {
    const li = document.createElement("li");
    li.className = "session-card";

    const info = document.createElement("div");
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = s.label;
    const status = document.createElement("div");
    status.className = "status" + (s.pending_request_id ? " pending" : "");
    status.textContent = s.pending_request_id ? "승인 대기 중" : s.status;
    info.appendChild(label);
    info.appendChild(status);

    const toggle = document.createElement("label");
    toggle.className = "toggle";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = !!s.auto_approve;
    checkbox.addEventListener("change", () => setAutoApprove(s.session_id, checkbox.checked));
    toggle.appendChild(checkbox);
    toggle.appendChild(document.createTextNode("자동 승인"));

    li.appendChild(info);
    li.appendChild(toggle);
    els.sessionList.appendChild(li);
  }
}

async function hmacSha256Hex(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  return Array.from(new Uint8Array(sigBuf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function setAutoApprove(sessionId, enabled) {
  const sig = await hmacSha256Hex(config.secret, `${sessionId}|${enabled ? "true" : "false"}`);
  const body = { type: "set_auto_approve", session_id: sessionId, enabled, sig };
  await publish(`${config.prefix}-reply`, body);
}

async function publish(topic, jsonBody) {
  try {
    await fetch(`${config.server}/${topic}`, {
      method: "POST",
      body: JSON.stringify(jsonBody),
    });
  } catch (err) {
    setStatus(`전송 실패: ${err}`);
  }
}

function setStatus(text) {
  els.statusBar.textContent = text;
}
