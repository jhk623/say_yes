"use strict";

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
  pendingList: document.getElementById("pendingList"),
  emptyPending: document.getElementById("emptyPending"),
};

let config = loadConfig();
let source = null;

// request_id -> pending_request payload (auto_resolved=false 인 것만 유지)
const pending = new Map();
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
  const topics = `${config.prefix}-notify,${config.prefix}-state`;
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
    handlePayload(payload);
  };
}

function handlePayload(payload) {
  if (payload.type === "pending_request") {
    if (!payload.auto_resolved) {
      pending.set(payload.request_id, payload);
    }
  } else if (payload.type === "cancelled") {
    pending.delete(payload.request_id);
  } else if (payload.type === "state_snapshot") {
    sessions = payload.sessions || [];
    // relay가 더 이상 pending으로 들고 있지 않은 request_id는 화면에서도 정리한다.
    const stillPending = new Set(sessions.map((s) => s.pending_request_id).filter(Boolean));
    for (const id of Array.from(pending.keys())) {
      if (!stillPending.has(id)) pending.delete(id);
    }
  }
  render();
}

function render() {
  renderSessions();
  renderPending();
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

function renderPending() {
  els.pendingList.innerHTML = "";
  const items = Array.from(pending.values());
  els.emptyPending.style.display = items.length ? "none" : "block";

  for (const req of items) {
    const li = document.createElement("li");
    li.className = "pending-card";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${req.session_label || req.session_id} · ${req.tool_name || ""}`;
    li.appendChild(meta);

    const pre = document.createElement("pre");
    pre.textContent = req.summary || "";
    li.appendChild(pre);

    const buttons = document.createElement("div");
    buttons.className = "options";
    const approve = document.createElement("button");
    approve.className = "approve";
    approve.textContent = "승인";
    approve.addEventListener("click", () => sendReply(req.request_id, "allow"));
    const deny = document.createElement("button");
    deny.className = "deny";
    deny.textContent = "거부";
    deny.addEventListener("click", () => sendReply(req.request_id, "deny"));
    buttons.appendChild(approve);
    buttons.appendChild(deny);
    li.appendChild(buttons);

    els.pendingList.appendChild(li);
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

async function sendReply(requestId, decision) {
  const sig = await hmacSha256Hex(config.secret, `${requestId}|${decision}`);
  const body = { type: "reply", request_id: requestId, decision, sig };
  await publish(`${config.prefix}-reply`, body);
  pending.delete(requestId);
  render();
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
