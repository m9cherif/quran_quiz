/* Admin console: competitions list, question manager, live broadcasting,
   participant roster and live leaderboard. */

"use strict";

const KEY_STORE = "quran.admin.key";
const $ = (id) => document.getElementById(id);

let adminKey = localStorage.getItem(KEY_STORE) || "";
let ws = null;
let currentComp = null;      // competition row
let questions = [];
let liveInfo = null;         // {question_id, position, started_at, ends_at, paused}
let teller = null;
let answeredCount = 0;

const STATUS_LABELS = {
  draft: "Brouillon", scheduled: "Planifiée", waiting: "Salle d'attente",
  running: "En cours", paused: "En pause", finished: "Terminée", cancelled: "Annulée",
};

function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
  $(view).hidden = false;
}

/* ---------------- unlock ---------------- */

function unlock() {
  adminKey = $("admin-key").value.trim();
  if (!adminKey) return;
  localStorage.setItem(KEY_STORE, adminKey);
  loadCompetitions();
}

$("btn-unlock").addEventListener("click", unlock);
$("admin-key").addEventListener("keydown", (e) => { if (e.key === "Enter") unlock(); });
$("btn-logout-admin").addEventListener("click", () => {
  localStorage.removeItem(KEY_STORE);
  adminKey = "";
  ws && ws.close();
  show("view-unlock");
  $("admin-key").value = "";
});

/* ---------------- create your own admin key ---------------- */

$("btn-show-create-key").addEventListener("click", () => {
  $("create-key-box").hidden = !$("create-key-box").hidden;
});

$("btn-create-key").addEventListener("click", async () => {
  const password = $("create-key-password").value;
  const label = $("create-key-label").value.trim() || null;
  const errBox = $("create-key-error");
  errBox.hidden = true;
  if (!password) { errBox.textContent = "Entrez le mot de passe partagé."; errBox.hidden = false; return; }
  const btn = $("btn-create-key");
  btn.disabled = true;
  btn.textContent = "Création…";
  try {
    const data = await API.post("/api/admin/key/generate", { password, label });
    $("create-key-value").value = data.access_token;
    $("create-key-result").hidden = false;
    $("create-key-box").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    errBox.textContent = err.message || "Mot de passe refusé.";
    errBox.hidden = false;
  }
  btn.disabled = false;
  btn.textContent = "Créer ma clé";
});

$("btn-copy-new-key").addEventListener("click", () => {
  const key = $("create-key-value").value;
  navigator.clipboard
    ? navigator.clipboard.writeText(key).then(() => {
        localStorage.setItem(KEY_STORE, key);
        adminKey = key;
        loadCompetitions();
      })
    : toast("Copiez la clé manuellement.");
});

if (adminKey) $("admin-key").value = adminKey;

async function loadCompetitions() {
  try {
    const rows = await API.get("/api/admin/competitions", adminKey);
    show("view-list");
    $("btn-logout-admin").hidden = false;
    renderCompetitions(rows || []);
  } catch (err) {
    if (err.code === "NOT_AUTHORIZED") {
      toast("Clé d'administration invalide.");
      show("view-unlock");
    } else toast(err.message);
  }
}

function renderCompetitions(rows) {
  const box = $("competition-list");
  if (!rows.length) {
    box.innerHTML = '<p class="hint">Aucune compétition. Créez-en une ci-dessus.</p>';
    return;
  }
  box.innerHTML = rows
    .map(
      (c) =>
        '<div class="comp-row">' +
        "<div><h3>" + esc(c.name) + "</h3>" +
        '<div class="meta"><span class="badge badge-' + c.status + '">' + (STATUS_LABELS[c.status] || c.status) + "</span>" +
        "<span>#" + esc(c.code) + "</span></div></div>" +
        '<div class="actions"><button class="btn" data-open="' + c.id + '">Ouvrir</button></div></div>'
    )
    .join("");
  box.querySelectorAll("[data-open]").forEach((b) =>
    b.addEventListener("click", () => openCompetition(b.dataset.open))
  );
}

/* ---------------- create ---------------- */

$("btn-create").addEventListener("click", async () => {
  const name = $("new-name").value.trim();
  if (!name) { toast("Donnez un nom à la compétition."); return; }
  const body = {
    name,
    code: $("new-code").value.trim().toUpperCase() || undefined,
    default_points: Number($("new-points").value) || 10,
    default_negative_points: Number($("new-negative").value) || 0,
    speed_bonus_enabled: $("new-speed").checked,
  };
  try {
    const comp = await API.post("/api/admin/competitions", body, adminKey);
    toast("Compétition créée — code " + comp.code);
    $("new-name").value = "";
    $("new-code").value = "";
    await loadCompetitions();
    openCompetition(comp.id);
  } catch (err) {
    toast(err.message);
  }
});

/* ---------------- open / manage ---------------- */

async function openCompetition(id) {
  if (ws) ws.close();
  await loadCompetitionDetail(id);
  show("view-manage");
  ws = WSClient("/ws/competition/" + id, onWsMessage, onWsStatus);
  ws.connect();
}

async function loadCompetitionDetail(id) {
  const data = await API.get("/api/admin/competitions/" + id, adminKey);
  currentComp = data;
  $("comp-name").textContent = data.name;
  $("comp-code").textContent = data.code;
  $("comp-status").className = "badge badge-" + data.status;
  $("comp-status").textContent = STATUS_LABELS[data.status] || data.status;
  renderStatusButtons(data.status);
  await Promise.all([loadQuestions(id), loadPlayers(id), loadLeaderboard(id)]);
}

function renderStatusButtons(status) {
  $("btn-start").hidden = !["draft", "scheduled", "waiting"].includes(status);
  $("btn-pause").hidden = status !== "running";
  $("btn-resume").hidden = status !== "paused";
  $("btn-finish").hidden = !["running", "paused"].includes(status);
}

function onWsStatus(state) {
  if (state === "connected") ws.send({ type: "identify", role: "admin", token: adminKey });
}

function onWsMessage(message) {
  if (!currentComp) return;
  switch (message.type) {
    case "competition_state":
      updateStatus(message.status);
      if (message.active_question) showLive(message.active_question);
      $("comp-connected").textContent = "👥 " + message.participants_connected;
      break;
    case "participant_joined":
    case "participant_left":
      loadPlayers(currentComp.id);
      $("comp-connected").textContent =
        "👥 " + (parseInt($("comp-connected").textContent.replace(/\D/g, ""), 10) || 0);
      break;
    case "question_started":
      answeredCount = 0;
      showLive({
        question_id: message.question_id,
        position: message.position,
        started_at: message.started_at,
        ends_at: message.ends_at,
        paused: false,
      });
      break;
    case "question_ended":
      endLive();
      break;
    case "answer_received":
      answeredCount = message.answered_count || answeredCount + 1;
      updateLiveCount();
      break;
    case "leaderboard_updated":
      if (currentComp) loadLeaderboard(currentComp.id);
      break;
    case "competition_started":
    case "competition_resumed":
      updateStatus("running");
      break;
    case "competition_paused":
      updateStatus("paused");
      break;
    case "competition_finished":
      updateStatus("finished");
      endLive();
      break;
    case "error":
      toast(message.message || "Erreur WebSocket.");
      break;
  }
}

function updateStatus(status) {
  if (!currentComp) return;
  currentComp.status = status;
  $("comp-status").className = "badge badge-" + status;
  $("comp-status").textContent = STATUS_LABELS[status] || status;
  renderStatusButtons(status);
}

$("btn-back").addEventListener("click", () => {
  if (ws) ws.close();
  clearInterval(teller);
  loadCompetitions();
});

/* ---------------- status controls ---------------- */

async function adminPost(path, okMessage) {
  try {
    const data = await API.post(path, {}, adminKey);
    if (okMessage) toast(okMessage);
    return data;
  } catch (err) {
    toast(err.message);
    return null;
  }
}

$("btn-start").addEventListener("click", async () => {
  const data = await adminPost(
    "/api/admin/competitions/" + currentComp.id + "/start",
    "Compétition démarrée !"
  );
  if (data) updateStatus("running");
});
$("btn-pause").addEventListener("click", () =>
  adminPost("/api/admin/competitions/" + currentComp.id + "/pause", "⏸ En pause")
);
$("btn-resume").addEventListener("click", () =>
  adminPost("/api/admin/competitions/" + currentComp.id + "/resume", "Reprise")
);
$("btn-finish").addEventListener("click", async () => {
  if (!confirm("Terminer la compétition ? Les participants ne pourront plus répondre.")) return;
  const data = await adminPost(
    "/api/admin/competitions/" + currentComp.id + "/finish",
    "🏁 Compétition terminée"
  );
  if (data) updateStatus("finished");
});

$("btn-copy-code").addEventListener("click", () => {
  navigator.clipboard
    ? navigator.clipboard.writeText(currentComp.code).then(() => toast("Code copié ✓"))
    : toast(currentComp.code);
});

/* ---------------- live question ---------------- */

function showLive(info) {
  liveInfo = info;
  clearInterval(teller);
  const box = $("live-question");
  box.innerHTML =
    '<div class="live-question"><div class="lq-pos">Question ' + esc(info.position) + "</div>" +
    '<div class="live-timer-big" id="live-timer">…</div>' +
    '<div class="lq-count" id="live-count">📥 0 réponse</div></div>';
  if (info.ends_at) {
    const startedAt = info.started_at ? new Date(info.started_at).toISOString() : null;
    const ref = startedAt || new Date().toISOString();
    teller = serverCountdown(info.ends_at, ref, (remaining) => {
      const safe = Math.max(0, Math.ceil(remaining / 1000));
      $("live-timer").textContent = safe + " s";
      if (safe === 0) $("live-timer").style.color = "var(--red)";
    });
  } else {
    $("live-timer").textContent = "⏸ en pause";
  }
  updateLiveCount();
}

function endLive() {
  clearInterval(teller);
  liveInfo = null;
  $("live-question").innerHTML = '<p class="hint">Question terminée. Lancez la suivante.</p>';
  loadQuestions(currentComp.id);
  loadLeaderboard(currentComp.id);
}

function updateLiveCount() {
  const el = $("live-count");
  if (el) el.textContent = "📥 " + answeredCount + " réponse" + (answeredCount > 1 ? "s" : "");
}

/* ---------------- questions ---------------- */

async function loadQuestions(competitionId) {
  const rows = await API.get("/api/admin/competitions/" + competitionId + "/questions", adminKey);
  questions = rows || [];
  renderQuestions();
}

function renderQuestions() {
  const box = $("questions-list");
  if (!questions.length) {
    box.innerHTML = '<p class="hint">Aucune question. Ajoutez-en une à droite.</p>';
    return;
  }
  box.innerHTML = questions
    .map((q) => {
      const correct =
        q.type === "text" || q.type === "number"
          ? '<div class="qcorrect">✔ ' + esc(q.correct_answer_text) + "</div>"
          : "";
      return (
        '<div class="question-row"><div class="qnum">' + q.position + "</div>" +
        '<div class="qbody"><div class="qtype">' + q.type.replace("_", " ") + " · " + q.duration_seconds + " s</div>" +
        esc(q.text) + correct + "</div>" +
        '<button class="btn btn-primary" data-launch="' + q.id + '" ' + (canLaunch() ? "" : "disabled") + ">▶ Lancer</button>" +
        '<button class="btn btn-ghost" data-del="' + q.id + '">🗑</button></div>'
      );
    })
    .join("");
  box.querySelectorAll("[data-launch]").forEach((b) =>
    b.addEventListener("click", () => launchQuestion(b.dataset.launch))
  );
  box.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => deleteQuestion(b.dataset.del))
  );
}

function canLaunch() {
  return currentComp && currentComp.status === "running" && !liveInfo;
}

async function launchQuestion(questionId) {
  const data = await adminPost(
    "/api/admin/competitions/" + currentComp.id + "/questions/" + questionId + "/start",
    null
  );
  if (data) loadQuestions(currentComp.id);
}

async function deleteQuestion(questionId) {
  if (!confirm("Supprimer cette question (et ses choix) ?")) return;
  try {
    await API.del("/api/admin/questions/" + questionId, adminKey);
    loadQuestions(currentComp.id);
  } catch (err) {
    toast(err.message);
  }
}

/* ---------------- add question ---------------- */

const TYPE_HAS_CHOICES = ["mcq", "true_false"];

function questionFormState() {
  const type = $("q-type").value;
  $("q-choices").hidden = !TYPE_HAS_CHOICES.includes(type);
  $("q-correct-wrap").hidden = !["text", "number"].includes(type);
  $("q-audio-wrap").hidden = type !== "audio";
  if (type === "true_false" && !$("q-choices-row").children.length) {
    $("q-choices-row").innerHTML = "";
    ["Vrai", "Faux"].forEach((label, i) =>
      $("q-choices-row").appendChild(choiceEditorHtml(label, i === 0))
    );
  }
  if (type === "mcq" && !$("q-choices-row").children.length) {
    ["", "", "", ""].forEach((_, i) =>
      $("q-choices-row").appendChild(choiceEditorHtml("", i === 0))
    );
  }
}

function choiceEditorHtml(value, correct) {
  const wrap = document.createElement("div");
  wrap.className = "grid-2";
  wrap.innerHTML =
    '<label class="checkbox-label"><input type="radio" name="correct-choice" ' + (correct ? "checked" : "") + "> correct</label>" +
    '<input class="choice-text" placeholder="Choix…" value="' + esc(value).replace(/"/g, "&quot;") + '">';
  return wrap;
}

$("q-type").addEventListener("change", questionFormState);
$("btn-add-choice").addEventListener("click", () =>
  $("q-choices-row").appendChild(choiceEditorHtml("", false))
);

$("btn-add-question").addEventListener("click", async () => {
  const type = $("q-type").value;
  const text = $("q-text").value.trim();
  if (!text) { toast("Entrez le texte de la question."); return; }
  const body = {
    type,
    text,
    position: Number($("q-position").value) || 1,
    duration_seconds: Number($("q-duration").value) || 15,
    points: $("q-points").value ? Number($("q-points").value) : null,
    negative_points: $("q-negative").value ? Number($("q-negative").value) : null,
    explanation: $("q-explanation").value.trim() || null,
  };
  if (type === "audio") body.audio_url = $("q-audio").value.trim() || null;
  if (type === "text" || type === "number") body.correct_answer_text = $("q-correct").value.trim() || null;

  let question;
  try {
    question = await API.post(
      "/api/admin/competitions/" + currentComp.id + "/questions",
      body,
      adminKey
    );
  } catch (err) {
    toast(err.message);
    return;
  }

  if (TYPE_HAS_CHOICES.includes(type)) {
    const rows = [...$("q-choices-row").querySelectorAll(".grid-2")];
    const correctIndex = rows.findIndex(
      (r) => r.querySelector("input[type=radio]").checked
    );
    for (let i = 0; i < rows.length; i++) {
      const choiceText = rows[i].querySelector(".choice-text").value.trim();
      if (!choiceText) continue;
      try {
        await API.post(
          "/api/admin/questions/" + question.id + "/choices",
          { text: choiceText, position: i + 1, is_correct: i === correctIndex },
          adminKey
        );
      } catch (err) {
        toast("Choix refusé : " + err.message);
      }
    }
  }
  toast("Question ajoutée ✓");
  resetQuestionForm(type);
  loadQuestions(currentComp.id);
});

function resetQuestionForm(type) {
  $("q-text").value = "";
  $("q-position").value = questions.length + 1;
  $("q-duration").value = 15;
  $("q-points").value = "";
  $("q-negative").value = "";
  $("q-correct").value = "";
  $("q-audio").value = "";
  $("q-explanation").value = "";
  if (type === "mcq") {
    const rows = [...$("q-choices-row").querySelectorAll(".grid-2")];
    rows.forEach((r) => (r.querySelector(".choice-text").value = ""));
  }
}

/* ---------------- players & leaderboard ---------------- */

async function loadPlayers(competitionId) {
  try {
    const rows = await API.get("/api/admin/competitions/" + competitionId + "/participants", adminKey);
    $("players-count").textContent = rows.length;
    const box = $("players-list");
    if (!rows.length) { box.innerHTML = '<p class="hint">Aucun participant pour l\'instant.</p>'; return; }
    box.innerHTML = rows
      .map(
        (p) =>
          '<div class="comp-row"><strong>' + esc(p.display_name) + "</strong>" +
          '<span class="chip">' + (p.connected ? "🟢 connecté" : "⚪ hors ligne") + "</span></div>"
      )
      .join("");
  } catch (err) {
    toast(err.message);
  }
}

async function loadLeaderboard(competitionId) {
  try {
    const rows = await API.get("/api/competitions/" + competitionId + "/leaderboard", adminKey);
    renderLeaderboard(rows || []);
  } catch (err) { /* not authorized or not started — ignore */ }
}

function renderLeaderboard(rows) {
  const box = $("leaderboard-table");
  if (!rows.length) { box.innerHTML = '<p class="hint">Le classement apparaîtra ici.</p>'; return; }
  box.innerHTML =
    '<table class="leaderboard"><tr><th></th><th>Participant</th><th>Réussites</th><th>Score</th></tr>' +
    rows
      .map(
        (r) =>
          '<tr class="' + (r.rank === 1 ? "top1" : "") + '">' +
          '<td class="rank">' + r.rank + "</td><td>" + esc(r.display_name) + "</td>" +
          "<td>" + r.correct_answers + "/" + r.answered_questions + "</td>" +
          "<td><strong>" + r.score + "</strong></td></tr>"
      )
      .join("") +
    "</table>";
}

/* ---------------- init ---------------- */

if (adminKey) loadCompetitions();
questionFormState();