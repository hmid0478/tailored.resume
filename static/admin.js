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
    const name = document.getElementById("new-name").value.trim();
    const email = document.getElementById("new-email").value.trim();
    const password = document.getElementById("new-password").value;
    createBtn.disabled = true;
    createBtn.textContent = "Creating...";
    try {
      const res = await adminFetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
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
    table.innerHTML = "<thead><tr><th>Name</th><th>Email</th><th>Password</th><th>API keys</th><th></th></tr></thead>";
    const tbody = document.createElement("tbody");

    users.forEach((u) => {
      const tr = document.createElement("tr");

      // Name (editable)
      const tdName = document.createElement("td");
      const nameInput = document.createElement("input");
      nameInput.className = "user-edit-input";
      nameInput.value = u.name || "";
      nameInput.placeholder = "—";
      tdName.appendChild(nameInput);
      tr.appendChild(tdName);

      // Email (the key — read only) + created date under it
      const tdEmail = document.createElement("td");
      tdEmail.innerHTML =
        `<div class="user-email">${escapeHtml(u.email)}</div>` +
        `<div class="user-date">${escapeHtml(formatDate(u.created_at))}</div>`;
      tr.appendChild(tdEmail);

      // Password (visible + editable)
      const tdPass = document.createElement("td");
      const passInput = document.createElement("input");
      passInput.className = "user-edit-input mono";
      passInput.value = u.password || "";
      passInput.placeholder = "(set a password)";
      tdPass.appendChild(passInput);
      tr.appendChild(tdPass);

      // API keys (lazily loaded into a details row below this one)
      const tdKeys = document.createElement("td");
      const keysBtn = document.createElement("button");
      keysBtn.className = "btn btn-ghost";
      keysBtn.textContent = "View keys";
      tdKeys.appendChild(keysBtn);
      tr.appendChild(tdKeys);

      // Actions
      const tdAct = document.createElement("td");
      tdAct.style.textAlign = "right";
      tdAct.style.whiteSpace = "nowrap";

      const save = document.createElement("button");
      save.className = "btn btn-ghost";
      save.textContent = "Save";
      save.addEventListener("click", () => saveUser(u.email, nameInput.value.trim(), passInput.value, save));
      tdAct.appendChild(save);

      const del = document.createElement("button");
      del.className = "btn btn-danger";
      del.textContent = "Delete";
      del.style.marginLeft = "6px";
      del.addEventListener("click", () => deleteUser(u.email, del));
      tdAct.appendChild(del);

      tr.appendChild(tdAct);
      tbody.appendChild(tr);

      // Details row, hidden until "View keys" is clicked.
      const keysRow = document.createElement("tr");
      keysRow.style.display = "none";
      const keysCell = document.createElement("td");
      keysCell.colSpan = 5;
      keysRow.appendChild(keysCell);
      tbody.appendChild(keysRow);
      keysBtn.addEventListener("click", () => toggleKeys(u.email, keysRow, keysCell, keysBtn));
    });
    table.appendChild(tbody);
    usersContainer.innerHTML = "";
    usersContainer.appendChild(table);
  }

  // ── Per-user provider API keys (admin only) ──
  // The list view shows masked keys; the full value is fetched only when the admin
  // clicks Copy, and the server logs each of those reveals.
  async function toggleKeys(email, row, cell, btn) {
    if (row.style.display !== "none") {
      row.style.display = "none";
      btn.textContent = "View keys";
      cell.innerHTML = "";
      return;
    }
    row.style.display = "";
    btn.textContent = "Hide keys";
    cell.innerHTML = '<div class="keys-panel"><span class="spinner"></span> Loading keys…</div>';
    try {
      const res = await adminFetch("/api/admin/user-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Could not load keys.");
      renderKeys(email, cell, json);
    } catch (err) {
      cell.innerHTML = `<div class="keys-panel keys-empty">${escapeHtml(err.message)}</div>`;
    }
  }

  function renderKeys(email, cell, json) {
    const keys = json.keys || [];
    cell.innerHTML = "";
    const panel = document.createElement("div");
    panel.className = "keys-panel";
    if (!keys.length) {
      panel.className += " keys-empty";
      panel.textContent = "This user has not saved any provider API keys yet.";
      cell.appendChild(panel);
      return;
    }
    keys.forEach((k) => {
      const rowEl = document.createElement("div");
      rowEl.className = "key-row";

      const label = document.createElement("span");
      label.className = "key-provider";
      label.textContent = k.label + (k.provider === json.active_provider ? " (active)" : "");
      rowEl.appendChild(label);

      const val = document.createElement("code");
      val.className = "key-value";
      val.textContent = k.masked;
      rowEl.appendChild(val);

      const reveal = document.createElement("button");
      reveal.className = "btn btn-ghost";
      reveal.textContent = "Show";
      let shown = false;
      reveal.addEventListener("click", async () => {
        if (shown) {
          val.textContent = k.masked;
          reveal.textContent = "Show";
          shown = false;
          return;
        }
        const full = await fetchKey(email, k.provider, reveal);
        if (full == null) return;
        val.textContent = full;
        reveal.textContent = "Hide";
        shown = true;
      });
      rowEl.appendChild(reveal);

      const copy = document.createElement("button");
      copy.className = "btn btn-ghost";
      copy.textContent = "Copy";
      copy.addEventListener("click", async () => {
        const full = await fetchKey(email, k.provider, copy);
        if (full == null) return;
        const ok = await copyText(full);
        flash(copy, ok ? "Copied!" : "Copy failed", "Copy");
      });
      rowEl.appendChild(copy);

      panel.appendChild(rowEl);
    });
    cell.appendChild(panel);
  }

  async function fetchKey(email, provider, btn) {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const res = await adminFetch("/api/admin/user-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, provider, reveal: true }),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Could not read that key.");
      const entry = (json.keys || [])[0];
      if (!entry || !entry.key) throw new Error("That key is no longer saved.");
      return entry.key;
    } catch (err) {
      flash(btn, err.message.slice(0, 24), label);
      return null;
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  // navigator.clipboard needs a secure context — an admin on http://<lan-ip> doesn't
  // have one, so fall back to the old selection trick there.
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch { /* fall through */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }

  function flash(btn, text, restore) {
    btn.textContent = text;
    setTimeout(() => { btn.textContent = restore; }, 1600);
  }

  async function saveUser(email, name, password, btn) {
    // POST with the email in the body — PUT + email-in-URL breaks on some hosts.
    const body = { email, name };
    if (password && password.trim()) body.password = password.trim();
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      const res = await adminFetch("/api/admin/users/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Could not update user.");
      btn.textContent = "Saved ✓";
      setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 1200);
    } catch (err) {
      alert(err.message);
      btn.textContent = label;
      btn.disabled = false;
    }
  }

  async function deleteUser(email, btn) {
    if (!confirm(`Delete ${email}? This permanently removes the user and all of their saved resumes.`)) return;
    btn.disabled = true;
    btn.textContent = "Deleting...";
    try {
      const res = await adminFetch("/api/admin/users/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
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
      const unlock = () => {
        el.removeAttribute("readonly");
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

  // ── Boot ──
  hardenAgainstAutofill(["admin-email", "admin-password", "new-name", "new-email", "new-password"]);
  if (token()) {
    showDash();
  } else {
    showLogin();
  }
})();
