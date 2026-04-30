/* ─────────────────────────────────────────────
   Resume Tailor — Frontend Logic
   ───────────────────────────────────────────── */

let tailoredData = null;
let savedJDText = "";  // Preserved JD context for Q&A
let scrapedMetadata = null;  // Metadata from last URL scrape
let lastAtsReport = null;     // Most recent ATS score report

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
  trackerEntries: "rt_tracker_entries",
  pdfTemplate: "rt_pdf_template",
  ats: "rt_ats_report",
  fastMode: "rt_fast_mode",
  dlCompany: "rt_dl_company",
  dlTitle: "rt_dl_title",
};

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

function saveToStorage(key, value) {
  try { localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value)); } catch {}
}

function loadFromStorage(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function loadJSONFromStorage(key) {
  try {
    const raw = localStorage.getItem(key);
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
});

function getActiveApiKey() { return keyInput.value.trim(); }
function getActiveModel() { return modelInput.value.trim(); }
function getActiveBaseUrl() { return baseUrlInput.value.trim(); }
function getActiveProvider() { return providerSelect.value; }

// ── Auto-save text inputs ──
keyInput.addEventListener("input", () => {
  setProviderMap(STORAGE_KEYS.providerKeys, providerSelect.value, keyInput.value);
});
modelInput.addEventListener("input", () => {
  setProviderMap(STORAGE_KEYS.providerModels, providerSelect.value, modelInput.value);
});
baseUrlInput.addEventListener("input", () => {
  setProviderMap(STORAGE_KEYS.providerBaseUrls, providerSelect.value, baseUrlInput.value);
});
document.getElementById("apify-key").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.apifyKey, e.target.value);
});
document.getElementById("jd-text").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.jd, e.target.value);
});
document.getElementById("jd-url").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.jdUrl, e.target.value);
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
  "Rewriting summary for ATS optimization...",
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

  const fastMode = !!document.getElementById("fast-mode-checkbox")?.checked;

  const fd = new FormData();
  fd.append("provider", provider);
  fd.append("api_key", apiKey);
  if (model) fd.append("model", model);
  if (baseUrl) fd.append("base_url", baseUrl);
  if (fastMode) fd.append("fast_mode", "1");
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
    const res = await fetch("/api/tailor", {
      method: "POST",
      body: fd,
    });

    const json = await res.json();

    if (!res.ok || json.error) {
      throw new Error(json.error || "Unknown error");
    }

    tailoredData = json.data;
    savedJDText = jd;
    lastAtsReport = json.ats || null;
    saveToStorage(STORAGE_KEYS.tailoredData, tailoredData);
    saveToStorage(STORAGE_KEYS.savedJD, savedJDText);
    saveToStorage(STORAGE_KEYS.ats, lastAtsReport);
    renderResults(tailoredData);
    renderAtsPanel(lastAtsReport);
    resultsSection.classList.add("active");

    // Auto-add tracker entry
    const jdUrl = document.getElementById("jd-url").value.trim();
    const trackerEntry = {
      platform: (scrapedMetadata && scrapedMetadata.platform) || detectPlatformFromUrl(jdUrl) || "Manual",
      company: (scrapedMetadata && scrapedMetadata.company) || "",
      position: (scrapedMetadata && scrapedMetadata.position) || (tailoredData && tailoredData.title) || "",
      url: (scrapedMetadata && scrapedMetadata.url) || jdUrl || "",
      status: "Applied",
    };
    addTrackerEntry(trackerEntry);
    scrapedMetadata = null;  // Reset for next use

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

// ── Auto-fix diff (structured comparison of pre- vs post-remediation resume) ──
function _wordDiff(beforeStr, afterStr) {
  const a = (beforeStr || "").split(/(\s+)/);
  const b = (afterStr || "").split(/(\s+)/);
  // Bail out for very long strings — LCS is O(n*m).
  if (a.length * b.length > 60000) {
    return { before: [{ text: beforeStr || "", type: "removed" }],
             after:  [{ text: afterStr  || "", type: "added"   }] };
  }
  const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1
               : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const before = [], after = [];
  let i = a.length, j = b.length;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      before.unshift({ text: a[i - 1], type: "same" });
      after.unshift({ text: b[j - 1], type: "same" });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      after.unshift({ text: b[j - 1], type: "added" });
      j--;
    } else {
      before.unshift({ text: a[i - 1], type: "removed" });
      i--;
    }
  }
  return { before, after };
}

function _renderDiffSide(parts, side) {
  const span = document.createElement("div");
  span.className = `diff-side diff-${side}`;
  parts.forEach((p) => {
    if (p.type === "same") {
      span.appendChild(document.createTextNode(p.text));
    } else {
      const tag = document.createElement(p.type === "added" ? "ins" : "del");
      tag.className = `diff-${p.type}`;
      tag.textContent = p.text;
      span.appendChild(tag);
    }
  });
  return span;
}

function _diffChange(path, before, after) {
  return { path, before: before == null ? "" : String(before), after: after == null ? "" : String(after) };
}

function diffResume(before, after) {
  const changes = [];
  if ((before.title || "") !== (after.title || ""))
    changes.push(_diffChange("Title", before.title, after.title));
  if ((before.contact || "") !== (after.contact || ""))
    changes.push(_diffChange("Contact", before.contact, after.contact));

  const bSecs = before.sections || [];
  const aSecs = after.sections  || [];
  const m = Math.min(bSecs.length, aSecs.length);
  for (let i = 0; i < m; i++) {
    const bs = bSecs[i], as = aSecs[i];
    const heading = as.heading || bs.heading || `Section ${i + 1}`;

    if (bs.heading && as.heading && bs.heading !== as.heading) {
      changes.push(_diffChange(`${bs.heading} → renamed`, bs.heading, as.heading));
    }

    if (bs.type === "text" || as.type === "text") {
      if ((bs.content || "") !== (as.content || "")) {
        changes.push(_diffChange(heading, bs.content, as.content));
      }
    } else if (bs.type === "skills" || as.type === "skills") {
      const bIt = bs.items || [], aIt = as.items || [];
      const km = Math.min(bIt.length, aIt.length);
      for (let j = 0; j < km; j++) {
        const bc = bIt[j] || {}, ac = aIt[j] || {};
        const cat = ac.category || bc.category || "category";
        if ((bc.category || "") !== (ac.category || "")) {
          changes.push(_diffChange(`${heading} > category`, bc.category, ac.category));
        }
        if ((bc.items || "") !== (ac.items || "")) {
          changes.push(_diffChange(`${heading} > ${cat}`, bc.items, ac.items));
        }
      }
      for (let j = km; j < aIt.length; j++) {
        changes.push(_diffChange(`${heading} > +${aIt[j].category || "new"}`, "", aIt[j].items || ""));
      }
      for (let j = km; j < bIt.length; j++) {
        changes.push(_diffChange(`${heading} > −${bIt[j].category || "removed"}`, bIt[j].items || "", ""));
      }
    } else if (bs.type === "experience" || as.type === "experience") {
      const bJ = bs.items || [], aJ = as.items || [];
      const jm = Math.min(bJ.length, aJ.length);
      for (let j = 0; j < jm; j++) {
        const bj = bJ[j] || {}, aj = aJ[j] || {};
        const label = `${heading} > ${aj.company || bj.company || ""} — ${aj.job_title || bj.job_title || ""}`.trim();
        const bb = bj.bullets || [], ab = aj.bullets || [];
        const bm = Math.max(bb.length, ab.length);
        for (let k = 0; k < bm; k++) {
          const bef = bb[k], aft = ab[k];
          if ((bef || "") !== (aft || "")) {
            changes.push(_diffChange(`${label} > bullet ${k + 1}`, bef, aft));
          }
        }
      }
    } else if (bs.type === "projects" || as.type === "projects") {
      const bP = bs.items || [], aP = as.items || [];
      const pm = Math.min(bP.length, aP.length);
      for (let j = 0; j < pm; j++) {
        const bp = bP[j] || {}, ap = aP[j] || {};
        const label = `${heading} > ${ap.name || bp.name || `Project ${j + 1}`}`;
        if ((bp.description || "") !== (ap.description || "")) {
          changes.push(_diffChange(`${label} > description`, bp.description, ap.description));
        }
        if ((bp.technologies || "") !== (ap.technologies || "")) {
          changes.push(_diffChange(`${label} > technologies`, bp.technologies, ap.technologies));
        }
        const bb = bp.bullets || [], ab = ap.bullets || [];
        const bm = Math.max(bb.length, ab.length);
        for (let k = 0; k < bm; k++) {
          if ((bb[k] || "") !== (ab[k] || "")) {
            changes.push(_diffChange(`${label} > bullet ${k + 1}`, bb[k], ab[k]));
          }
        }
      }
    } else {
      // education / simple_list / unknown — JSON-string fallback
      const beforeStr = JSON.stringify(bs.items ?? bs);
      const afterStr  = JSON.stringify(as.items ?? as);
      if (beforeStr !== afterStr) {
        changes.push(_diffChange(heading, beforeStr, afterStr));
      }
    }
  }
  // Added / removed sections
  for (let i = m; i < aSecs.length; i++) changes.push(_diffChange(`+ ${aSecs[i].heading || `Section ${i + 1}`}`, "", JSON.stringify(aSecs[i])));
  for (let i = m; i < bSecs.length; i++) changes.push(_diffChange(`− ${bSecs[i].heading || `Section ${i + 1}`}`, JSON.stringify(bSecs[i]), ""));

  return changes;
}

function renderAutoFixDiff(report) {
  const row = document.getElementById("ats-diff-row");
  const panel = document.getElementById("ats-diff-panel");
  const summary = document.getElementById("ats-diff-summary");
  const toggleBtn = document.getElementById("ats-diff-toggle");
  if (!row || !panel || !summary || !toggleBtn) return;

  const initial = report && report.initial_resume;
  if (!initial || !tailoredData) {
    row.style.display = "none";
    panel.style.display = "none";
    panel.innerHTML = "";
    return;
  }

  const changes = diffResume(initial, tailoredData);
  const initScore = report.initial_score;
  const finalScore = report.score;
  const delta = (typeof initScore === "number" && typeof finalScore === "number")
    ? `${initScore}% → ${finalScore}%` : "";
  summary.textContent = `${changes.length} field${changes.length === 1 ? "" : "s"} changed${delta ? " · " + delta : ""}`;

  row.style.display = "";
  panel.style.display = "none";   // collapsed by default
  panel.innerHTML = "";
  toggleBtn.textContent = "View auto-fix changes";

  // Build the diff DOM lazily on first toggle so we don't pay if user never opens it.
  let built = false;
  toggleBtn.onclick = () => {
    if (!built) {
      changes.forEach((c) => {
        const item = document.createElement("div");
        item.className = "diff-item";
        const head = document.createElement("div");
        head.className = "diff-path";
        head.textContent = c.path;
        item.appendChild(head);

        if (!c.before) {
          const after = document.createElement("div");
          after.className = "diff-side diff-after";
          const ins = document.createElement("ins");
          ins.className = "diff-added";
          ins.textContent = c.after;
          after.appendChild(ins);
          item.appendChild(after);
        } else if (!c.after) {
          const before = document.createElement("div");
          before.className = "diff-side diff-before";
          const del = document.createElement("del");
          del.className = "diff-removed";
          del.textContent = c.before;
          before.appendChild(del);
          item.appendChild(before);
        } else {
          const wd = _wordDiff(c.before, c.after);
          item.appendChild(_renderDiffSide(wd.before, "before"));
          item.appendChild(_renderDiffSide(wd.after,  "after"));
        }
        panel.appendChild(item);
      });
      built = true;
    }
    const showing = panel.style.display !== "none";
    panel.style.display = showing ? "none" : "";
    toggleBtn.textContent = showing ? "View auto-fix changes" : "Hide auto-fix changes";
  };
}

// ── ATS panel ──
function renderAtsPanel(report) {
  const panel = document.getElementById("ats-panel");
  if (!panel) return;
  if (!report) { panel.style.display = "none"; return; }

  const numEl = document.getElementById("ats-score-num");
  const badgeEl = document.getElementById("ats-score-badge");
  const targetLine = document.getElementById("ats-target-line");
  const notesLine = document.getElementById("ats-notes-line");
  const matchedEl = document.getElementById("ats-matched-chips");
  const missingEl = document.getElementById("ats-missing-chips");

  panel.style.display = "";

  const target = report.target ?? 85;
  const score = report.score;

  if (score === null || score === undefined) {
    numEl.textContent = "—";
    badgeEl.dataset.tier = "unknown";
    targetLine.textContent = `ATS scoring unavailable${report.error ? ": " + report.error : ""}.`;
    notesLine.textContent = "";
    matchedEl.innerHTML = "";
    missingEl.innerHTML = "";
    return;
  }

  numEl.textContent = `${score}%`;
  badgeEl.dataset.tier = score >= target ? "good" : (score >= 70 ? "ok" : "low");
  const passes = report.passes || 0;
  const history = Array.isArray(report.history) ? report.history : [];
  const floor = report.floor ?? 80;
  const historyText = history.length > 1 ? ` (${history.join(" → ")})` : "";
  const passText = passes > 0 ? ` after ${passes} refinement pass${passes === 1 ? "" : "es"}${historyText}` : "";
  if (score >= target) {
    targetLine.textContent = `Hit the ${target}% target${passText}.`;
  } else if (score >= floor) {
    targetLine.textContent = `Above the ${floor}% floor but below the ${target}% target${passText}. Surface the missing keywords manually if your experience supports them.`;
  } else {
    targetLine.textContent = `Below the ${floor}% floor${passText}. Auto-fix exhausted its rewrite budget — likely the original resume doesn't support enough of the JD's keywords. Edit manually or pick a different role.`;
  }
  notesLine.textContent = report.notes || "";

  matchedEl.innerHTML = "";
  (report.matched_keywords || []).forEach((kw) => {
    const span = document.createElement("span");
    span.className = "ats-chip ats-chip-matched";
    span.textContent = kw;
    matchedEl.appendChild(span);
  });
  missingEl.innerHTML = "";
  (report.missing_keywords || []).forEach((kw) => {
    const span = document.createElement("span");
    span.className = "ats-chip ats-chip-missing";
    span.textContent = kw;
    missingEl.appendChild(span);
  });

  renderAutoFixDiff(report);
}

// ── Keyword heatmap (per-section density on the resume preview) ──
function collectActiveKeywords() {
  const set = new Set();
  const add = (k) => {
    if (!k) return;
    const v = (typeof k === "string") ? k : (k.keyword || "");
    if (v && v.length >= 2) set.add(v);
  };
  if (lastAtsReport) {
    (lastAtsReport.matched_keywords || []).forEach(add);
    (lastAtsReport.missing_keywords || []).forEach(add);
  }
  (lastJdKeywords || []).forEach(add);
  return [...set];
}

function _kwRegex(keywords) {
  if (!keywords || !keywords.length) return null;
  // Sort longest-first so multi-word terms win against subword matches.
  const sorted = [...new Set(keywords)].sort((a, b) => b.length - a.length);
  const escaped = sorted.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // Word-ish boundary: not preceded/followed by alphanum (allow hyphenated terms inside)
  return new RegExp(`(?<![A-Za-z0-9])(${escaped.join("|")})(?![A-Za-z0-9])`, "gi");
}

function _highlightInNode(node, re) {
  if (!re) return 0;
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.nodeValue;
    re.lastIndex = 0;
    if (!re.test(text)) return 0;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, total = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const mark = document.createElement("mark");
      mark.className = "kw-hit";
      mark.textContent = m[0];
      frag.appendChild(mark);
      last = m.index + m[0].length;
      total++;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
    return total;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return 0;
  const tag = node.tagName;
  if (tag === "A" || tag === "MARK" || tag === "SCRIPT" || tag === "STYLE") return 0;
  let total = 0;
  Array.from(node.childNodes).forEach((c) => { total += _highlightInNode(c, re); });
  return total;
}

function applyKeywordHeatmap(rootEl, keywords) {
  // Clear any prior badges
  rootEl.querySelectorAll(".r-section-density").forEach((n) => n.remove());
  const re = _kwRegex(keywords);
  if (!re) return;

  const sections = [];
  let current = null;
  Array.from(rootEl.children).forEach((child) => {
    if (child.classList && child.classList.contains("r-section-header")) {
      if (current) sections.push(current);
      current = { header: child, hits: 0 };
      return;
    }
    if (!current) return;  // pre-section content (name/title/contact)
    current.hits += _highlightInNode(child, re);
  });
  if (current) sections.push(current);

  sections.forEach((s) => {
    if (s.hits > 0) {
      const badge = document.createElement("span");
      badge.className = "r-section-density";
      badge.dataset.tier = s.hits >= 6 ? "high" : (s.hits >= 3 ? "mid" : "low");
      badge.textContent = `${s.hits} keyword${s.hits === 1 ? "" : "s"}`;
      s.header.appendChild(badge);
    }
  });
}

// ── Filename helpers ──
const dlCompanyInput = document.getElementById("dl-company");
const dlTitleInput = document.getElementById("dl-title");

function _sanitizeFilenamePart(s) {
  // Strip characters Windows/macOS/Linux disallow in filenames + collapse whitespace.
  return (s || "").replace(/[<>:"/\\|?*\x00-\x1F]/g, "").replace(/\s+/g, " ").trim();
}

function _autoCompanyName() {
  // Source priority: scraped metadata, then last tracker entry.
  if (scrapedMetadata && scrapedMetadata.company) return scrapedMetadata.company;
  const entries = loadJSONFromStorage(STORAGE_KEYS.trackerEntries) || [];
  if (entries.length) {
    const latest = entries[entries.length - 1];
    if (latest && latest.company) return latest.company;
  }
  return "";
}

function getDlCompany() {
  return (dlCompanyInput && dlCompanyInput.value || "").trim();
}
function getDlTitle() {
  return (dlTitleInput && dlTitleInput.value || "").trim();
}

function buildResumeFilename() {
  const company = _sanitizeFilenamePart(getDlCompany());
  const title = _sanitizeFilenamePart(getDlTitle());
  let stem;
  if (company && title) stem = `${company} - ${title}`;
  else if (company)      stem = company;
  else if (title)        stem = title;
  else                   stem = _sanitizeFilenamePart((tailoredData && tailoredData.name) || "Resume");
  return `${stem || "Resume"}.pdf`;
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
  // Auto-fill only when the user hasn't typed anything manually.
  if (dlCompanyInput && dlCompanyInput.dataset.userEdited !== "1") {
    const v = _autoCompanyName();
    if (v) dlCompanyInput.value = v;
  }
  if (dlTitleInput && dlTitleInput.dataset.userEdited !== "1") {
    const v = (tailoredData && tailoredData.title) || "";
    if (v) dlTitleInput.value = v;
  }
  _updateFilenamePreview();
}

// ── Render Results ──
function renderResults(data) {
  const preview = document.getElementById("resume-preview");
  preview.innerHTML = buildResumeHTML(data);
  applyKeywordHeatmap(preview, collectActiveKeywords());
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

    const res = await fetch("/api/download-pdf", {
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
    const res = await fetch("/api/answer-questions", {
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

// ── JD keyword preview ──
let lastJdKeywords = [];   // most recent preview list (used by heatmap as a seed)

function renderKwPreview(keywords, mode) {
  const cloud = document.getElementById("kw-preview-cloud");
  const status = document.getElementById("kw-preview-status");
  if (!keywords || keywords.length === 0) {
    cloud.style.display = "none";
    cloud.innerHTML = "";
    status.textContent = "No keywords found. Try a longer JD.";
    return;
  }
  cloud.innerHTML = "";
  cloud.style.display = "";
  keywords.forEach((kw) => {
    const span = document.createElement("span");
    span.className = "kw-chip";
    if (kw.priority === "preferred")     span.classList.add("kw-chip-preferred");
    else if (kw.priority === "nice_to_have") span.classList.add("kw-chip-nth");
    else if (kw.priority === "required") span.classList.add("kw-chip-required");
    if (kw.count && kw.count > 1) span.dataset.count = kw.count;
    span.textContent = kw.keyword + (kw.count && kw.count > 1 ? ` ×${kw.count}` : "");
    span.title = [kw.priority, kw.category].filter(Boolean).join(" · ");
    cloud.appendChild(span);
  });
  const tag = mode === "ai" ? "AI-extracted" : "heuristic";
  status.textContent = `${keywords.length} keywords (${tag}). Tailoring will target ≥85% coverage of these.`;
}

async function previewKeywords(useAi) {
  const jd = document.getElementById("jd-text").value.trim();
  const status = document.getElementById("kw-preview-status");
  if (!jd) { showError("Paste the job description first."); return; }
  status.textContent = useAi ? "Asking the AI..." : "Scanning JD...";

  const body = { jd, mode: useAi ? "ai" : "heuristic" };
  if (useAi) {
    body.provider = getActiveProvider();
    body.api_key = getActiveApiKey();
    body.model = getActiveModel();
    body.base_url = getActiveBaseUrl();
  }

  try {
    const res = await fetch("/api/jd-keywords", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Keyword preview failed.");
    lastJdKeywords = json.keywords || [];
    renderKwPreview(lastJdKeywords, json.mode);
  } catch (err) {
    status.textContent = "";
    showError(err.message);
  }
}

document.getElementById("kw-preview-btn").addEventListener("click", () => previewKeywords(false));
document.getElementById("kw-preview-ai-btn").addEventListener("click", () => previewKeywords(true));

// ── JD URL Scraping ──
const scrapeBtn = document.getElementById("scrape-btn");
const scrapeStatus = document.getElementById("scrape-status");

function detectPlatformFromUrl(url) {
  const platforms = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter",
    "monster.com": "Monster",
    "dice.com": "Dice",
    "lever.co": "Lever",
    "greenhouse.io": "Greenhouse",
    "workday.com": "Workday",
    "myworkdayjobs.com": "Workday",
    "smartrecruiters.com": "SmartRecruiters",
    "angel.co": "AngelList",
    "wellfound.com": "Wellfound",
    "builtin.com": "Built In",
    "simplyhired.com": "SimplyHired",
    "careerbuilder.com": "CareerBuilder",
    "welcometothejungle.com": "Welcome to the Jungle",
  };
  try {
    const domain = new URL(url).hostname.toLowerCase().replace("www.", "");
    for (const [key, name] of Object.entries(platforms)) {
      if (domain.includes(key)) return name;
    }
  } catch {}
  return "Other";
}

scrapeBtn.addEventListener("click", async () => {
  const apifyKey = document.getElementById("apify-key").value.trim();
  const jdUrl = document.getElementById("jd-url").value.trim();

  if (!apifyKey) return showError("Please enter your Apify API token above.");
  if (!jdUrl) return showError("Please enter a job posting URL.");

  try {
    new URL(jdUrl);
  } catch {
    return showError("Please enter a valid URL (e.g. https://...).");
  }

  scrapeBtn.disabled = true;
  scrapeBtn.textContent = "Scraping...";
  scrapeStatus.textContent = "Crawling page with Apify (this may take 30-60s)...";
  scrapeStatus.className = "scrape-status loading";

  try {
    const res = await fetch("/api/scrape-jd", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apify_token: apifyKey, url: jdUrl }),
    });

    const json = await res.json();

    if (!res.ok || json.error) {
      throw new Error(json.error || "Scraping failed.");
    }

    // Populate JD textarea
    document.getElementById("jd-text").value = json.text;
    saveToStorage(STORAGE_KEYS.jd, json.text);

    // Store scraped metadata
    scrapedMetadata = json.metadata || {};
    scrapeStatus.textContent = `Scraped successfully — ${scrapedMetadata.platform || "Unknown platform"}`;
    scrapeStatus.className = "scrape-status success";
  } catch (err) {
    scrapeStatus.textContent = err.message;
    scrapeStatus.className = "scrape-status error";
    showError(err.message);
  } finally {
    scrapeBtn.disabled = false;
    scrapeBtn.textContent = "Scrape JD";
  }
});

// ── Job Application Tracker ──
const trackerTbody = document.getElementById("tracker-tbody");
const trackerEmpty = document.getElementById("tracker-empty");
const trackerCopyBtn = document.getElementById("tracker-copy-btn");
const trackerClearBtn = document.getElementById("tracker-clear-btn");

function loadTrackerEntries() {
  return loadJSONFromStorage(STORAGE_KEYS.trackerEntries) || [];
}

function saveTrackerEntries(entries) {
  saveToStorage(STORAGE_KEYS.trackerEntries, entries);
}

function addTrackerEntry(entry) {
  const entries = loadTrackerEntries();
  entry.id = Date.now().toString();
  entry.date = new Date().toLocaleDateString("en-US", { month: "2-digit", day: "2-digit", year: "numeric" });
  entries.push(entry);
  saveTrackerEntries(entries);
  renderTracker();
}

function updateTrackerEntry(id, field, value) {
  const entries = loadTrackerEntries();
  const entry = entries.find(e => e.id === id);
  if (entry) {
    entry[field] = value;
    saveTrackerEntries(entries);
  }
}

function deleteTrackerEntry(id) {
  const entries = loadTrackerEntries().filter(e => e.id !== id);
  saveTrackerEntries(entries);
  renderTracker();
}

function renderTracker() {
  const entries = loadTrackerEntries();
  trackerTbody.innerHTML = "";

  if (entries.length === 0) {
    trackerEmpty.classList.remove("hidden");
    return;
  }

  trackerEmpty.classList.add("hidden");

  entries.forEach((entry) => {
    const tr = document.createElement("tr");

    // Date
    const tdDate = document.createElement("td");
    tdDate.textContent = entry.date || "";
    tr.appendChild(tdDate);

    // Job Link (editable)
    const tdLink = document.createElement("td");
    const linkWrap = document.createElement("div");
    linkWrap.style.display = "flex";
    linkWrap.style.alignItems = "center";
    linkWrap.style.gap = "6px";
    const linkSpan = document.createElement("span");
    linkSpan.className = "tracker-editable";
    linkSpan.contentEditable = "true";
    linkSpan.style.minWidth = "80px";
    linkSpan.textContent = entry.url || "";
    linkSpan.addEventListener("blur", () => {
      const val = linkSpan.textContent.trim();
      updateTrackerEntry(entry.id, "url", val);
      renderTracker();
    });
    linkWrap.appendChild(linkSpan);
    if (entry.url) {
      const a = document.createElement("a");
      a.className = "tracker-link";
      a.href = entry.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "\u2197";
      a.title = "Open link";
      a.style.flexShrink = "0";
      linkWrap.appendChild(a);
    }
    tdLink.appendChild(linkWrap);
    tr.appendChild(tdLink);

    // Platform (where the job was found)
    const tdPlatform = document.createElement("td");
    tdPlatform.textContent = entry.platform || "";
    tr.appendChild(tdPlatform);

    // Company (the company being applied to — editable)
    const tdCompany = document.createElement("td");
    const companySpan = document.createElement("span");
    companySpan.className = "tracker-editable";
    companySpan.contentEditable = "true";
    companySpan.textContent = entry.company || "";
    companySpan.addEventListener("blur", () => {
      updateTrackerEntry(entry.id, "company", companySpan.textContent.trim());
    });
    tdCompany.appendChild(companySpan);
    tr.appendChild(tdCompany);

    // Role (editable)
    const tdRole = document.createElement("td");
    const roleSpan = document.createElement("span");
    roleSpan.className = "tracker-editable";
    roleSpan.contentEditable = "true";
    roleSpan.textContent = entry.position || "";
    roleSpan.addEventListener("blur", () => {
      updateTrackerEntry(entry.id, "position", roleSpan.textContent.trim());
    });
    tdRole.appendChild(roleSpan);
    tr.appendChild(tdRole);

    // Status (dropdown)
    const tdStatus = document.createElement("td");
    const select = document.createElement("select");
    select.className = "tracker-status-select";
    ["Applied", "Interview", "Offer", "Rejected", "Withdrawn"].forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      if (entry.status === s) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener("change", () => {
      updateTrackerEntry(entry.id, "status", select.value);
    });
    tdStatus.appendChild(select);
    tr.appendChild(tdStatus);

    // Delete button
    const tdDel = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.className = "tracker-delete-btn";
    delBtn.innerHTML = "&#x2715;";
    delBtn.title = "Delete entry";
    delBtn.addEventListener("click", () => deleteTrackerEntry(entry.id));
    tdDel.appendChild(delBtn);
    tr.appendChild(tdDel);

    trackerTbody.appendChild(tr);
  });
}

// Copy tracker to clipboard as TSV (Excel-compatible)
trackerCopyBtn.addEventListener("click", async () => {
  const entries = loadTrackerEntries();
  if (entries.length === 0) return showError("No entries to copy.");

  const rows = entries.map(e =>
    `${e.date || ""}\t${e.url || ""}\t${e.platform || ""}\t${e.company || ""}\t${e.position || ""}\t${e.status || ""}`
  );
  const tsv = rows.join("\n");

  try {
    await navigator.clipboard.writeText(tsv);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = tsv;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }

  trackerCopyBtn.textContent = "Copied!";
  trackerCopyBtn.classList.add("copied");
  setTimeout(() => {
    trackerCopyBtn.textContent = "Copy to Clipboard (Excel)";
    trackerCopyBtn.classList.remove("copied");
  }, 2000);
});

// Clear all tracker entries
trackerClearBtn.addEventListener("click", () => {
  if (!confirm("Clear all tracked applications? This cannot be undone.")) return;
  saveTrackerEntries([]);
  renderTracker();
});

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

  const savedApifyKey = loadFromStorage(STORAGE_KEYS.apifyKey);
  const savedProvider = loadFromStorage(STORAGE_KEYS.provider);
  const savedJD = loadFromStorage(STORAGE_KEYS.jd);
  const savedJdUrl = loadFromStorage(STORAGE_KEYS.jdUrl);
  const savedPrompt = loadFromStorage(STORAGE_KEYS.promptText);
  const savedTailored = loadJSONFromStorage(STORAGE_KEYS.tailoredData);
  const savedJDContext = loadFromStorage(STORAGE_KEYS.savedJD);
  const savedPromptMode = loadFromStorage(STORAGE_KEYS.promptMode);

  if (savedProvider && PROVIDER_META[savedProvider]) providerSelect.value = savedProvider;
  updateProviderUI();   // Loads key/model/base_url for the active provider

  // Restore LinkedIn URL
  const savedLinkedin = loadFromStorage("rt_linkedin_url");
  if (savedLinkedin) document.getElementById("linkedin-url").value = savedLinkedin;

  if (savedApifyKey) document.getElementById("apify-key").value = savedApifyKey;
  if (savedJD) document.getElementById("jd-text").value = savedJD;
  if (savedJdUrl) document.getElementById("jd-url").value = savedJdUrl;
  if (savedPrompt) document.getElementById("prompt-text").value = savedPrompt;

  // Fast mode toggle
  const fastBox = document.getElementById("fast-mode-checkbox");
  if (fastBox) {
    fastBox.checked = loadFromStorage(STORAGE_KEYS.fastMode) === "1";
    fastBox.addEventListener("change", () => {
      saveToStorage(STORAGE_KEYS.fastMode, fastBox.checked ? "1" : "0");
    });
  }

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
    });
  }

  // Restore tailored results
  if (savedTailored) {
    tailoredData = savedTailored;
    savedJDText = savedJDContext || "";
    lastAtsReport = loadJSONFromStorage(STORAGE_KEYS.ats);
    renderResults(tailoredData);
    renderAtsPanel(lastAtsReport);
    resultsSection.classList.add("active");
  }

  // Update setup status (will auto-collapse if ready)
  updateSetupStatus();

  // Render tracker
  renderTracker();
}

restoreState();
