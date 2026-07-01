/* User login page logic. */
(function () {
  // Already signed in? Skip straight to the app.
  if (localStorage.getItem("rt_auth_token")) {
    window.location.replace("/");
    return;
  }

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
      window.location.replace("/");
    } catch (err) {
      showError(err.message);
      btn.disabled = false;
      btn.textContent = "Sign in";
    }
  });
})();
