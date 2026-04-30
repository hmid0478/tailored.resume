# Resume Tailor

AI-powered resume tailoring app that rewrites your resume for a specific job description, scores it against ATS keyword coverage, auto-fixes anything below 85%, and exports a clean PDF — all driven from a single page in your browser.

Multi-provider: works with **Anthropic Claude**, **Google Gemini**, **OpenAI**, **OpenRouter**, **Groq**, **Together AI**, **Ollama (local)**, or any OpenAI-compatible endpoint.

## What it does

1. You upload your resume once (`.docx` / `.pdf` / `.txt`).
2. You paste a job description (or scrape one from a URL).
3. The app runs a multi-step pipeline:
   - **Tailor** — rewrites the resume to match the JD using a comprehensive prompt (keyword optimization, ATS rules, power verbs, metrics).
   - **Score** — separate AI pass acts as an ATS scorer, returns a 0–100 keyword-coverage score with matched / missing keyword chips.
   - **Auto-fix** — if score < 85, runs up to 4 remediation passes with a 10-item diagnostic checklist (terminology mismatch, buried keywords, summary front-loading, etc.) until the score hits the 85% target. Best-result preservation: a worse rewrite never replaces a better one.
4. You preview the tailored resume with **per-section keyword density highlighting**, see the **score history** (e.g. `62 → 78 → 86`), optionally view a **side-by-side diff** of what the auto-fix changed, and download the PDF.

## Features

- **Multi-provider AI** — Anthropic, Gemini, OpenAI, OpenRouter, Groq, Together AI, Ollama, and any OpenAI-compatible base URL. Keys, models, and base URLs persist per-provider.
- **ATS scoring + auto-fix loop** — every tailoring is verified to ≥85% coverage; aggressive remediation kicks in when scores drop below 80%.
- **Resume-section-loss guard** — if any AI pass returns a resume with fewer sections than the input, it's rejected and the previous version is kept.
- **JSON repair** — 3-tier fallback (lightweight repair → `json-repair` library → raw dump) so transient LLM JSON quirks (unterminated strings, unquoted keys, single quotes, missing commas) self-heal.
- **JD keyword preview** — free heuristic mode (regex, no AI cost) or AI-extracted mode shows the 25–50 keywords the ATS will score against, before you spend tokens.
- **Auto-fix diff view** — word-level red/green diff of every field the remediation changed (per bullet, per skill category).
- **Per-section density heatmap** — JD keywords highlighted inline in the preview with a hit-count badge per section.
- **Fast mode** — checkbox next to the Tailor button skips the score+improve loop for a single AI call (3–6× faster).
- **4 PDF templates** — Minimal Clean (default), Modern Green, Classic Blue, Universal. All ATS-safe (Helvetica, single column, no tables/images).
- **Filename builder** — Company name + Job title fields directly next to the Download PDF button. Filename auto-builds as `{company} - {title}.pdf`. Auto-fills from scraped JD metadata; persists across reloads.
- **Application Q&A** — generates first-person answers to job application questions using your tailored resume + JD as context.
- **JD scraping** — paste a job posting URL (LinkedIn, Indeed, Glassdoor, Lever, Greenhouse, Workday, etc.) and auto-extract the description via Apify.
- **Job application tracker** — built-in CRUD table tracks every application (date, platform, company, role, status, link). Exportable to clipboard for Excel.
- **Quick-copy LinkedIn bar** — one-click copy of your LinkedIn URL at the top.
- **Persistent state** — every input (API keys, resume, prompt, JD, fast-mode toggle, template choice, company/title fields, ATS report, score history, scratch state) is saved to localStorage and restored on reload.
- **Robust error handling** — auto-retry on transient provider 5xx errors with exponential backoff (1.5s → 4s → 9s); friendly messages for invalid keys, exhausted quotas, and overloaded models.
- **Dark theme** — vanilla HTML/CSS/JS, no framework, no build step.

## Quick Start

### Prerequisites

- Python 3.9+ (tested on 3.11)
- An API key for at least one provider (links below)
- (Optional) [Apify API token](https://console.apify.com/) for URL-based JD scraping

### Get an API key

| Provider | Where to get a key | Free-tier note |
|---|---|---|
| Anthropic | https://console.anthropic.com/ | $5 starting credit |
| Google Gemini | https://aistudio.google.com/apikey | `gemini-2.5-flash-lite`: 1,500 req/day free |
| OpenAI | https://platform.openai.com/api-keys | Pay-as-you-go |
| OpenRouter | https://openrouter.ai/keys | Pay-as-you-go, many models |
| Groq | https://console.groq.com/keys | Generous free tier |
| Together AI | https://api.together.xyz/settings/api-keys | $1 starting credit |
| Ollama | n/a — runs locally on `localhost:11434` | Free, self-hosted |

### Installation

```bash
git clone https://github.com/hmid0478/tailored.resume.git
cd tailored.resume

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Run

```bash
python app.py
```

Open **http://localhost:5000** in your browser.

> **Port 5000 conflict on macOS:** disable AirPlay Receiver in System Settings → General → AirDrop & Handoff.

> **Debug mode:** off by default. Set `FLASK_DEBUG=1` to enable.

## Usage

### 1. Choose AI Provider + Model + Key

The provider dropdown lists 8 options. Picking one updates the key field label and pre-fills a sensible default model (editable). For Ollama and "OpenAI-compatible (custom)" a Base URL field appears. Keys are stored per-provider — switching providers doesn't wipe other keys.

### 2. Upload your resume

In the "Your Profile" section, upload `.docx`, `.pdf`, or `.txt`. Once uploaded it's stored in localStorage and reused across all future tailoring runs.

### 3. Add a job description

- Paste the JD into the textarea, **or**
- Paste a URL into the input above and click **Scrape JD** (needs an Apify token).

Optionally click **Preview keywords (free)** under the JD to see what the ATS will score against — no API call.

### 4. (Optional) Fill Company name + Job title

These two fields next to the Download PDF button drive the filename: `{company} - {title}.pdf`. They auto-fill from scraped JD metadata once you tailor.

### 5. Tailor

Click **Tailor My Resume**. The pipeline runs through tailor → score → (auto-fix loop if needed) → done. Watch the ATS panel for the score and progression history.

If you want maximum speed and don't need the score, tick **Fast mode** before clicking Tailor — it skips the verification loop.

### 6. Review

- Resume preview shows your tailored resume with **JD keywords highlighted** inline and a **density badge** on each section header (green ≥6 hits, yellow 3–5, red 1–2).
- ATS panel shows the score, target/floor, matched / missing keyword chips, and (if auto-fix ran) a **View auto-fix changes** button that opens a per-field word-level diff.
- Edit name / contact / LinkedIn fields above the preview if needed.
- Pick a PDF template — **Minimal Clean** is the default.

### 7. Download

Click **Download PDF**. The file saves with the company-and-title filename you set.

Switch to the **Application Q&A** tab to generate first-person answers to the application's free-text questions.

### 8. Repeat

For the next application, just change the JD (and optionally the company/title fields) and click Tailor again. Everything else stays put.

## PDF Templates

| Template | Layout | Best for |
|---|---|---|
| **Minimal Clean** *(default)* | Large regular-weight name, stacked contact lines, thin grey rule above each heading, regular-weight headings, large regular-weight job titles, disc bullets with `**bold**` markdown support, plain-paragraph skills, degree-first education | Modern minimalist resumes; ATS-friendly |
| **Modern Green** | Olive-green title-case headings with rule below, left-aligned large bold name, dash bullets | Engineering / startup roles |
| **Classic Blue** | Steel-blue UPPERCASE headings, centered name, dash bullets | Traditional corporate / consulting |
| **Universal** | Dark grey UPPERCASE headings, left-aligned name, neutral colors | When in doubt |

All four use Helvetica only, single column, no tables, no images — fully ATS-parseable.

## Custom Tailoring Prompt

The app ships with a 5,000-word built-in prompt (`DEFAULT_TAILORING_PROMPT` in `app.py`) covering JD analysis, keyword matrix, bullet rewriting, ATS optimization checklist, and final quality checks. You can override it by uploading a custom prompt file (`.txt` / `.docx` / `.pdf`) or pasting one in the "Tailoring Prompt" card. The custom prompt is used as the system message; the user message (resume + JD + structured output instructions) is added by the backend.

## Architecture

```
app.py              — All backend logic (routes, AI providers, ATS pipeline, PDF gen, JD scraping)
requirements.txt    — Python dependencies
templates/
  index.html        — Single-page HTML
static/
  app.js            — Frontend logic (vanilla JS, localStorage)
  style.css         — Dark theme
```

**Backend** — Flask with the routes listed below. AI calls go through `call_ai(provider, api_key, prompt, ...)` which dispatches to `_call_anthropic`, `_call_gemini`, or `_call_openai_compatible` (the last covers OpenAI, OpenRouter, Groq, Together, Ollama, LM Studio, and any custom OpenAI-compatible endpoint by swapping `base_url`). Anthropic prompt caching is enabled on the system block. JSON parsing uses a 3-tier repair chain. Auto-retry on transient 5xx with exponential backoff. ASCII-safe sanitization on all prompts and headers.

**ATS pipeline** —

```
tailor → score → if score < 85 → improve → re-score → loop (max 4 passes, early-stop after 2 with no progress) → keep best
```

The improve pass is a **combined** score+improve call that returns both the new score AND the rewritten resume in one round-trip, halving latency vs. naive sequential calls. When score < 80, the prompt switches to "diagnose-then-fix" mode with a 10-item ATS failure-mode checklist.

**Frontend** — Vanilla JS with localStorage persistence. No build step. Per-provider keys/models/base URLs stored as separate maps so switching providers doesn't wipe other credentials.

## API Routes

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Serve the app |
| POST | `/api/tailor` | Tailor a resume (multipart: resume file + JD + provider + key + model + base_url + fast_mode) — returns `{data, ats}` |
| POST | `/api/jd-keywords` | Preview the keywords the ATS will score against (mode: `heuristic` is free / no AI; `ai` uses tokens) |
| POST | `/api/scrape-jd` | Scrape a JD from a URL via Apify |
| POST | `/api/answer-questions` | Generate Q&A answers from JD + tailored resume |
| POST | `/api/download-pdf` | Render a tailored resume JSON as PDF (accepts `template` parameter) |

## Tunables

These constants at the top of the ATS section in `app.py` are safe to edit:

```python
ATS_TARGET_SCORE = 85          # auto-fix loop runs until score >= this
ATS_FLOOR_SCORE = 80           # below this, switch to aggressive mistake-diagnosis mode
ATS_MAX_IMPROVE_PASSES = 4     # cap on rewrite attempts
ATS_NO_PROGRESS_STOP = 2       # bail out after N consecutive passes with no score gain
```

Bump `ATS_MAX_IMPROVE_PASSES` higher if you want it to grind harder; lower `ATS_NO_PROGRESS_STOP` to fail faster on hopeless cases.

## Deployment (Vercel)

The repo includes `vercel.json` and `api/index.py` so it can be deployed straight to Vercel as a serverless Python function.

### Steps

1. Push the repo to GitHub (or connect from your Git provider).
2. On https://vercel.com/new, import the repo. Vercel auto-detects the Python build via `vercel.json`.
3. **Do NOT set any environment variables for AI keys** — keys are entered in the browser per-request and stored client-side.
4. (Optional) Set `FLASK_DEBUG=0` (default).
5. Deploy. The first cold start takes ~3–5 seconds; subsequent requests are warm.

### Tier requirements (important)

The ATS pipeline can run multiple AI calls per tailoring (1 tailor + 1 score + up to 4 improve+score combined calls = 4–6 round-trips). Each call to a frontier model takes 5–25 seconds.

| Tier | Function timeout | Will normal mode work? | Will Fast mode work? |
|---|---|---|---|
| **Hobby (free)** | 10 s | No — pipeline will time out | Sometimes (close to limit) |
| **Pro** | 60 s (configurable up to 300 s) | Yes for most resumes | Yes |
| **Enterprise** | 900 s | Yes always | Yes |

`vercel.json` already sets `maxDuration: 60`. On Hobby this is silently clamped to 10s — **so you'll need Pro for the auto-fix loop to run end-to-end on Vercel**, or use **Fast mode** (single AI call, fits inside ~10s) for Hobby tier.

### Other constraints to know

- **Request body limit**: 4.5 MB on Hobby, 100 MB on Pro. Resume files are small (<1 MB typical) — fine on either tier.
- **Function bundle size**: ~50 MB unzipped on Hobby (250 MB on Pro). The deps come in around 30–40 MB, fits Hobby but tight.
- **Filesystem**: function directory is read-only; only `/tmp` is writable. The app already routes the failing-AI-response debug dump to `/tmp` when `VERCEL=1` is set.
- **Cold starts**: ~2–5 seconds extra latency on the first request after idle. Acceptable for a personal tool.
- **Outbound HTTPS**: works fine (Anthropic / Gemini / OpenAI / Apify all reachable).

### Local development is unchanged

`python app.py` still works as before. Vercel files are only used when deploying.

## License

MIT
