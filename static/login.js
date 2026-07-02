/* User login page logic. */
(function () {
  // Already signed in? Skip straight to the app.
  if (localStorage.getItem("rt_auth_token")) {
    window.location.replace("/");
    return;
  }

  hardenAgainstAutofill(["email", "password"]);

  const form = document.getElementById("login-form");
  const msg = document.getElementById("auth-msg");
  const btn = document.getElementById("login-btn");

  function showError(text) {
    msg.textContent = text;
    msg.className = "auth-msg error";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.className = "auth-msg";
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    if (!email || !password) return showError("Please enter your email and password.");

    btn.disabled = true;
    btn.textContent = "Signing in...";
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Login failed.");

      localStorage.setItem("rt_auth_token", json.token);
      localStorage.setItem("rt_auth_email", json.email);
      localStorage.setItem("rt_auth_name", json.name || "");
      window.location.replace("/");
    } catch (err) {
      showError(err.message);
      btn.disabled = false;
      btn.textContent = "Sign in";
    }
  });
})();

/* Defeat browser autofill so the login boxes start empty. Fields begin read-only
   (browsers won't autofill a read-only field on load) and unlock on first focus;
   any value the browser injected is cleared unless the user has already typed. */
function hardenAgainstAutofill(ids) {
  const els = ids.map((id) => document.getElementById(id)).filter(Boolean);
  els.forEach((el) => {
    el.setAttribute("readonly", "readonly");
    const unlock = () => {
      el.removeAttribute("readonly");
      // Wipe anything the browser injected the instant the user engages (before they type).
      if (el.dataset.touched !== "1") el.value = "";
    };
    el.addEventListener("focus", unlock, { once: true });
    el.addEventListener("mousedown", unlock, { once: true });
    el.addEventListener("input", () => { el.dataset.touched = "1"; });
  });
  const clear = () => els.forEach((el) => {
    if (document.activeElement !== el && el.dataset.touched !== "1") el.value = "";
  });
  clear();
  setTimeout(clear, 150);
  setTimeout(clear, 500);
}
