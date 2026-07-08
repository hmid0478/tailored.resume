# Resume Tailor

AI-powered resume tailoring app that rewrites (or keyword-swaps) your resume for a specific job description and exports a clean PDF — all driven from a single page in your browser.

Multi-provider: works with **Anthropic Claude**, **Google Gemini**, **OpenAI**, **OpenRouter**, **Groq**, **Together AI**, **Ollama (local)**, or any OpenAI-compatible endpoint.

## What it does:

1. You upload your resume once (`.docx` / `.pdf` / `.txt`).
2. You paste a job description.
3. The app tailors the resume to the JD — either a full rewrite (keyword optimization, power verbs, metrics) or, in **Keyword-swap only** mode, it keeps your exact wording/lengths and only swaps technology terms to match the JD (including older roles).
4. You preview the tailored resume, tweak the contact fields, and download the PDF (auto-named from the JD's company + job title).

## Features

- **Accounts & admin portal** — private app behind a login. A separate admin portal (`/admin`) creates/deletes users. Each user's data (resumes, settings, keys) is isolated to their account. See [Authentication & Multi-User](#authentication--multi-user).
- **Multi-provider AI** — Anthropic, Gemini, OpenAI, OpenRouter, Groq, Together AI, Ollama, and any OpenAI-compatible base URL. Keys, models, and base URLs are stored **per-user on the server**, so they follow the account across devices and never need re-entering.
- **Keyword-swap only mode** — a toggle (on by default) that preserves your resume's exact wording, sentence lengths, and structure, and only substitutes technology keywords to match the JD across every section including older roles. Turn it off for a full aggressive rewrite.
- **Resume-section-loss guard** — if the AI returns a resume with fewer sections than the input, it retries with an explicit section list and keeps the better result.
- **JSON repair** — 3-tier fallback (lightweight repair → `json-repair` library → raw dump) so transient LLM JSON quirks (unterminated strings, unquoted keys, single quotes, missing commas) self-heal.
- **4 PDF templates** — Minimal Clean (default), Modern Green, Classic Blue, Universal. All ATS-safe (Helvetica, single column, no tables/images).
- **Auto-naming from the JD** — the tailor step reads the hiring company and job title from the job description and auto-fills the Company / Job title fields (filename becomes `{company} - {title}.pdf`). If only one is present it uses that; if neither, the fields stay blank for you to type.
- **Application Q&A** — generates first-person answers to job application questions using your tailored resume + JD as context.
- **Job application tracker** — built-in CRUD table tracks every application (date, company, role, status). Exportable to clipboard for Excel.
- **Quick-copy LinkedIn bar** — one-click copy of your LinkedIn URL at the top.
- **Persistent state** — every input (API keys, resume, prompt, JD, keyword-swap toggle, template choice, company/title fields, tailored result) is saved per-user (localStorage + server) and restored on reload.
- **Robust error handling** — auto-retry on transient provider 5xx errors with exponential backoff (1.5s → 4s → 9s); friendly messages for invalid keys, exhausted quotas, and overloaded models.
- **Dark theme** — vanilla HTML/CSS/JS, no framework, no build step.

## Quick Start

### Prerequisites

- Python 3.9+ (tested on 3.11)
- An API key for at least one provider (links below)

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

Paste the JD into the textarea.

### 4. (Optional) Fill Company name + Job title

These two fields next to the Download PDF button drive the filename: `{company} - {title}.pdf`. They **auto-fill from the job description** when you tailor (company + job title read straight from the JD); edit them if you want.

### 5. Tailor

Click **Tailor My Resume**. Leave **Keyword-swap only** ticked (default) to preserve your exact wording and just swap technologies to match the JD; untick it for a full rewrite.

### 6. Review

- Resume preview shows your tailored resume.
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
app.py              — All backend logic (routes, AI providers, tailoring, PDF gen)
auth.py             — Password hashing, signed tokens, route guards
store.py            — Persistent storage (Upstash Redis / local JSON fallback)
requirements.txt    — Python dependencies
templates/
  index.html        — Single-page HTML
static/
  app.js            — Frontend logic (vanilla JS, localStorage)
  style.css         — Dark theme
```

**Backend** — Flask with the routes listed below. AI calls go through `call_ai(provider, api_key, prompt, ...)` which dispatches to `_call_anthropic`, `_call_gemini`, or `_call_openai_compatible` (the last covers OpenAI, OpenRouter, Groq, Together, Ollama, LM Studio, and any custom OpenAI-compatible endpoint by swapping `base_url`). Anthropic prompt caching is enabled on the system block. JSON parsing uses a 3-tier repair chain. Auto-retry on transient 5xx with exponential backoff. ASCII-safe sanitization on all prompts and headers.

**Tailoring** — a single AI call turns the resume + JD into structured JSON. In keyword-swap mode a conservative prompt preserves wording/lengths and only aligns technology terms; otherwise the full rewrite prompt applies. The company and job title are extracted from the JD in the same call for auto-naming. A section-loss guard retries once if the model drops a section.

**Frontend** — Vanilla JS with per-user localStorage persistence (synced to the server for keys/models/prefs). No build step.

## API Routes

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Serve the app (redirects to `/login` client-side if not signed in) |
| GET | `/login` | User login page |
| GET | `/admin` | Admin portal (login + user management) |
| POST | `/api/login` | User login → signed bearer token |
| GET | `/api/me` | Current signed-in identity (requires user token) |
| POST | `/api/admin/login` | Admin login (fixed credentials) → admin token |
| GET / POST | `/api/admin/users` | List / create users (admin token) |
| DELETE | `/api/admin/users/<email>` | Delete a user and all their resumes (admin token) |
| GET / POST | `/api/settings` | Load / save the user's provider keys, models, and prefs |
| GET / POST | `/api/resumes` | List / save the signed-in user's private resumes |
| GET / DELETE | `/api/resumes/<id>` | Fetch / delete one of the user's resumes |
| POST | `/api/tailor` | Tailor a resume (requires user token) — returns `{data, job_meta}` |
| POST | `/api/answer-questions` | Generate Q&A answers from JD + tailored resume (requires user token) |
| POST | `/api/download-pdf` | Render a tailored resume JSON as PDF (requires user token) |

All `/api/*` routes except `/api/login` and `/api/admin/login` require a valid `Authorization: Bearer <token>` header. The frontend attaches this automatically and redirects to `/login` only on a session-expiry `401`.

Bump `ATS_MAX_IMPROVE_PASSES` higher if you want it to grind harder; lower `ATS_NO_PROGRESS_STOP` to fail faster on hopeless cases.

## Authentication & Multi-User

The app is private. The first page every visitor sees is `/login`. Users sign in with
credentials **created by the admin** — there is no self-signup.

- **Admin portal** — `/admin`. Separate login, fixed credentials (below). The admin
  creates and deletes users (email + password). Each created user can then sign in at `/login`.
- **Per-user isolation** — every user's tailored resumes are stored server-side keyed
  by their email and are only visible to them (`/api/resumes`). Client-side app state
  (JD, keys, tracker) is also namespaced per user in the browser.
- **Tokens** — login returns a signed, stateless bearer token (7-day expiry) — no
  server-side session store, which is what keeps it working on serverless.

### Admin credentials

Defaults (overridable via env vars `ADMIN_EMAIL` / `ADMIN_PASSWORD`):

```
email:    contact.hf3@gmail.com
password: @Uckhan@6435
```

### Storage backend

User accounts and saved resumes need persistent storage. Selection is automatic:

- **Upstash Redis** (production) — used when `UPSTASH_REDIS_REST_URL` +
  `UPSTASH_REDIS_REST_TOKEN` (or Vercel KV's `KV_REST_API_URL` / `KV_REST_API_TOKEN`)
  are set. Stateless HTTPS REST — ideal for Vercel serverless.
- **Local JSON file** (dev fallback) — used automatically when no Redis creds are
  present. Writes to `./.data/rt_store.json` locally so you can run with zero setup.
  On Vercel without Redis this falls back to `/tmp`, which is **ephemeral** — set up
  Upstash for any real deployment.

### Required environment variables (production)

| Variable | Required? | Purpose |
|---|---|---|
| `APP_SECRET` | **Yes** | Signs auth tokens. Use a long random string. Without it, tokens use an insecure dev fallback. |
| `UPSTASH_REDIS_REST_URL` | **Yes** (prod) | Upstash Redis REST endpoint. |
| `UPSTASH_REDIS_REST_TOKEN` | **Yes** (prod) | Upstash Redis REST token. |
| `ADMIN_EMAIL` | No | Override the default admin email. |
| `ADMIN_PASSWORD` | No | Override the default admin password. |

To provision Upstash on Vercel: **Storage → Marketplace → Upstash for Redis → Connect
to project**. Vercel injects the two `UPSTASH_REDIS_REST_*` vars automatically. Then add
`APP_SECRET` under **Settings → Environment Variables** and redeploy.

## Deployment (Vercel)

The repo includes `vercel.json` and `api/index.py` so it can be deployed straight to Vercel as a serverless Python function.

### Steps

1. Push the repo to GitHub (or connect from your Git provider).
2. On https://vercel.com/new, import the repo. Vercel auto-detects the Python build via `vercel.json`.
3. **Do NOT set any environment variables for AI keys** — keys are entered in the browser per-request and stored client-side.
4. **Add auth/storage env vars** (see [Authentication & Multi-User](#authentication--multi-user)): connect **Upstash for Redis** from the Storage tab, and set `APP_SECRET` to a long random string. Optionally override `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
5. (Optional) Set `FLASK_DEBUG=0` (default).
6. Deploy. The first cold start takes ~3–5 seconds; subsequent requests are warm.
7. Visit `/admin`, sign in with the admin credentials, and create your first user.

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
