/* ─────────────────────────────────────────────
   Resume Tailor — Frontend Logic
   ───────────────────────────────────────────── */

let tailoredData = null;
let savedJDText = "";  // Preserved JD context for Q&A
let scrapedMetadata = null;  // Metadata from last URL scrape

// ── LocalStorage persistence ──
const STORAGE_KEYS = {
  apiKey: "rt_api_key",
  apifyKey: "rt_apify_key",
  provider: "rt_provider",
  geminiKey: "rt_gemini_key",
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

// ── Provider toggle ──
const providerSelect = document.getElementById("ai-provider");
const claudeKeyGroup = document.getElementById("claude-key-group");
const geminiKeyGroup = document.getElementById("gemini-key-group");

function updateProviderUI() {
  const provider = providerSelect.value;
  claudeKeyGroup.style.display = provider === "claude" ? "" : "none";
  geminiKeyGroup.style.display = provider === "gemini" ? "" : "none";
}

providerSelect.addEventListener("change", () => {
  saveToStorage(STORAGE_KEYS.provider, providerSelect.value);
  updateProviderUI();
});

function getActiveApiKey() {
  const provider = providerSelect.value;
  if (provider === "gemini") {
    return document.getElementById("gemini-key").value.trim();
  }
  return document.getElementById("api-key").value.trim();
}

// ── Auto-save text inputs ──
document.getElementById("api-key").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.apiKey, e.target.value);
});
document.getElementById("gemini-key").addEventListener("input", (e) => {
  saveToStorage(STORAGE_KEYS.geminiKey, e.target.value);
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

  const provider = providerSelect.value;
  const apiKey = getActiveApiKey();
  const jd = document.getElementById("jd-text").value.trim();

  const keyLabel = provider === "gemini" ? "Gemini" : "Anthropic";
  if (!apiKey) return showError(`Please enter your ${keyLabel} API key.`);
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
    saveToStorage(STORAGE_KEYS.tailoredData, tailoredData);
    saveToStorage(STORAGE_KEYS.savedJD, savedJDText);
    renderResults(tailoredData);
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

// ── Render Results ──
function renderResults(data) {
  const preview = document.getElementById("resume-preview");
  preview.innerHTML = buildResumeHTML(data);

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

    const cd = res.headers.get("Content-Disposition");
    let filename = "Resume.pdf";
    if (cd) {
      const match = cd.match(/filename=(.+)/);
      if (match) filename = match[1].replace(/"/g, "");
    }

    a.download = filename;
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
  const provider = providerSelect.value;
  const apiKey = getActiveApiKey();
  const keyLabel = provider === "gemini" ? "Gemini" : "Anthropic";

  if (!questions) return showError("Please paste at least one question.");
  if (!apiKey) return showError(`Please enter your ${keyLabel} API key above.`);
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
function restoreState() {
  // Restore text inputs
  const savedApiKey = loadFromStorage(STORAGE_KEYS.apiKey);
  const savedApifyKey = loadFromStorage(STORAGE_KEYS.apifyKey);
  const savedProvider = loadFromStorage(STORAGE_KEYS.provider);
  const savedGeminiKey = loadFromStorage(STORAGE_KEYS.geminiKey);
  const savedJD = loadFromStorage(STORAGE_KEYS.jd);
  const savedJdUrl = loadFromStorage(STORAGE_KEYS.jdUrl);
  const savedPrompt = loadFromStorage(STORAGE_KEYS.promptText);
  const savedTailored = loadJSONFromStorage(STORAGE_KEYS.tailoredData);
  const savedJDContext = loadFromStorage(STORAGE_KEYS.savedJD);
  const savedPromptMode = loadFromStorage(STORAGE_KEYS.promptMode);

  if (savedProvider) providerSelect.value = savedProvider;
  updateProviderUI();

  // Restore LinkedIn URL
  const savedLinkedin = loadFromStorage("rt_linkedin_url");
  if (savedLinkedin) document.getElementById("linkedin-url").value = savedLinkedin;

  if (savedApiKey) document.getElementById("api-key").value = savedApiKey;
  if (savedGeminiKey) document.getElementById("gemini-key").value = savedGeminiKey;
  if (savedApifyKey) document.getElementById("apify-key").value = savedApifyKey;
  if (savedJD) document.getElementById("jd-text").value = savedJD;
  if (savedJdUrl) document.getElementById("jd-url").value = savedJdUrl;
  if (savedPrompt) document.getElementById("prompt-text").value = savedPrompt;

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
    renderResults(tailoredData);
    resultsSection.classList.add("active");
  }

  // Update setup status (will auto-collapse if ready)
  updateSetupStatus();

  // Render tracker
  renderTracker();
}

restoreState();
