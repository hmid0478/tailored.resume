/* ─────────────────────────────────────────────
   Resume Tailor — Frontend Logic
   ───────────────────────────────────────────── */

// ── Auth gate ──
// The app is private: without a valid token we bounce to the login page.
// Throwing here stops the rest of this script from running while we navigate away.
const AUTH_TOKEN = localStorage.getItem("rt_auth_token");
const AUTH_EMAIL = (localStorage.getItem("rt_auth_email") || "").trim().toLowerCase();
if (!AUTH_TOKEN) {
  window.location.replace("/login");
  throw new Error("Not authenticated — redirecting to /login");
}
// Every localStorage key the app writes is namespaced per user, so two people
// sharing a browser never see each other's JD, keys, or results.
const USER_PREFIX = "u:" + AUTH_EMAIL + ":";

// Fetch wrapper that attaches the bearer token and handles session expiry.
// IMPORTANT: only a *session* 401 (tagged `auth: "required"` by the backend) should
// log the user out. A 401 from an AI provider (invalid API key) must NOT — otherwise
// a bad key would kick the user to the login page instead of showing the real error.
async function authFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers, { Authorization: "Bearer " + AUTH_TOKEN });
  const res = await fetch(url, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    let isSessionExpiry = false;
    try {
      const data = await res.clone().json();
      isSessionExpiry = data && data.auth === "required";
    } catch { /* non-JSON 401 — treat as a normal error, not a logout */ }
    if (isSessionExpiry) {
      localStorage.removeItem("rt_auth_token");
      localStorage.removeItem("rt_auth_email");
      window.location.replace("/login");
      throw new Error("Your session expired. Please log in again.");
    }
  }
  return res;
}

let tailoredData = null;
let currentResumeId = null;  // server id of the resume currently in the preview
let savedJDText = "";  // Preserved JD context for Q&A
let scrapedMetadata = null;  // (legacy) metadata holder — kept null now that scraping is gone
let lastJobMeta = { company: "", position: "" };  // Company/title extracted from the JD

// ── LocalStorage persistence ──
const STORAGE_KEYS = {
  apifyKey: "rt_apify_key",
  provider: "rt_provider",
  providerModels: "rt_provider_models",   // {provider: lastModel}
  providerKeys: "rt_provider_keys",       // {provider: apiKey}
  providerBaseUrls: "rt_provider_base_urls", // {provider: baseUrl}
  jd: "rt_jd",
  jdUrl: "rt_jd_url",
  promptText: "rt_prompt_text",
  tailoredData: "rt_tailored_data",
  savedJD: "rt_saved_jd",
  resumeFileName: "rt_resume_file_name",
  resumeFileContent: "rt_resume_file_content",
  promptFileName: "rt_prompt_file_name",
  promptFileContent: "rt_prompt_file_content",
  promptMode: "rt_prompt_mode",  // "upload" or "paste"
  pdfTemplate: "rt_pdf_template",
  batchJobs: "rt_batch_jobs",
  dlCompany: "rt_dl_company",
  dlTitle: "rt_dl_title",
  jobMeta: "rt_job_meta",
};

// Job identity (company + job title) extracted from the JD, used for auto-naming.
function normalizeJobMeta(meta) {
  meta = meta || {};
  return { company: (meta.company || "").trim(), position: (meta.position || "").trim() };
}

// Push the JD-derived company/title into the download filename fields, unless the
// user has manually typed something there. Empty-field rules are handled by
// buildResumeFilename (company only / title only / blank).
function applyJobMetaToFilenameFields() {
  if (dlCompanyInput && dlCompanyInput.dataset.userEdited !== "1") {
    dlCompanyInput.value = lastJobMeta.company || "";
  }
  if (dlTitleInput && dlTitleInput.dataset.userEdited !== "1") {
    dlTitleInput.value = lastJobMeta.position || "";
  }
  _updateFilenamePreview();
}

// Provider metadata: default model, key placeholder, whether base_url is shown.
const PROVIDER_META = {
  anthropic:  { label: "Anthropic API Key", model: "claude-sonnet-4-5-20250929",   keyPh: "sk-ant-api03-...",  needsBase: false, defaultBase: "" },
  gemini:     { label: "Gemini API Key",    model: "gemini-2.5-flash-lite",        keyPh: "AIza...",           needsBase: false, defaultBase: "" },
  openai:     { label: "OpenAI API Key",    model: "gpt-4o-mini",                  keyPh: "sk-...",            needsBase: false, defaultBase: "" },
  openrouter: { label: "OpenRouter API Key", model: "openrouter/auto",             keyPh: "sk-or-...",         needsBase: false, defaultBase: "" },
  groq:       { label: "Groq API Key",      model: "llama-3.3-70b-versatile",      keyPh: "gsk_...",           needsBase: false, defaultBase: "" },
  together:   { label: "Together AI API Key", model: "meta-llama/Llama-3.3-70B-Instruct-Turbo", keyPh: "...",  needsBase: false, defaultBase: "" },
  ollama:     { label: "API Key (optional)", model: "llama3.1",                    keyPh: "(leave blank for local)", needsBase: true, defaultBase: "http://localhost:11434/v1" },
  openai_compatible: { label: "API Key",    model: "",                             keyPh: "...",               needsBase: true, defaultBase: "http://localhost:1234/v1" },
};

// All app state is namespaced per signed-in user (see USER_PREFIX).
function _nsKey(key) { return USER_PREFIX + key; }

function saveToStorage(key, value) {
  try { localStorage.setItem(_nsKey(key), typeof value === "string" ? value : JSON.stringify(value)); } catch {}
}

function loadFromStorage(key) {
  try { return localStorage.getItem(_nsKey(key)); } catch { return null; }
}

function loadJSONFromStorage(key) {
  try {
    const raw = localStorage.getItem(_nsKey(key));
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

// ── DOM refs ──
const form = document.getElementById("tailor-form");
const resumeFileInput = document.getElementById("resume-file");
const fileNameDisplay = document.getElementById("file-name");
const uploadZone = document.getElementById("upload-zone");
const promptFileInput = document.getElementById("prompt-file");
const promptFileNameDisplay = document.getElementById("prompt-file-name");
const promptUploadZone = document.getElementById("prompt-upload-zone");
const loadingOverlay = document.getElementById("loading-overlay");
const loadingStep = document.getElementById("loading-step");
const resultsSection = document.getElementById("results-section");
const errorToast = document.getElementById("error-toast");
const downloadBtn = document.getElementById("download-btn");

// ── LinkedIn quick-copy ──
// LinkedIn bar: save on edit, copy on click
const linkedinInput = document.getElementById("linkedin-url");
linkedinInput.addEventListener("input", () => {
  saveToStorage("rt_linkedin_url", linkedinInput.value);
});

document.getElementById("linkedin-copy-btn").addEventListener("click", async () => {
  const url = document.getElementById("linkedin-url").value;
  const btn = document.getElementById("linkedin-copy-btn");
  try {
    await navigator.clipboard.writeText(url);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = url;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  btn.textContent = "Copied!";
  btn.classList.add("copied");
  setTimeout(() => {
    btn.textContent = "Copy";
    btn.classList.remove("copied");
  }, 2000);
});

// Setup section refs
const setupSection = document.getElementById("setup-section");
const setupHeader = document.getElementById("setup-header");
const setupStatus = document.getElementById("setup-status");
const setupSubtitle = document.getElementById("setup-subtitle");

// ── Setup section collapse/expand ──
setupHeader.addEventListener("click", () => {
  setupSection.classList.toggle("collapsed");
});

function updateSetupStatus() {
  const hasResume = resumeFileInput.files.length > 0 || loadFromStorage(STORAGE_KEYS.resumeFileName);
  const promptUploadActive = document.getElementById("prompt-upload-mode").classList.contains("active");
  const hasPrompt = promptUploadActive
    ? (promptFileInput.files.length > 0 || loadFromStorage(STORAGE_KEYS.promptFileName))
    : document.getElementById("prompt-text").value.trim().length > 0;

  if (hasResume) {
    setupStatus.textContent = "Ready";
    setupStatus.className = "setup-status ready";
    setupSection.classList.add("configured");
    setupSubtitle.textContent = hasPrompt
      ? "Resume & prompt configured — click to edit"
      : "Resume configured (using default prompt) — click to edit";
    // Auto-collapse after first successful setup
    if (!setupSection.dataset.userExpanded) {
      setupSection.classList.add("collapsed");
    }
  } else {
    setupStatus.textContent = "Setup needed";
    setupStatus.className = "setup-status incomplete";
    setupSection.classList.remove("configured", "collapsed");
    setupSubtitle.textContent = "Missing: resume";
  }
}

// Track if user manually expanded
setupHeader.addEventListener("click", () => {
  if (!setupSection.classList.contains("collapsed")) {
    setupSection.dataset.userExpanded = "true";
  } else {
    delete setupSection.dataset.userExpanded;
  }
});

// ── Read file as base64 for localStorage persistence ──
function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function base64ToBlob(dataURL) {
  const [header, data] = dataURL.split(",");
  const mime = header.match(/:(.*?);/)[1];
  const binary = atob(data);
  const array = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) array[i] = binary.charCodeAt(i);
  return new Blob([array], { type: mime });
}

function createFileFromStorage(storageKeyName, storageKeyContent) {
  const name = loadFromStorage(storageKeyName);
  const content = loadFromStorage(storageKeyContent);
  if (!name || !content) return null;
  const blob = base64ToBlob(content);
  return new File([blob], name, { type: blob.type });
}

// ── Generic drag-and-drop helper ──
function setupUploadZone(zone, fileInput, nameDisplay, fileNameKey, fileContentKey) {
  async function handleFile() {
    if (fileInput.files.length > 0) {
      const file = fileInput.files[0];
      nameDisplay.textContent = file.name;
      nameDisplay.style.display = "block";
      // Persist file to localStorage
      saveToStorage(fileNameKey, file.name);
      const b64 = await readFileAsBase64(file);
      saveToStorage(fileContentKey, b64);
      updateSetupStatus();
    }
  }

  fileInput.addEventListener("change", handleFile);

  ["dragenter", "dragover"].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    });
  });

  zone.addEventListener("drop", (e) => {
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      // Create a new DataTransfer to set files
      const dt = new DataTransfer();
      dt.items.add(files[0]);
      fileInput.files = dt.files;
      handleFile();
    }
  });
}

// Wire up both upload zones with persistence keys
setupUploadZone(uploadZone, resumeFileInput, fileNameDisplay,
  STORAGE_KEYS.resumeFileName, STORAGE_KEYS.resumeFileContent);
setupUploadZone(promptUploadZone, promptFileInput, promptFileNameDisplay,
  STORAGE_KEYS.promptFileName, STORAGE_KEYS.promptFileContent);

// ── Upload / Paste toggle ──
document.querySelectorAll(".input-toggle").forEach((toggle) => {
  toggle.querySelectorAll(".toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      toggle.querySelectorAll(".toggle-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const card = toggle.closest(".input-card");
      card.querySelectorAll(".input-mode").forEach((m) => m.classList.remove("active"));
      document.getElementById(btn.dataset.target).classList.add("active");
      // Save prompt mode preference
      const mode = btn.dataset.target === "prompt-upload-mode" ? "upload" : "paste";
      saveToStorage(STORAGE_KEYS.promptMode, mode);
      updateSetupStatus();
    });
  });
});

// ── Provider / model / key handling ──
const providerSelect = document.getElementById("ai-provider");
const modelInput = document.getElementById("ai-model");
const keyInput = document.getElementById("provider-key");
const keyLabelEl = document.getElementById("provider-key-label");
const baseUrlInput = document.getElementById("base-url");
const baseUrlGroup = document.getElementById("base-url-group");

function getProviderMeta(provider) {
  return PROVIDER_META[provider] || PROVIDER_META.anthropic;
}

function getProviderMap(storageKey) {
  return loadJSONFromStorage(storageKey) || {};
}

function setProviderMap(storageKey, provider, value) {
  const map = getProviderMap(storageKey);
  map[provider] = value;
  saveToStorage(storageKey, map);
}

function updateProviderUI() {
  const provider = providerSelect.value;
  const meta = getProviderMeta(provider);

  // Key input label + placeholder
  keyLabelEl.textContent = meta.label;
  keyInput.placeholder = meta.keyPh;

  // Restore stored values for this provider
  const keys = getProviderMap(STORAGE_KEYS.providerKeys);
  const models = getProviderMap(STORAGE_KEYS.providerModels);
  const baseUrls = getProviderMap(STORAGE_KEYS.providerBaseUrls);

  keyInput.value = keys[provider] || "";
  modelInput.value = models[provider] || meta.model || "";
  modelInput.placeholder = meta.model || "model name";

  // Base URL: only show for providers that need a custom endpoint
  if (meta.needsBase) {
    baseUrlGroup.style.display = "";
    baseUrlInput.value = baseUrls[provider] || meta.defaultBase || "";
    baseUrlInput.placeholder = meta.defaultBase || "https://...";
  } else {
    baseUrlGroup.style.display = "none";
    baseUrlInput.value = "";
  }
}

providerSelect.addEventListener("change", () => {
  saveToStorage(STORAGE_KEYS.provider, providerSelect.value);
  updateProviderUI();
  pushSettingsToServer();
});

function getActiveApiKey() { return keyInput.value.trim(); }
function getActiveModel() { return modelInput.value.trim(); }
function getActiveBaseUrl() { return baseUrlInput.value.trim(); }
function getActiveProvider() { return providerSelect.value; }

// ── Auto-save text inputs ──
keyInput.addEventListener("input", () => {
  setProviderMap(STORAGE_KEYS.providerKeys, providerSelect.value, keyInput.value);
  pushSettingsToServer();
});
modelInput.addEventListener("input", () => {
  setProviderMap(STORAGE_KEYS.providerModels, providerSelect.value, modelInput.value);
  pushSettingsToServer();
});
baseUrlInput.addEventListener("input", () => {
  setProviderMap(STORAGE_KEYS.providerBaseUrls, providerSelect.value, baseUrlInput.value);
  pushSettingsToServer();
});
document.getElementById("jd-text").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.jd, e.target.value);
});
document.getElementById("prompt-text").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.promptText, e.target.value);
  updateSetupStatus();
});

// ── Loading steps animation ──
const loadingSteps = [
  "Parsing your resume...",
  "Analyzing the job description...",
  "Extracting hard requirements & hidden keywords...",
  "Mapping skills to JD priorities...",
  "Rewriting summary to match the role...",
  "Reframing experience bullets with power verbs...",
  "Running keyword density checks...",
  "Generating change log & interview prep...",
  "Polishing final output...",
];

let stepInterval = null;

function startLoadingSteps() {
  let i = 0;
  loadingStep.textContent = loadingSteps[0];
  stepInterval = setInterval(() => {
    i = (i + 1) % loadingSteps.length;
    loadingStep.textContent = loadingSteps[i];
  }, 4000);
}

function stopLoadingSteps() {
  if (stepInterval) clearInterval(stepInterval);
}

// ── Show error ──
function showError(msg) {
  errorToast.textContent = msg;
  errorToast.style.display = "block";
  setTimeout(() => {
    errorToast.style.display = "none";
  }, 6000);
}

// ── Restore file from localStorage into a file input ──
function restoreFileInput(fileInput, nameDisplay, fileNameKey, fileContentKey) {
  const storedName = loadFromStorage(fileNameKey);
  const storedContent = loadFromStorage(fileContentKey);
  if (storedName && storedContent) {
    nameDisplay.textContent = storedName;
    nameDisplay.style.display = "block";
    // Reconstruct the File and set it on the input
    try {
      const blob = base64ToBlob(storedContent);
      const file = new File([blob], storedName, { type: blob.type });
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
    } catch {}
  }
}

// ── Form submit ──
form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const provider = getActiveProvider();
  const apiKey = getActiveApiKey();
  const model = getActiveModel();
  const baseUrl = getActiveBaseUrl();
  const jd = document.getElementById("jd-text").value.trim();

  const meta = getProviderMeta(provider);
  const isLocal = provider === "ollama" || (provider === "openai_compatible" && baseUrl.includes("localhost"));
  if (!apiKey && !isLocal) return showError(`Please enter your ${meta.label}.`);
  if (meta.needsBase && !baseUrl) return showError("Please enter the Base URL for this provider.");
  if (!jd) return showError("Please paste the job description.");
  if (!resumeFileInput.files.length) return showError("Please upload your resume file in the 'Your Profile' section.");

  // Determine prompt source: file upload or pasted text
  const promptUploadActive = document.getElementById("prompt-upload-mode").classList.contains("active");
  const promptText = document.getElementById("prompt-text").value.trim();
  const hasPromptFile = promptFileInput.files.length > 0;

  // Prompt is optional — the backend has a built-in default

  const fd = new FormData();
  fd.append("provider", provider);
  fd.append("api_key", apiKey);
  if (model) fd.append("model", model);
  if (baseUrl) fd.append("base_url", baseUrl);
  fd.append("jd", jd);
  fd.append("resume_file", resumeFileInput.files[0]);

  if (promptUploadActive && hasPromptFile) {
    fd.append("prompt_file", promptFileInput.files[0]);
  } else if (promptText) {
    fd.append("prompt", promptText);
  }
  // If neither is provided, the backend uses its built-in default prompt

  // Show loading
  loadingOverlay.classList.add("active");
  resultsSection.classList.remove("active");
  startLoadingSteps();

  try {
    const res = await authFetch("/api/tailor", {
      method: "POST",
      body: fd,
    });

    const json = await res.json();

    if (!res.ok || json.error) {
      throw new Error(json.error || "Unknown error");
    }

    tailoredData = json.data;
    savedJDText = jd;
    lastJobMeta = normalizeJobMeta(json.job_meta);
    saveToStorage(STORAGE_KEYS.tailoredData, tailoredData);
    saveToStorage(STORAGE_KEYS.savedJD, savedJDText);
    saveToStorage(STORAGE_KEYS.jobMeta, lastJobMeta);
    applyJobMetaToFilenameFields();
    renderResults(tailoredData);
    resultsSection.classList.add("active");

    // Persist to the user's private, server-side resume library.
    currentResumeId = null;
    saveResumeToServer(jd);

    setTimeout(() => {
      resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
  } catch (err) {
    showError(err.message);
  } finally {
    loadingOverlay.classList.remove("active");
    stopLoadingSteps();
  }
});

// ── Filename helpers ──
const dlCompanyInput = document.getElementById("dl-company");
const dlTitleInput = document.getElementById("dl-title");

function _sanitizeFilenamePart(s) {
  // Strip characters Windows/macOS/Linux disallow in filenames + collapse whitespace.
  return (s || "").replace(/[<>:"/\\|?*\x00-\x1F]/g, "").replace(/\s+/g, " ").trim();
}

function _autoCompanyName() {
  if (scrapedMetadata && scrapedMetadata.company) return scrapedMetadata.company;
  return "";
}

function getDlCompany() {
  return (dlCompanyInput && dlCompanyInput.value || "").trim();
}
function getDlTitle() {
  return (dlTitleInput && dlTitleInput.value || "").trim();
}

// The candidate's name, taking any manual edit into account.
function _resumeName() {
  const edited = (document.getElementById("edit-name")?.value || "").trim();
  return edited || (tailoredData && tailoredData.name) || "";
}

// PDF filename is "{Candidate Name} - {Job Title}.pdf" (company is only used to
// organize resumes in My Resumes, not in the downloaded file name).
function buildResumeFilename() {
  const name = _sanitizeFilenamePart(_resumeName());
  const title = _sanitizeFilenamePart(getDlTitle());
  let stem;
  if (name && title) stem = `${name} - ${title}`;
  else if (name)     stem = name;
  else if (title)    stem = title;
  else               stem = "Resume";
  return `${stem}.pdf`;
}

function _updateFilenamePreview() {
  const previewEl = document.getElementById("filename-preview");
  if (previewEl) previewEl.textContent = buildResumeFilename();
}

// Track manual edits so re-renders don't clobber them; persist values across reloads.
function _wireFilenameField(input, storageKey) {
  if (!input) return;
  const prior = loadFromStorage(storageKey);
  if (prior != null && prior !== "") {
    input.value = prior;
    input.dataset.userEdited = "1";
  }
  input.addEventListener("input", () => {
    input.dataset.userEdited = input.value.trim() ? "1" : "";
    saveToStorage(storageKey, input.value);
    _updateFilenamePreview();
  });
}
_wireFilenameField(dlCompanyInput, STORAGE_KEYS.dlCompany);
_wireFilenameField(dlTitleInput, STORAGE_KEYS.dlTitle);

function refreshFilenameInput() {
  // Auto-fill from the JD-derived company/title, only when the user hasn't typed
  // anything manually. Empty stays empty (user fills it in).
  if (dlCompanyInput && dlCompanyInput.dataset.userEdited !== "1") {
    dlCompanyInput.value = lastJobMeta.company || _autoCompanyName() || "";
  }
  if (dlTitleInput && dlTitleInput.dataset.userEdited !== "1") {
    dlTitleInput.value = lastJobMeta.position || "";
  }
  _updateFilenamePreview();
}

// ── Render Results ──
function renderResults(data) {
  const preview = document.getElementById("resume-preview");
  preview.innerHTML = buildResumeHTML(data);
  refreshFilenameInput();

  // Populate editable contact fields
  const editName = document.getElementById("edit-name");
  const editContact = document.getElementById("edit-contact");
  const editLinkedin = document.getElementById("edit-linkedin");

  if (editName) editName.value = data.name || "";

  // Split contact into non-URL parts and LinkedIn URL
  const contact = data.contact || "";
  const parts = contact.split("|").map((p) => p.trim());
  const linkedinPart = parts.find((p) => p.toLowerCase().includes("linkedin") || p.includes("linkedin.com"));
  const otherParts = parts.filter((p) => p !== linkedinPart);

  if (editContact) editContact.value = otherParts.join(" | ");
  if (editLinkedin) editLinkedin.value = linkedinPart || "";
}

function buildResumeHTML(d) {
  let html = "";
  html += `<div class="r-name">${esc(d.name || "")}</div>`;
  html += `<div class="r-title">${esc(d.title || "")}</div>`;
  html += `<div class="r-contact">${linkify(d.contact || "")}</div>`;

  // Helper: safely convert any value to an array
  function toArray(val) {
    if (Array.isArray(val)) return val;
    if (val && typeof val === "object") return [val];
    if (typeof val === "string") return [val];
    return [];
  }

  // Dynamic sections format
  if (d.sections && Array.isArray(d.sections)) {
    d.sections.forEach((section) => {
      const heading = section.heading || "";
      const type = section.type || "simple_list";
      const items = toArray(section.items);

      if (type === "text") {
        html += `<div class="r-section-header">${esc(heading)}</div>`;
        html += `<div class="r-summary">${esc(section.content || "")}</div>`;

      } else if (type === "skills") {
        html += `<div class="r-section-header">${esc(heading)}</div>`;
        items.forEach((s) => {
          if (typeof s === "string") {
            html += `<div class="r-skill-row">${esc(s)}</div>`;
          } else {
            html += `<div class="r-skill-row"><strong>${esc(s.category || "")}:</strong> ${esc(s.items || "")}</div>`;
          }
        });

      } else if (type === "experience") {
        html += `<div class="r-section-header">${esc(heading)}</div>`;
        items.forEach((job) => {
          html += `<div class="r-job-title">${esc(job.company || "")} - ${esc(job.job_title || "")}</div>`;
          html += `<div class="r-job-meta">${esc(job.dates || "")}</div>`;
          html += `<ul class="r-bullets">`;
          toArray(job.bullets).forEach((b) => {
            html += `<li>${esc(b)}</li>`;
          });
          html += `</ul>`;
        });

      } else if (type === "education") {
        html += `<div class="r-section-header">${esc(heading)}</div>`;
        items.forEach((edu) => {
          if (typeof edu === "string") {
            html += `<div class="r-edu-degree">${esc(edu)}</div>`;
          } else {
            html += `<div class="r-edu-degree">${esc(edu.degree || "")}</div>`;
            html += `<div class="r-edu-meta">${esc(edu.school || "")} | ${esc(edu.dates || "")} | ${esc(edu.location || "")}</div>`;
          }
        });

      } else if (type === "projects") {
        html += `<div class="r-section-header">${esc(heading)}</div>`;
        items.forEach((proj) => {
          html += `<div class="r-job-title">${esc(proj.name || "")}</div>`;
          if (proj.technologies) {
            html += `<div class="r-job-meta">${esc(proj.technologies)}</div>`;
          }
          if (proj.description) {
            html += `<div class="r-summary">${esc(proj.description)}</div>`;
          }
          html += `<ul class="r-bullets">`;
          toArray(proj.bullets).forEach((b) => {
            html += `<li>${esc(b)}</li>`;
          });
          html += `</ul>`;
        });

      } else {
        // simple_list
        html += `<div class="r-section-header">${esc(heading)}</div>`;
        html += `<ul class="r-bullets">`;
        items.forEach((item) => {
          const text = typeof item === "string" ? item : (item.name || item.title || JSON.stringify(item));
          html += `<li>${esc(text)}</li>`;
        });
        html += `</ul>`;
      }
    });
  } else {
    // Backward compatibility: old fixed-schema format
    if (d.summary) {
      html += `<div class="r-section-header">Summary</div>`;
      html += `<div class="r-summary">${esc(d.summary)}</div>`;
    }

    if (d.skills) {
      html += `<div class="r-section-header">Skills</div>`;
      (d.skills || []).forEach((s) => {
        html += `<div class="r-skill-row"><strong>${esc(s.category)}:</strong> ${esc(s.items)}</div>`;
      });
    }

    if (d.experience) {
      html += `<div class="r-section-header">Experience</div>`;
      (d.experience || []).forEach((job) => {
        html += `<div class="r-job-title">${esc(job.job_title)}</div>`;
        html += `<div class="r-job-meta">${esc(job.company)} -- ${esc(job.context)} <span>| ${esc(job.dates)} | ${esc(job.location)}</span></div>`;
        html += `<ul class="r-bullets">`;
        (job.bullets || []).forEach((b) => {
          html += `<li>${esc(b)}</li>`;
        });
        html += `</ul>`;
      });
    }

    const edu = d.education || {};
    if (edu.degree) {
      html += `<div class="r-section-header">Education</div>`;
      html += `<div class="r-edu-degree">${esc(edu.degree)}</div>`;
      html += `<div class="r-edu-meta">${esc(edu.school)} | ${esc(edu.dates)} | ${esc(edu.location)}</div>`;
    }
  }

  return html;
}

function esc(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

function linkify(str) {
  // Escape HTML first, then render [text](url) markdown links and bare URLs
  const escaped = esc(str);
  // Markdown-style [display text](url) — supports https and email links
  let result = escaped.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (match, text, url) => {
      let href = url;
      // Add mailto: for email addresses, https:// check for web URLs
      if (url.includes("@") && !url.startsWith("mailto:")) href = "mailto:" + url;
      const target = url.startsWith("http") ? ' target="_blank" rel="noopener"' : "";
      return `<a href="${href}"${target} style="color:#2c5f8a;text-decoration:underline;">${text}</a>`;
    }
  );
  // Bare URLs not already inside an href
  result = result.replace(
    /(?<!href=")(https?:\/\/[^\s|,<]+)/g,
    '<a href="$1" target="_blank" rel="noopener" style="color:#2c5f8a;text-decoration:underline;">$1</a>'
  );
  return result;
}

// ── Tabs ──
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.tab).classList.add("active");
  });
});

// ── Download PDF ──
downloadBtn.addEventListener("click", async () => {
  if (!tailoredData) return showError("No tailored resume data available.");

  downloadBtn.disabled = true;
  downloadBtn.textContent = "Generating PDF...";

  try {
    // Build contact from editable fields
    const editedName = (document.getElementById("edit-name")?.value || "").trim();
    const editedContact = (document.getElementById("edit-contact")?.value || "").trim();
    const editedLinkedin = (document.getElementById("edit-linkedin")?.value || "").trim();

    // Merge contact + linkedin into one string
    let fullContact = editedContact;
    if (editedLinkedin) {
      fullContact = fullContact ? fullContact + " | " + editedLinkedin : editedLinkedin;
    }

    const pdfData = {
      ...tailoredData,
      name: editedName || tailoredData.name,
      contact: fullContact || tailoredData.contact,
      template: loadFromStorage(STORAGE_KEYS.pdfTemplate) || "modern_green",
    };

    const res = await authFetch("/api/download-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pdfData),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "PDF download failed");
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;

    a.download = buildResumeFilename();
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showError(err.message);
  } finally {
    downloadBtn.disabled = false;
    downloadBtn.textContent = "Download PDF";
  }
});

// ── Application Q&A ──
const qaSubmitBtn = document.getElementById("qa-submit-btn");
const qaLoading = document.getElementById("qa-loading");
const qaResults = document.getElementById("qa-results");

qaSubmitBtn.addEventListener("click", async () => {
  const questions = document.getElementById("qa-questions").value.trim();
  const provider = getActiveProvider();
  const apiKey = getActiveApiKey();
  const model = getActiveModel();
  const baseUrl = getActiveBaseUrl();
  const meta = getProviderMeta(provider);
  const isLocal = provider === "ollama" || (provider === "openai_compatible" && baseUrl.includes("localhost"));

  if (!questions) return showError("Please paste at least one question.");
  if (!apiKey && !isLocal) return showError(`Please enter your ${meta.label} above.`);
  if (!tailoredData) return showError("Please tailor a resume first.");
  if (!savedJDText) return showError("Job description context is missing. Please tailor a resume first.");

  qaSubmitBtn.disabled = true;
  qaSubmitBtn.textContent = "Generating...";
  qaLoading.style.display = "flex";
  qaResults.innerHTML = "";

  try {
    const res = await authFetch("/api/answer-questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: provider,
        api_key: apiKey,
        model: model,
        base_url: baseUrl,
        questions: questions,
        jd: savedJDText,
        resume: tailoredData,
      }),
    });

    const json = await res.json();

    if (!res.ok || json.error) {
      throw new Error(json.error || "Unknown error");
    }

    renderQAResults(json.answers);
  } catch (err) {
    showError(err.message);
  } finally {
    qaSubmitBtn.disabled = false;
    qaSubmitBtn.textContent = "Generate Answers";
    qaLoading.style.display = "none";
  }
});

function renderQAResults(answers) {
  qaResults.innerHTML = "";
  (answers || []).forEach((item, i) => {
    const card = document.createElement("div");
    card.className = "qa-card";
    card.innerHTML = `
      <div class="qa-question">
        <span class="qa-q-label">Q${i + 1}</span>
        <span>${esc(item.question)}</span>
      </div>
      <div class="qa-answer">${esc(item.answer)}</div>
      <button class="qa-copy-btn" data-answer="${encodeURIComponent(item.answer)}">Copy answer</button>
    `;
    qaResults.appendChild(card);
  });

  qaResults.querySelectorAll(".qa-copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = decodeURIComponent(btn.dataset.answer);
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = "Copy answer";
          btn.classList.remove("copied");
        }, 2000);
      } catch {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = "Copy answer";
          btn.classList.remove("copied");
        }, 2000);
      }
    });
  });
}

// ── User bar / logout ──
(function initUserBar() {
  const emailEl = document.getElementById("user-bar-email");
  if (emailEl) emailEl.textContent = AUTH_EMAIL || "";
  const logoutBtn = document.getElementById("logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => {
      localStorage.removeItem("rt_auth_token");
      localStorage.removeItem("rt_auth_email");
      window.location.replace("/login");
    });
  }
})();

// ── Server-side settings sync (per-user provider keys / models / prefs) ──
// Keys are stored against the user's account so they follow them across devices
// and are never visible to other users. localStorage is just a fast local cache.
function collectSettings() {
  return {
    provider: loadFromStorage(STORAGE_KEYS.provider) || (providerSelect && providerSelect.value) || "",
    providerKeys: getProviderMap(STORAGE_KEYS.providerKeys),
    providerModels: getProviderMap(STORAGE_KEYS.providerModels),
    providerBaseUrls: getProviderMap(STORAGE_KEYS.providerBaseUrls),
    pdfTemplate: loadFromStorage(STORAGE_KEYS.pdfTemplate) || "",
  };
}

let _settingsPushTimer = null;
function pushSettingsToServer() {
  clearTimeout(_settingsPushTimer);
  _settingsPushTimer = setTimeout(async () => {
    try {
      await authFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: collectSettings() }),
      });
    } catch (err) {
      console.warn("Could not save settings:", err.message);
    }
  }, 700);
}

async function pullSettingsFromServer() {
  try {
    const res = await authFetch("/api/settings");
    const json = await res.json();
    if (!res.ok || !json.settings) return;
    const s = json.settings;
    // Merge provider maps: keep anything already local, fill gaps from the server.
    ["providerKeys", "providerModels", "providerBaseUrls"].forEach((mapName) => {
      if (s[mapName] && typeof s[mapName] === "object") {
        const merged = Object.assign({}, s[mapName], getProviderMap(STORAGE_KEYS[mapName]));
        saveToStorage(STORAGE_KEYS[mapName], merged);
      }
    });
    // Scalar prefs: only adopt the server value if nothing is set locally yet.
    if (s.provider && !loadFromStorage(STORAGE_KEYS.provider)) saveToStorage(STORAGE_KEYS.provider, s.provider);
    if (s.pdfTemplate && !loadFromStorage(STORAGE_KEYS.pdfTemplate)) saveToStorage(STORAGE_KEYS.pdfTemplate, s.pdfTemplate);
  } catch (err) {
    console.warn("Could not load settings:", err.message);
  }
}

// ── My Resumes (per-user, server-side) ──
const savedListEl = document.getElementById("saved-list");
const savedEmptyEl = document.getElementById("saved-empty");
const savedCountEl = document.getElementById("saved-count");

async function saveResumeToServer(jd) {
  if (!tailoredData) return;
  try {
    const res = await authFetch("/api/resumes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: tailoredData.name || "",
        // Label the saved resume by the JD's job title + company.
        title: lastJobMeta.position || tailoredData.title || "",
        company: lastJobMeta.company || (dlCompanyInput && dlCompanyInput.value.trim()) || "",
        jd: jd || savedJDText || "",
        data: tailoredData,
      }),
    });
    const json = await res.json();
    if (res.ok && json.id) {
      currentResumeId = json.id;
      loadMyResumes();
    }
  } catch (err) {
    // Saving to the library is best-effort — never block the main flow.
    console.warn("Could not save resume to library:", err.message);
  }
}

async function loadMyResumes() {
  if (!savedListEl) return;
  try {
    const res = await authFetch("/api/resumes");
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Could not load resumes.");
    renderSavedResumes(json.resumes || []);
  } catch (err) {
    console.warn(err.message);
  }
}

function renderSavedResumes(list) {
  if (!savedListEl) return;
  savedListEl.innerHTML = "";
  if (savedCountEl) savedCountEl.textContent = list.length ? `(${list.length})` : "";
  if (!list.length) {
    if (savedEmptyEl) savedEmptyEl.style.display = "";
    return;
  }
  if (savedEmptyEl) savedEmptyEl.style.display = "none";

  list.forEach((r) => {
    const card = document.createElement("div");
    card.className = "saved-card";
    if (r.id === currentResumeId) card.classList.add("active");

    const info = document.createElement("div");
    info.className = "saved-info";
    const titleLine = [r.company, r.title].filter(Boolean).join(" · ") || r.name || "Untitled resume";
    info.innerHTML =
      `<div class="saved-title">${esc(titleLine)}</div>` +
      `<div class="saved-meta">${esc(formatSavedDate(r.created_at))}</div>`;
    card.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "saved-actions";

    const openBtn = document.createElement("button");
    openBtn.className = "btn btn-secondary btn-sm";
    openBtn.textContent = "Open";
    openBtn.addEventListener("click", () => openSavedResume(r.id));
    actions.appendChild(openBtn);

    const dlBtn = document.createElement("button");
    dlBtn.className = "btn btn-secondary btn-sm";
    dlBtn.textContent = "Download";
    dlBtn.addEventListener("click", () => downloadSavedResume(r.id, dlBtn));
    actions.appendChild(dlBtn);

    const qaBtn = document.createElement("button");
    qaBtn.className = "btn btn-secondary btn-sm";
    qaBtn.textContent = "Q&A";
    qaBtn.title = "Generate application answers for this resume";
    qaBtn.addEventListener("click", () => openSavedResumeQA(r.id));
    actions.appendChild(qaBtn);

    const delBtn = document.createElement("button");
    delBtn.className = "saved-delete-btn";
    delBtn.innerHTML = "&#x2715;";
    delBtn.title = "Delete";
    delBtn.addEventListener("click", () => deleteSavedResume(r.id));
    actions.appendChild(delBtn);

    card.appendChild(actions);
    savedListEl.appendChild(card);
  });
}

async function openSavedResume(id) {
  try {
    const res = await authFetch("/api/resumes/" + encodeURIComponent(id));
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Could not open resume.");
    const rec = json.resume || {};
    tailoredData = rec.data || null;
    savedJDText = rec.jd || "";
    lastJobMeta = normalizeJobMeta({ company: rec.company, position: rec.title });
    currentResumeId = id;
    if (!tailoredData) throw new Error("This saved resume has no data.");

    // Opening a saved resume replaces the auto-filled filename fields.
    if (dlCompanyInput) dlCompanyInput.dataset.userEdited = "";
    if (dlTitleInput) dlTitleInput.dataset.userEdited = "";

    saveToStorage(STORAGE_KEYS.tailoredData, tailoredData);
    saveToStorage(STORAGE_KEYS.savedJD, savedJDText);
    saveToStorage(STORAGE_KEYS.jobMeta, lastJobMeta);

    renderResults(tailoredData);
    resultsSection.classList.add("active");
    loadMyResumes();  // refresh active highlight
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showError(err.message);
  }
}

async function deleteSavedResume(id) {
  if (!confirm("Delete this saved resume? This cannot be undone.")) return;
  try {
    const res = await authFetch("/api/resumes/" + encodeURIComponent(id), { method: "DELETE" });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Could not delete resume.");
    if (currentResumeId === id) currentResumeId = null;
    loadMyResumes();
  } catch (err) {
    showError(err.message);
  }
}

// Build a "{Candidate Name} - {Job Title}.pdf" filename for a saved resume.
function buildFilenameFrom(company, title, data) {
  const name = _sanitizeFilenamePart((data && data.name) || "");
  const t = _sanitizeFilenamePart(title || "");
  let stem;
  if (name && t) stem = `${name} - ${t}`;
  else if (name) stem = name;
  else if (t) stem = t;
  else stem = "Resume";
  return `${stem}.pdf`;
}

// Render a resume JSON to PDF (using the currently selected template) and download it.
async function downloadResumePdf(data, company, title) {
  const pdfData = { ...data, template: loadFromStorage(STORAGE_KEYS.pdfTemplate) || "modern_green" };
  const res = await authFetch("/api/download-pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pdfData),
  });
  if (!res.ok) {
    let msg = "PDF download failed";
    try { msg = (await res.json()).error || msg; } catch {}
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = buildFilenameFrom(company, title, data);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Download one saved resume directly from its card (fetches its full data first).
async function downloadSavedResume(id, btn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const res = await authFetch("/api/resumes/" + encodeURIComponent(id));
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Could not load resume.");
    const rec = json.resume || {};
    if (!rec.data) throw new Error("This saved resume has no data.");
    await downloadResumePdf(rec.data, rec.company, rec.title);
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// Open a saved resume AND jump to the Application Q&A tab for it.
async function openSavedResumeQA(id) {
  await openSavedResume(id);   // loads data + its stored JD into the preview + Q&A context
  const qaTabBtn = document.querySelector('.tab-btn[data-tab="tab-qa"]');
  if (qaTabBtn) qaTabBtn.click();
  const qa = document.getElementById("tab-qa");
  if (qa) qa.scrollIntoView({ behavior: "smooth", block: "start" });
}

function formatSavedDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch { return iso; }
}

const savedRefreshBtn = document.getElementById("saved-refresh-btn");
if (savedRefreshBtn) savedRefreshBtn.addEventListener("click", loadMyResumes);

// ── Batch generation (tailor + save a resume for multiple jobs) ──
const batchList = document.getElementById("batch-list");
const batchGenerateBtn = document.getElementById("batch-generate-btn");
const batchProgress = document.getElementById("batch-progress");

// Build the same FormData the single-tailor flow sends, for one JD.
function buildTailorFormData(jd) {
  const fd = new FormData();
  fd.append("provider", getActiveProvider());
  fd.append("api_key", getActiveApiKey());
  const model = getActiveModel();
  if (model) fd.append("model", model);
  const baseUrl = getActiveBaseUrl();
  if (baseUrl) fd.append("base_url", baseUrl);
  fd.append("jd", jd);
  if (resumeFileInput.files.length) fd.append("resume_file", resumeFileInput.files[0]);
  const promptUploadActive = document.getElementById("prompt-upload-mode").classList.contains("active");
  const promptText = document.getElementById("prompt-text").value.trim();
  if (promptUploadActive && promptFileInput.files.length > 0) fd.append("prompt_file", promptFileInput.files[0]);
  else if (promptText) fd.append("prompt", promptText);
  return fd;
}

function collectBatchJobs() {
  return [...batchList.querySelectorAll(".batch-job")].map((c) => ({
    company: c.querySelector(".batch-company").value.trim(),
    title: c.querySelector(".batch-title").value.trim(),
    jd: c.querySelector(".batch-jd").value.trim(),
  }));
}

function saveBatchJobs() {
  saveToStorage(STORAGE_KEYS.batchJobs, collectBatchJobs());
}

function addBatchJob(job) {
  job = job || { company: "", title: "", jd: "" };
  const card = document.createElement("div");
  card.className = "batch-job";
  card.innerHTML =
    '<div class="batch-job-row">' +
    '  <input class="batch-company" placeholder="Company (optional)" autocomplete="off" />' +
    '  <input class="batch-title" placeholder="Job title (optional)" autocomplete="off" />' +
    '  <button type="button" class="batch-remove" title="Remove job">&#x2715;</button>' +
    '</div>' +
    '<textarea class="batch-jd" rows="4" placeholder="Paste the job description for this job..."></textarea>' +
    '<div class="batch-job-status"></div>';
  card.querySelector(".batch-company").value = job.company || "";
  card.querySelector(".batch-title").value = job.title || "";
  card.querySelector(".batch-jd").value = job.jd || "";
  card.querySelectorAll("input, textarea").forEach((el) => el.addEventListener("input", saveBatchJobs));
  card.querySelector(".batch-remove").addEventListener("click", () => {
    card.remove();
    if (!batchList.querySelector(".batch-job")) addBatchJob();  // keep at least one card
    saveBatchJobs();
  });
  batchList.appendChild(card);
  return card;
}

function updateBatchProgress(done, failed, total) {
  if (!total) { batchProgress.textContent = ""; return; }
  let txt = `${done + failed}/${total} processed`;
  if (failed) txt += ` · ${failed} failed`;
  batchProgress.textContent = txt;
}

async function generateAllResumes() {
  const provider = getActiveProvider();
  const apiKey = getActiveApiKey();
  const baseUrl = getActiveBaseUrl();
  const meta = getProviderMeta(provider);
  const isLocal = provider === "ollama" || (provider === "openai_compatible" && baseUrl.includes("localhost"));
  if (!apiKey && !isLocal) return showError(`Please enter your ${meta.label} above.`);
  if (!resumeFileInput.files.length) return showError("Please upload your resume in 'Your Profile' first.");

  const cards = [...batchList.querySelectorAll(".batch-job")].filter((c) => c.querySelector(".batch-jd").value.trim());
  if (!cards.length) return showError("Add at least one job with a job description.");

  batchGenerateBtn.disabled = true;
  batchGenerateBtn.textContent = "Generating…";
  let done = 0, failed = 0;
  updateBatchProgress(0, 0, cards.length);

  // Sequential — each job is its own request, so nothing hits the serverless timeout.
  for (const card of cards) {
    const jd = card.querySelector(".batch-jd").value.trim();
    const company = card.querySelector(".batch-company").value.trim();
    const title = card.querySelector(".batch-title").value.trim();
    const statusEl = card.querySelector(".batch-job-status");
    statusEl.textContent = "Generating…";
    statusEl.className = "batch-job-status generating";
    try {
      const res = await authFetch("/api/tailor", { method: "POST", body: buildTailorFormData(jd) });
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error || "Tailoring failed.");
      const jm = normalizeJobMeta(json.job_meta);
      const finalCompany = company || jm.company || "";
      const finalTitle = title || jm.position || (json.data && json.data.title) || "";
      const saveRes = await authFetch("/api/resumes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: (json.data && json.data.name) || "", title: finalTitle, company: finalCompany, jd, data: json.data }),
      });
      const saveJson = await saveRes.json();
      if (!saveRes.ok || saveJson.error) throw new Error(saveJson.error || "Could not save resume.");
      done++;
      const label = [finalCompany, finalTitle].filter(Boolean).join(" · ");
      statusEl.textContent = "✓ Saved" + (label ? " — " + label : "");
      statusEl.className = "batch-job-status done";
    } catch (err) {
      failed++;
      statusEl.textContent = "✗ " + err.message;
      statusEl.className = "batch-job-status failed";
    }
    updateBatchProgress(done, failed, cards.length);
  }

  batchGenerateBtn.disabled = false;
  batchGenerateBtn.textContent = "Generate all resumes";
  loadMyResumes();  // new resumes now appear in My Resumes, ready to download
}

if (batchList) {
  document.getElementById("batch-add-btn").addEventListener("click", () => { addBatchJob(); saveBatchJobs(); });
  batchGenerateBtn.addEventListener("click", generateAllResumes);
  document.getElementById("batch-header").addEventListener("click", (e) => {
    if (e.target.closest("input, textarea, .batch-remove")) return;
    document.getElementById("batch-section").classList.toggle("collapsed");
  });
}

function initBatch() {
  if (!batchList) return;
  const saved = loadJSONFromStorage(STORAGE_KEYS.batchJobs);
  if (Array.isArray(saved) && saved.length) saved.forEach((j) => addBatchJob(j));
  else addBatchJob();
}

// ── Restore saved state ──
function migrateOldKeys() {
  // Migrate from the old single-key schema to the per-provider map.
  const legacyAnthropic = loadFromStorage("rt_api_key");
  const legacyGemini = loadFromStorage("rt_gemini_key");
  if (legacyAnthropic || legacyGemini) {
    const keys = getProviderMap(STORAGE_KEYS.providerKeys);
    if (legacyAnthropic && !keys.anthropic) keys.anthropic = legacyAnthropic;
    if (legacyGemini && !keys.gemini) keys.gemini = legacyGemini;
    saveToStorage(STORAGE_KEYS.providerKeys, keys);
    try { localStorage.removeItem("rt_api_key"); } catch {}
    try { localStorage.removeItem("rt_gemini_key"); } catch {}
  }
  // Old provider value "claude" → "anthropic"
  const legacyProvider = loadFromStorage(STORAGE_KEYS.provider);
  if (legacyProvider === "claude") saveToStorage(STORAGE_KEYS.provider, "anthropic");
}

function restoreState() {
  migrateOldKeys();

  const savedProvider = loadFromStorage(STORAGE_KEYS.provider);
  const savedJD = loadFromStorage(STORAGE_KEYS.jd);
  const savedPrompt = loadFromStorage(STORAGE_KEYS.promptText);
  const savedTailored = loadJSONFromStorage(STORAGE_KEYS.tailoredData);
  const savedJDContext = loadFromStorage(STORAGE_KEYS.savedJD);
  const savedPromptMode = loadFromStorage(STORAGE_KEYS.promptMode);
  const savedJobMeta = loadJSONFromStorage(STORAGE_KEYS.jobMeta);
  if (savedJobMeta) lastJobMeta = normalizeJobMeta(savedJobMeta);

  if (savedProvider && PROVIDER_META[savedProvider]) providerSelect.value = savedProvider;
  updateProviderUI();   // Loads key/model/base_url for the active provider

  // Restore LinkedIn URL
  const savedLinkedin = loadFromStorage("rt_linkedin_url");
  if (savedLinkedin) document.getElementById("linkedin-url").value = savedLinkedin;

  if (savedJD) document.getElementById("jd-text").value = savedJD;
  if (savedPrompt) document.getElementById("prompt-text").value = savedPrompt;

  // One-shot cleanup: drop any leftover cover-letter localStorage from prior versions.
  ["rt_cover_company", "rt_cover_hm", "rt_cover_tone", "rt_cover_body"].forEach((k) => {
    try { localStorage.removeItem(k); } catch {}
  });
  _updateFilenamePreview();

  // Restore prompt mode preference
  if (savedPromptMode === "paste") {
    document.querySelectorAll(".input-toggle .toggle-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.target === "prompt-paste-mode");
    });
    document.getElementById("prompt-upload-mode").classList.remove("active");
    document.getElementById("prompt-paste-mode").classList.add("active");
  }

  // Restore file inputs from localStorage
  restoreFileInput(resumeFileInput, fileNameDisplay,
    STORAGE_KEYS.resumeFileName, STORAGE_KEYS.resumeFileContent);
  restoreFileInput(promptFileInput, promptFileNameDisplay,
    STORAGE_KEYS.promptFileName, STORAGE_KEYS.promptFileContent);

  // Restore template selection
  const templateSelect = document.getElementById("template-select");
  if (templateSelect) {
    const savedTemplate = loadFromStorage(STORAGE_KEYS.pdfTemplate);
    if (savedTemplate) templateSelect.value = savedTemplate;
    templateSelect.addEventListener("change", () => {
      saveToStorage(STORAGE_KEYS.pdfTemplate, templateSelect.value);
      pushSettingsToServer();
    });
  }

  // Restore tailored results
  if (savedTailored) {
    tailoredData = savedTailored;
    savedJDText = savedJDContext || "";
    renderResults(tailoredData);
    resultsSection.classList.add("active");
  }

  // Update setup status (will auto-collapse if ready)
  updateSetupStatus();

  // Load this user's private resume library from the server.
  loadMyResumes();

  // Populate the batch panel (restores any jobs the user queued earlier).
  initBatch();
}

// Boot: pull the user's saved settings (API keys, model, prefs) from the server
// FIRST so restoreState() sees them, then render everything.
(async function initApp() {
  await pullSettingsFromServer();
  restoreState();
})();
