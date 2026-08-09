/* Shared client: REST helper + WebSocket client with auto-reconnect. */

"use strict";

const API = (() => {
  async function request(method, path, body, token) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    let res;
    try {
      res = await fetch(path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      throw { code: "NETWORK", message: "Serveur injoignable." };
    }
    let payload = null;
    try { payload = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok || !payload || payload.success === false) {
      const err = (payload && payload.error) || { code: "HTTP_" + res.status, message: "Erreur " + res.status };
      throw err;
    }
    return payload.data;
  }
  return {
    get: (path, token) => request("GET", path, undefined, token),
    post: (path, body, token) => request("POST", path, body, token),
    put: (path, body, token) => request("PUT", path, body, token),
    del: (path, token) => request("DELETE", path, undefined, token),
  };
})();

/* WebSocket with exponential-backoff reconnect. Returns {connect, close}.
   onMessage receives parsed JSON. onStatus informs connection state. */
function WSClient(path, onMessage, onStatus) {
  let ws = null;
  let closed = false;
  let attempts = 0;
  let retryTimer = null;

  function connect() {
    if (closed) return;
    const protocol = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(protocol + location.host + path);
    ws.onopen = () => {
      attempts = 0;
      if (onStatus) onStatus("connected");
    };
    ws.onmessage = (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch (_) { return; }
      if (data.type === "ping") { ws.send(JSON.stringify({ type: "pong" })); return; }
      if (onMessage) onMessage(data);
    };
    ws.onclose = () => {
      if (onStatus) onStatus("disconnected");
      if (closed) return;
      const delay = Math.min(1000 * 2 ** attempts, 10000);
      attempts += 1;
      retryTimer = setTimeout(connect, delay);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function close() {
    closed = true;
    clearTimeout(retryTimer);
    try { ws && ws.close(); } catch (_) {}
  }

  return { connect, send, close };
}

/* Simple toast notifications. */
function toast(message) {
  const box = document.getElementById("toast");
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, 3500);
}

function esc(html) {
  const div = document.createElement("div");
  div.textContent = html ?? "";
  return div.innerHTML;
}

/* Countdown driven by server timestamps.
   deadline stays accurate despite client clock drift. */
function serverCountdown(serverEndsAt, serverReceivedAt, tick) {
  const ends = new Date(serverEndsAt).getTime();
  const drift = Date.now() - new Date(serverReceivedAt).getTime();
  const deadline = ends + drift;
  tick(deadline - Date.now());
  const id = setInterval(() => tick(deadline - Date.now()), 200);
  return id;
}

const STATUS_LABELS = {
  draft: "Brouillon",
  scheduled: "Planifiée",
  waiting: "Salle d'attente",
  running: "En cours",
  paused: "En pause",
  finished: "Terminée",
  cancelled: "Annulée",
};