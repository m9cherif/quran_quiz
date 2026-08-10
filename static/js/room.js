/* Participant room: waiting → question → feedback → results.
   Real-time via WebSocket; answers submitted by REST (server is the time authority). */

"use strict";

const COMPETITION_ID = document.querySelector(".room").dataset.competition;
const CIRC = 2 * Math.PI * 52;

const token = localStorage.getItem("quran.token");
const myName = localStorage.getItem("quran.name") || "Participant";

if (!token) {
  location.href = "/join";
} else {
  init();
}

let ws = null;
let identified = false;
let currentQuestion = null;      // {id, deadlineTimer, answered, duration}
let teller = null;               // countdown interval
let timerBar = null;
let lastAnsweredAt = null;

const $ = (id) => document.getElementById(id);

function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
  const el = document.getElementById(view);
  if (el) el.hidden = false;
  return el;
}

function init() {
  show("view-waiting");
  ws = WSClient(
    "/ws/competition/" + COMPETITION_ID,
    onMessage,
    onStatus
  );
  ws.connect();
  $("btn-leave").addEventListener("click", () => {
    ws.close();
    localStorage.removeItem("quran.token");
    location.href = "/";
  });
  fillWaitingRoom();
}

function onStatus(state) {
  if (state === "connected") {
    ws.send({ type: "identify", role: "participant", token });
    identified = true;
    fillWaitingRoom();
  }
  if (state === "disconnected") {
    identified = false;
    if (!apiAnsweredVisible()) toast("Connexion perdue — reconnexion…");
  }
}

function apiAnsweredVisible() {
  return !$("view-feedback").hidden;
}

/* ---------------- waiting room ---------------- */

async function fillWaitingRoom() {
  $("room-myname").textContent = myName;
  try {
    const info = await API.get(
      "/api/competitions/" + COMPETITION_ID + "/waitroom",
      token
    );
    $("room-name").textContent = info.competition_name;
    $("room-status").textContent = statusText(info.competition_status);
    $("room-code-chip").textContent = "#" + (info.competition_code || "?");
    $("room-players").textContent =
      "👥 " + (info.connected_participants || 0) + " connectés";
  } catch (err) {
    if (err.code === "NOT_AUTHORIZED") {
      localStorage.removeItem("quran.token");
      location.href = "/join";
      return;
    }
    toast(err.message || "Impossible de charger la salle.");
  }
}

function statusText(status) {
  if (status === "running" || status === "paused")
    return "La compétition a commencé !";
  if (status === "waiting") return "En attente du démarrage…";
  if (status === "finished") return "Compétition terminée.";
  return "Bientôt…";
}

/* ---------------- WebSocket events ---------------- */

function onMessage(message) {
  switch (message.type) {
    case "competition_state":
      handleState(message);
      break;
    case "competition_started":
    case "competition_resumed":
      show("view-waiting");
      fillWaitingRoom();
      toast("La compétition commence !");
      break;
    case "competition_paused":
      toast("⏸ Compétition en pause");
      break;
    case "question_started":
      renderQuestion(message);
      break;
    case "question_ended":
      handleQuestionEnd();
      break;
    case "competition_finished":
      showResults();
      break;
    case "participant_joined":
    case "participant_left":
      fillWaitingRoom();
      break;
    case "error":
      handleWsError(message);
      break;
  }
}

function handleWsError(message) {
  if (message.code === "NOT_AUTHORIZED") {
    toast("Session expirée — reconnectez-vous.");
    localStorage.removeItem("quran.token");
    setTimeout(() => (location.href = "/join"), 800);
    return;
  }
  toast(message.message || "Erreur WebSocket.");
}

function handleState(state) {
  $("room-status").textContent = statusText(state.status);
  if (typeof state.participants_connected === "number") {
    $("room-players").textContent =
      "👥 " + state.participants_connected + " connectés";
  }
  if (state.status === "finished") showResults();
}

/* ---------------- question ---------------- */

function renderQuestion(message) {
  if (currentQuestion && currentQuestion.id === message.question_id) return;

  clearInterval(teller);
  currentQuestion = {
    id: message.question_id,
    duration: message.duration_seconds,
    answered: false,
  };

  show("view-question");
  $("q-position-chip").textContent = "Question " + message.position;
  $("q-text").textContent = message.text;

  const choicesBox = $("q-choices");
  const textInput = $("q-text-input");
  choicesBox.innerHTML = "";
  textInput.hidden = true;

  if (message.choices && message.choices.length) {
    choicesBox.hidden = false;
    choicesBox.classList.toggle("two-col", message.choices.length <= 2);
    message.choices.forEach((choice) => {
      const btn = document.createElement("button");
      btn.className = "choice-btn";
      btn.textContent = choice.text;
      btn.dataset.id = choice.id;
      btn.addEventListener("click", () => answerChoice(choice.id, btn));
      choicesBox.appendChild(btn);
    });
  } else {
    choicesBox.hidden = true;
    textInput.hidden = false;
    const field = $("answer-field");
    field.value = "";
    field.disabled = false;
    $("btn-answer-text").disabled = false;
  }

  $("q-answering").hidden = true;
  startCountdown(message.ends_at, message.started_at, message.duration_seconds);
}

function startCountdown(endsAt, startedAt, duration) {
  const bar = $("q-timer-bar");
  const text = $("q-timer-text");
  const timer = $("q-timer");
  bar.style.strokeDasharray = CIRC;
  timer.classList.remove("urgent");
  teller = serverCountdown(endsAt, startedAt, (remaining) => {
    const safe = Math.max(0, Math.floor(remaining / 1000) + 1);
    text.textContent = Math.min(safe, duration);
    bar.style.strokeDashoffset = CIRC * (1 - Math.min(safe, duration) / duration);
    if (safe <= 5) timer.classList.add("urgent");
  });
}

function lockInputs() {
  document.querySelectorAll(".choice-btn").forEach((b) => (b.disabled = true));
  const field = $("answer-field");
  field.disabled = true;
  $("btn-answer-text").disabled = true;
}

async function answerChoice(choiceId, button) {
  if (!currentQuestion || currentQuestion.answered) return;
  currentQuestion.answered = true;
  [].forEach.call(document.querySelectorAll(".choice-btn"), (b) => (b.disabled = true));
  button.classList.add("sel");
  $("q-answering").hidden = false;
  submit({ question_id: currentQuestion.id, choice_id: choiceId }, button);
}

async function answerText() {
  if (!currentQuestion || currentQuestion.answered) return;
  const value = $("answer-field").value.trim();
  if (!value) return;
  currentQuestion.answered = true;
  lockInputs();
  $("q-answering").hidden = false;
  submit({ question_id: currentQuestion.id, answer_text: value }, null);
}

$("btn-answer-text").addEventListener("click", answerText);

async function submit(payload, selectedButton) {
  try {
    const receipt = await API.post(
      "/api/competitions/" + COMPETITION_ID + "/answers",
      payload,
      token
    );
    lastAnsweredAt = { receipt, questionId: currentQuestion.id };
    if (selectedButton) {
      const buttons = document.querySelectorAll(".choice-btn");
      [].forEach.call(buttons, (b) => {
        if (b !== selectedButton) b.classList.add("bad");
      });
      selectedButton.classList.add(receipt.is_correct ? "ok" : "bad");
    }
    showFeedback(receipt);
  } catch (err) {
    if (err.code === "ALREADY_ANSWERED") {
      toast("Vous avez déjà répondu à cette question.");
      currentQuestion.answered = false;
      return;
    }
    if (err.code === "QUESTION_EXPIRED" || err.code === "QUESTION_NOT_ACTIVE") {
      toast("⏰ Trop tard — le temps est écoulé.");
      currentQuestion.answered = false;
      handleQuestionEnd();
      return;
    }
    toast(err.message || "Réponse refusée.");
    currentQuestion.answered = false;
    reenableInputs();
  }
}

function reenableInputs() {
  if ($("view-question").hidden) return;
  document.querySelectorAll(".choice-btn").forEach((b) => (b.disabled = false));
  $("answer-field").disabled = false;
  $("btn-answer-text").disabled = false;
  $("q-answering").hidden = true;
}

function handleQuestionEnd() {
  clearInterval(teller);
  lockInputs();

  const endedId = currentQuestion ? currentQuestion.id : null;
  if (lastAnsweredAt && lastAnsweredAt.questionId === endedId) {
    if (currentQuestion && !currentQuestion.answered) {
      showFeedback(lastAnsweredAt.receipt);
      currentQuestion.answered = true;
    }
    return;
  }
  if (currentQuestion && currentQuestion.answered) return;

  $("feedback-icon").textContent = "⏰";
  $("feedback-title").textContent = "Temps écoulé !";
  $("feedback-detail").textContent = "La question est close, sans réponse pour vous.";
  $("btn-continue").hidden = false;
  show("view-feedback");
}

function showFeedback(receipt) {
  clearInterval(teller);
  lockInputs();
  const icon = $("feedback-icon");
  if (receipt.is_correct === true) {
    icon.textContent = "✅";
    $("feedback-title").textContent = "Bonne réponse !";
    $("feedback-detail").textContent =
      "+" + receipt.points + " points" +
      (receipt.explanation ? " — " + receipt.explanation : "");
  } else if (receipt.is_correct === false) {
    icon.textContent = "❌";
    $("feedback-title").textContent = "Mauvaise réponse";
    $("feedback-detail").textContent =
      receipt.points + " points" +
      (receipt.explanation ? " — " + receipt.explanation : "");
  } else {
    icon.textContent = "📨";
    $("feedback-title").textContent = "Réponse enregistrée";
    $("feedback-detail").textContent = "La correction est en cours.";
  }
  currentQuestion.answered = true;
  $("btn-continue").hidden = false;
  $("btn-continue").onclick = () => {
    show("view-waiting");
    $("room-status").textContent = "Prêt pour la suite…";
  };
  show("view-feedback");
}

/* ---------------- results ---------------- */

async function showResults() {
  clearInterval(teller);
  const box = $("final-leaderboard");
  box.innerHTML = '<p class="hint">Chargement du classement…</p>';
  show("view-results");
  try {
    const rows = await API.get(
      "/api/competitions/" + COMPETITION_ID + "/leaderboard",
      token
    );
    let html =
      '<table class="leaderboard"><tr><th></th><th>Participant</th><th>Réussites</th><th>Score</th></tr>';
    rows.forEach((row) => {
      html +=
        '<tr class="' + (row.rank === 1 ? "top1" : "") + '">' +
        "<td class=\"rank\">" + row.rank + "</td>" +
        "<td>" + esc(row.display_name) + (row.display_name === myName ? " (vous)" : "") + "</td>" +
        "<td>" + row.correct_answers + "/" + row.answered_questions + "</td>" +
        "<td><strong>" + row.score + "</strong></td></tr>";
    });
    box.innerHTML = rows.length ? html : '<p class="hint">Aucune réponse enregistrée.</p>';
  } catch (err) {
    box.innerHTML = '<p class="hint">Classement indisponible (' + esc(err.message) + ")</p>";
  }
}