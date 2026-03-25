# Resume Tailor

AI-powered resume tailoring app that optimizes your resume for specific job descriptions. Supports both **Anthropic Claude** and **Google Gemini** as AI providers.

Upload your resume once, paste a job description, and get a tailored resume optimized for ATS systems and recruiter review — in seconds.

## Features

- **AI-Powered Tailoring** — Rewrites your resume to match job descriptions using advanced prompt engineering (keyword optimization, ATS scoring, power verbs, metrics)
- **Dual AI Provider Support** — Choose between Claude (Sonnet 4.5) or Gemini (2.0 Flash, free tier available)
- **Application Q&A** — Generates first-person answers to job application questions using your resume and JD as context
- **JD Scraping** — Paste a job posting URL (LinkedIn, Indeed, Glassdoor, etc.) and auto-extract the description via Apify
- **PDF Export** — Download your tailored resume as a clean, ATS-friendly PDF
- **Job Application Tracker** — Track all your applications with status, company, role, and links — exportable to Excel
- **Persistent State** — All inputs (API keys, resume, prompt, JD) are saved to localStorage and restored on reload
- **Dark Theme** — Clean, modern UI built with vanilla HTML/CSS/JS

## Quick Start

### Prerequisites

- Python 3.9+
- An API key from at least one provider:
  - [Anthropic API key](https://console.anthropic.com/) (for Claude)
  - [Google AI Studio API key](https://aistudio.google.com/apikey) (for Gemini — free tier available)
- (Optional) [Apify API token](https://console.apify.com/) for URL-based JD scraping

### Installation

```bash
# Clone or download the project
cd resume-tailor-app

# Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Run

```bash
source venv/bin/activate  # if not already activated
python app.py
```

Open **http://localhost:5000** in your browser.

> **Note:** macOS may have AirPlay Receiver on port 5000. If there's a conflict, disable it in System Settings > General > AirDrop & Handoff.

## Usage

### 1. Choose Your AI Provider

Select **Claude** or **Gemini** from the AI Provider dropdown and enter the corresponding API key. Gemini 2.0 Flash is a great free option for getting started.

### 2. Upload Your Resume

In the "Your Profile" section, upload your resume (.docx, .pdf, or .txt). This only needs to be done once — it's saved in your browser.

### 3. Add a Job Description

Either:
- **Paste** the job description text directly, or
- **Enter a URL** and click "Scrape JD" (requires an Apify API token) to auto-extract it from LinkedIn, Indeed, Glassdoor, and other platforms

### 4. Tailor

Click **"Tailor My Resume"**. The AI will analyze the JD, map it against your experience, and produce a tailored resume optimized for that specific role.

### 5. Review and Download

- Preview the tailored resume in the browser
- Click **"Download PDF"** to get an ATS-friendly PDF
- Switch to the **"Application Q&A"** tab to generate answers to application questions

### 6. Repeat

For each new application, just change the job description and click "Tailor My Resume" again. Your resume and prompt stay saved.

## Custom Tailoring Prompt

The app includes a comprehensive built-in tailoring prompt that covers ATS optimization, keyword matching, bullet rewriting, and more. You can optionally upload or paste your own custom prompt to override it.

## Architecture

```
app.py              — Flask backend (routes, AI API calls, PDF generation, JD scraping)
requirements.txt    — Python dependencies
templates/
  index.html        — Single-page HTML
static/
  app.js            — Frontend logic (vanilla JS)
  style.css         — Dark theme styling
```

**Backend:** Flask with 5 API routes. AI calls are abstracted behind a provider pattern — `call_ai(provider, key, prompt)` routes to Claude or Gemini. Resume parsing supports .docx, .pdf, and .txt. PDF generation uses fpdf2 with Helvetica (no custom fonts needed).

**Frontend:** Vanilla JS with localStorage persistence. No build step, no framework dependencies.

## API Routes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Serve the app |
| POST | `/api/tailor` | Tailor a resume (multipart form: resume file + JD + provider + API key) |
| POST | `/api/answer-questions` | Generate Q&A answers (JSON: questions + JD + resume + provider + API key) |
| POST | `/api/download-pdf` | Generate and download a PDF from resume JSON |
| POST | `/api/scrape-jd` | Scrape a job description from a URL via Apify |

## License

MIT
