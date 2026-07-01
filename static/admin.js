/* Admin portal: fixed-credential login + user management. */
(function () {
  const TOKEN_KEY = "rt_admin_token";
  const EMAIL_KEY = "rt_admin_email";

  const loginView = document.getElementById("admin-login-view");
  const dashView = document.getElementById("admin-dash-view");

  const loginForm = document.getElementById("admin-login-form");
  const loginMsg = document.getElementById("admin-login-msg");
  const loginBtn = document.getElementById("admin-login-btn");

  const createForm = document.getElementById("create-user-form");
  const createMsg = document.getElementById("create-msg");
  const createBtn = document.getElementById("create-btn");
  const usersContainer = document.getElementById("users-container");
  const userCount = document.getElementById("user-count");
  const adminWho = document.getElementById("admin-who");

  function token() { return localStorage.getItem(TOKEN_KEY); }

  function setMsg(el, text, kind) {
    el.textContent = text;
    el.className = "auth-msg " + (kind || "");
  }

  function showLogin() {
    dashView.style.display = "none";
    loginView.style.display = "";
  }

  function showDash() {
    loginView.style.display = "none";
    dashView.style.display = "";
    adminWho.textContent = localStorage.getItem(EMAIL_KEY) || "";
    loadUsers();
    loadStorageHealth();
  }

  async function loadStorageHealth() {
    const banner = document.getElementById("storage-banner");
    if (!banner) return;
    try {
      const res = await adminFetch("/api/admin/health");
      const json = await res.json();
      const s = (json && json.storage) || {};
      if (s.backend === "redis" && s.reachable) {
        banner.className = "storage-banner ok";
        banner.textContent = "✓ Storage: Upstash Redis — users & data persist across redeploys.";
        banner.style.display = "";
      } else if (s.backend === "redis" && !s.reachable) {
        banner.className = "storage-banner warn";
        banner.textContent = "⚠ Redis is configured but not reachable — check your Upstash credentials." +
          (s.error ? " (" + s.error + ")" : "");
        banner.style.display = "";
      } else if (!s.persistent) {
        banner.className = "storage-banner warn";
        banner.textContent = "⚠ Storage is a TEMPORARY file — users you create will be LOST on the next " +
          "redeploy. Connect Upstash Redis in Vercel (Storage tab) and redeploy to fix this.";
        banner.style.display = "";
      } else {
        banner.style.display = "none";
      }
    } catch (err) {
      // Non-fatal — just don't show the banner.
      banner.style.display = "none";
    }
  }

  async function adminFetch(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers, {
      Authorization: "Bearer " + token(),
    });
    const res = await fetch(url, opts);
    if (res.status === 401) {
      // Token missing/expired — force re-login.
      localStorage.removeItem(TOKEN_KEY);
      showLogin();
      throw new Error("Your admin session expired. Please sign in again.");
    }
    return res;
  }

  // ── Login ──
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    setMsg(loginMsg, "", "");
    const email = document.getElementById("admin-email").value.trim();
    const password = document.getElementById("admin-password").value;
    loginBtn.disabled = true;
    loginBtn.textContent = "Signing in...";
    try {
      const res = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Login failed.");
      localStorage.setItem(TOKEN_KEY, json.token);
      localStorage.setItem(EMAIL_KEY, json.email);
      showDash();
    } catch (err) {
      setMsg(loginMsg, err.message, "error");
    } finally {
      loginBtn.disabled = false;
      loginBtn.textContent = "Sign in as admin";
    }
  });

  // ── Logout ──
  document.getElementById("admin-logout-btn").addEventListener("click", () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
    showLogin();
  });

  // ── Create user ──
  createForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    setMsg(createMsg, "", "");
    const email = document.getElementById("new-email").value.trim();
    const password = document.getElementById("new-password").value;
    createBtn.disabled = true;
    createBtn.textContent = "Creating...";
    try {
      const res = await adminFetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Could not create user.");
      setMsg(createMsg, `Created ${json.email}.`, "success");
      createForm.reset();
      loadUsers();
    } catch (err) {
      setMsg(createMsg, err.message, "error");
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = "Create user";
    }
  });

  // ── Load + render users ──
  async function loadUsers() {
    usersContainer.innerHTML = '<div class="users-empty"><span class="spinner"></span> Loading…</div>';
    try {
      const res = await adminFetch("/api/admin/users");
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Could not load users.");
      renderUsers(json.users || []);
    } catch (err) {
      usersContainer.innerHTML = `<div class="users-empty">${escapeHtml(err.message)}</div>`;
    }
  }

  function renderUsers(users) {
    userCount.textContent = users.length;
    if (!users.length) {
      usersContainer.innerHTML = '<div class="users-empty">No users yet. Create one on the left.</div>';
      return;
    }
    const table = document.createElement("table");
    table.className = "user-table";
    table.innerHTML = "<thead><tr><th>Email</th><th>Created</th><th></th></tr></thead>";
    const tbody = document.createElement("tbody");

    users.forEach((u) => {
      const tr = document.createElement("tr");

      const tdEmail = document.createElement("td");
      tdEmail.className = "user-email";
      tdEmail.textContent = u.email;
      tr.appendChild(tdEmail);

      const tdDate = document.createElement("td");
      tdDate.className = "user-date";
      tdDate.textContent = formatDate(u.created_at);
      tr.appendChild(tdDate);

      const tdAct = document.createElement("td");
      tdAct.style.textAlign = "right";
      const del = document.createElement("button");
      del.className = "btn btn-danger";
      del.textContent = "Delete";
      del.addEventListener("click", () => deleteUser(u.email, del));
      tdAct.appendChild(del);
      tr.appendChild(tdAct);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    usersContainer.innerHTML = "";
    usersContainer.appendChild(table);
  }

  async function deleteUser(email, btn) {
    if (!confirm(`Delete ${email}? This permanently removes the user and all of their saved resumes.`)) return;
    btn.disabled = true;
    btn.textContent = "Deleting...";
    try {
      const res = await adminFetch("/api/admin/users/" + encodeURIComponent(email), { method: "DELETE" });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Could not delete user.");
      loadUsers();
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
      btn.textContent = "Delete";
    }
  }

  function formatDate(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
    } catch { return iso; }
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
  }

  // Defeat browser autofill so login/create-user boxes start empty (see login.js).
  function hardenAgainstAutofill(ids) {
    const els = ids.map((id) => document.getElementById(id)).filter(Boolean);
    els.forEach((el) => {
      el.setAttribute("readonly", "readonly");
      const unlock = () => el.removeAttribute("readonly");
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

  // ── Boot ──
  hardenAgainstAutofill(["admin-email", "admin-password", "new-email", "new-password"]);
  if (token()) {
    showDash();
  } else {
    showLogin();
  }
})();
