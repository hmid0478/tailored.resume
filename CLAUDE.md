# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
pip install -r requirements.txt
python app.py
# Opens at http://localhost:5000
```

Flask runs in debug mode on port 5000. Note: macOS may have AirPlay Receiver on port 5000 — disable it in System Settings if there's a conflict.

## Architecture

Single-page Flask app with vanilla JS frontend. No build step.

**Backend (`app.py`)** — Flask server with 5 routes:
- `GET /` — serves the HTML template
- `POST /api/tailor` — accepts resume file (multipart) + JD text + tailoring prompt, calls Claude API, returns structured JSON resume
- `POST /api/download-pdf` — accepts JSON resume structure, generates PDF via fpdf2, returns binary
- `POST /api/scrape-jd` — accepts a job posting URL + Apify token, crawls via Apify website-content-crawler actor, extracts text and metadata (platform, company, position)
- `POST /api/answer-questions` — accepts application questions + JD + resume context, calls Claude to generate first-person answers

**Frontend (`static/app.js` + `templates/index.html`)** — vanilla JS, no framework:
- All user inputs (API keys, JD text, prompt text) auto-save to `localStorage` on every keystroke
- Uploaded files (resume, prompt) are persisted as base64 in `localStorage` and restored into file inputs on reload via `DataTransfer` API
- The "Your Profile" section (resume + prompt) is collapsible and auto-collapses once both are configured
- Job Application Tracker is a client-side CRUD table stored in `localStorage`, with TSV export for Excel

**Key data flow:** Frontend sends `FormData` to `/api/tailor` → backend parses files, sends resume text + JD + prompt to Claude API → Claude returns JSON with exact resume structure → frontend renders preview and stores result → user clicks "Download PDF" which POSTs the JSON to `/api/download-pdf` → backend generates PDF with fpdf2.

## External Services

- **Anthropic Claude API** (`claude-sonnet-4-5-20250929`) — resume tailoring and Q&A generation. API key entered in UI, passed per-request.
- **Apify** (`apify~website-content-crawler` actor) — optional, for scraping job descriptions from URLs. Token entered in UI.

## Key Implementation Details

- Resume parsing supports `.docx` (python-docx), `.pdf` (PyPDF2), `.txt` — all converge to plain text via `PARSERS` dict
- The Claude prompt instructs the model to return **only raw JSON** (no markdown fences). Backend strips fences as fallback before `json.loads()`
- PDF generation (`ResumePDF` class) uses Helvetica only — no custom fonts. Unicode characters are manually replaced with ASCII equivalents before rendering
- Job metadata extraction (`extract_job_metadata`) uses platform-specific regex patterns for title parsing (LinkedIn: "Title at Company", Indeed: "Title - Company", Glassdoor: "Company hiring Title", etc.)
- The `/api/tailor` prompt explicitly tells Claude to return only the resume JSON — no change log or interview prep — to minimize token usage

## File Layout

```
app.py              — all backend logic (routes, Claude API, PDF gen, scraping)
requirements.txt    — Python deps (flask, anthropic, python-docx, fpdf2, PyPDF2, requests, python-dotenv)
templates/
  index.html        — single-page HTML (Jinja2 template, though no dynamic templating is used)
static/
  app.js            — all frontend logic (~870 lines)
  style.css         — dark theme, CSS variables, responsive
```
