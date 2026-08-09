/* Join page: POST /api/competitions/join then go to the room. */

"use strict";

const codeInput = document.getElementById("join-code");
const nameInput = document.getElementById("join-name");
const btn = document.getElementById("btn-join");
const errBox = document.getElementById("join-error");

btn.addEventListener("click", join);
nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") join(); });
codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") join(); });

async function join() {
  errBox.hidden = true;
  const code = codeInput.value.trim().toUpperCase();
  const name = nameInput.value.trim();
  if (!code || !name) { showError("Entrez le code et votre nom."); return; }
  btn.disabled = true;
  btn.textContent = "Entrée…";
  try {
    const data = await API.post("/api/competitions/join", {
      competition_code: code,
      display_name: name,
    });
    localStorage.setItem("quran.token", data.access_token);
    localStorage.setItem("quran.name", data.display_name);
    localStorage.setItem("quran.competition", data.competition_id);
    location.href = "/room/" + data.competition_id;
  } catch (err) {
    showError(err.message);
    btn.disabled = false;
    btn.textContent = "Entrer";
  }
}

function showError(message) {
  errBox.textContent = message;
  errBox.hidden = false;
  btn.disabled = false;
  btn.textContent = "Entrer";
}