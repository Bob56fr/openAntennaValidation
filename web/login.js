"use strict";

(async function () {
  const res = await fetch("/api/session");
  const session = await res.json();
  if (session.authenticated) {
    location.replace("/");
    return;
  }
  document.getElementById("no-password").hidden = session.password_set;
})();

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("login-error");
  error.hidden = true;
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: document.getElementById("password").value }),
  });
  if (res.ok) {
    location.replace("/");
  } else {
    const body = await res.json().catch(() => ({}));
    error.textContent = body.detail || "Connexion refusée.";
    error.hidden = false;
  }
});
