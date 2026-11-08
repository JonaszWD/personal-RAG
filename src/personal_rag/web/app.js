// All server text is written with textContent only (no HTML parsing).
"use strict";

const $ = (id) => document.getElementById(id);
let controller = null;

function show(view) {
  $("login").hidden = view !== "login";
  $("app").hidden = view !== "app";
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401 && path !== "/auth/login") {
    show("login");
  }
  return res;
}

async function loadDocs() {
  try {
    const res = await api("/documents");
    if (!res.ok) return;
    const { indexed } = await res.json();
    const names = Object.keys(indexed).sort();
    const list = $("docs");
    list.replaceChildren();
    for (const name of names) {
      const li = document.createElement("li");
      li.textContent = `${name} (${indexed[name]})`;
      list.appendChild(li);
    }
    $("docs-summary").textContent = `Documents (${names.length})`;
  } catch {
    /* non-fatal */
  }
}

async function start() {
  try {
    const res = await api("/auth/me");
    if (res.ok) {
      show("app");
      loadDocs();
      return;
    }
  } catch {
    /* fall through to login */
  }
  show("login");
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const res = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({ token: $("token").value }),
    });
    $("token").value = "";
    if (res.ok) {
      start();
    } else if (res.status === 429) {
      $("login-error").textContent = "Too many attempts. Try again later.";
    } else if (res.status === 503) {
      $("login-error").textContent = "Remote access is not enabled on the server.";
    } else {
      $("login-error").textContent = "Wrong token.";
    }
  } catch {
    $("login-error").textContent = "Cannot reach the server.";
  }
});

$("logout").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST", body: "{}" });
  $("answer").textContent = "";
  $("sources").textContent = "";
  show("login");
});

function setBusy(busy) {
  $("send").disabled = busy;
  $("stop").hidden = !busy;
}

$("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = $("question").value.trim();
  if (!question) return;
  $("answer").textContent = "";
  $("sources").textContent = "";
  $("error").textContent = "";
  setBusy(true);
  controller = new AbortController();
  try {
    const res = await api("/query/stream", {
      method: "POST",
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    if (!res.ok) {
      $("error").textContent =
        res.status === 429 ? "Slow down a moment." :
        res.status === 401 ? "" : `Request failed (${res.status}).`;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.sources) $("sources").textContent = msg.sources.length ? "Sources: " + msg.sources.join(", ") : "";
        if (msg.token) $("answer").textContent += msg.token;
        if (msg.error) $("error").textContent = msg.error;
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") $("error").textContent = "Connection lost.";
  } finally {
    setBusy(false);
    controller = null;
  }
});

$("stop").addEventListener("click", () => controller && controller.abort());

start();
