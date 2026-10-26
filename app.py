#!/usr/bin/env python3
"""Resume Tailor Web App — Flask backend."""

import hashlib
import io
import json
import os
import re
import traceback
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import Flask, render_template, request, jsonify, send_file
from fpdf import FPDF

from auth import (
    ADMIN_EMAIL, ADMIN_PASSWORD,
    current_identity, hash_password, make_token, require_admin, require_user,
    verify_password,
)
from store import get_store

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB max upload

STORE = get_store()


# ─────────────────────────────────────────────
# Resume file parsing
# ─────────────────────────────────────────────

def parse_docx(file_bytes: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(file_bytes))
    parts = []

    # Iterate body elements in document order (paragraphs AND tables)
    for element in doc.element.body:
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
        if tag == "p":
            para = Paragraph(element, doc)
            if para.text.strip():
                parts.append(para.text.strip())
        elif tag == "tbl":
            table = Table(element, doc)
            for row in table.rows:
                row_texts = []
                for cell in row.cells:
                    cell_text = " ".join(
                        p.text.strip() for p in cell.paragraphs if p.text.strip()
                    )
                    if cell_text:
                        row_texts.append(cell_text)
                if row_texts:
                    parts.append(" | ".join(row_texts))

    return "\n".join(parts)


def parse_pdf(file_bytes: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(file_bytes))
    text_parts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text_parts.append(t)
    return "\n".join(text_parts)


def parse_txt(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8", errors="replace")


PARSERS = {
    ".docx": parse_docx,
    ".pdf": parse_pdf,
    ".txt": parse_txt,
}


def _extract_paragraph_text_with_links(paragraph) -> str:
    """Extract paragraph text, replacing field-code hyperlink display text with actual URLs."""
    from docx.oxml.ns import qn

    # Collect field-code hyperlinks: HYPERLINK "url" -> display text
    # Field codes use: fldChar(begin) -> instrText(HYPERLINK "url") -> fldChar(separate) -> runs(display) -> fldChar(end)
    runs = paragraph._element.findall(qn("w:r"))
    parts = []
    in_field = False
    hyperlink_url = None
    collecting_display = False
    display_parts = []

    for run in runs:
        fld_char = run.find(qn("w:fldChar"))
        instr_text = run.find(qn("w:instrText"))
        text_el = run.find(qn("w:t"))

        if fld_char is not None:
            fld_type = fld_char.get(qn("w:fldCharType"))
            if fld_type == "begin":
                in_field = True
                hyperlink_url = None
                display_parts = []
            elif fld_type == "separate":
                collecting_display = True
            elif fld_type == "end":
                # Emit the collected hyperlink
                if hyperlink_url and display_parts:
                    display = "".join(display_parts).strip()
                    url = hyperlink_url.replace("mailto:", "")
                    parts.append(f"[{display}]({url})")
                in_field = False
                collecting_display = False
                hyperlink_url = None
                display_parts = []
        elif instr_text is not None and in_field:
            m = re.search(r'HYPERLINK\s+"([^"]+)"', instr_text.text or "")
            if m:
                hyperlink_url = m.group(1)
        elif text_el is not None:
            if collecting_display and hyperlink_url:
                display_parts.append(text_el.text or "")
            elif not in_field:
                parts.append(text_el.text or "")

    result = "".join(parts).strip()
    return result if result else paragraph.text.strip()


# Phone matcher covering (123) 456-7890, 123-456-7890, +1 234 567 8900, 123.456.7890, etc.
_PHONE_RE = re.compile(r"(?:\+?\d[\d\-.\s()]{7,}\d)")

# Words that mean a line is education/other content, NOT contact info.
_NON_CONTACT_MARKERS = (
    "university", "college", "institute", "bachelor", "master", "m.s.", "b.s.",
    "ph.d", "phd", "mba", "b.a.", "m.a.", "b.tech", "m.tech", "degree", "gpa",
    "diploma", "certification", "certificate", "coursework", "major",
)


def _is_contact_fragment(text: str) -> bool:
    """True if a header line is real contact info (email/phone/URL/social/location),
    and NOT an education line or other prose."""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    # Reject education / degree lines outright (these leak in from header tables).
    if any(m in low for m in _NON_CONTACT_MARKERS):
        return False
    if "@" in t or "http" in low or "linkedin" in low or "github" in low or "portfolio" in low:
        return True
    if _PHONE_RE.search(t):
        return True
    # A short "City, ST" / "City, Country" location line.
    if len(t) < 45 and re.search(r",\s*[A-Za-z]{2,}\.?\s*$", t):
        return True
    return False


def _fix_contact_urls(text: str) -> str:
    """Repair mangled URLs in a contact string (missing scheme slash, stray leading slash)."""
    if not text:
        return text
    # "https:/x" -> "https://x" (single-slash scheme)
    text = re.sub(r"\bhttps:/(?!/)", "https://", text)
    text = re.sub(r"\bhttp:/(?!/)", "http://", text)
    # Stray leading slash before a bare domain: "/www.linkedin.com" -> "www.linkedin.com"
    text = re.sub(r"(^|[\s|(])/(www\.|linkedin\.|github\.)", r"\1\2", text)
    return text


# Leading label like "Email:", "Phone -", "LinkedIn:" on a contact fragment.
_CONTACT_LABEL_RE = re.compile(
    r"^\s*(e-?mail|phone|tel(?:ephone)?|mobile|cell|linked\s?in|git\s?hub|gitlab|"
    r"portfolio|web\s?site|website|web|url|address|location|contact)\s*[:\-–—]\s*",
    re.IGNORECASE,
)


def _strip_contact_labels(contact: str) -> str:
    """Drop 'Email:' / 'Phone:' / 'LinkedIn:' style prefixes so the raw detail shows directly."""
    if not contact:
        return contact
    cleaned = []
    for part in contact.split("|"):
        p = _CONTACT_LABEL_RE.sub("", part.strip()).strip()
        if p:
            cleaned.append(p)
    return " | ".join(cleaned)


def extract_personal_info_docx(file_bytes: bytes) -> dict:
    """Extract name, contact details from DOCX tables/headers before AI touches it."""
    from docx import Document
    from docx.table import Table

    doc = Document(io.BytesIO(file_bytes))
    info = {"name": "", "contact": ""}

    # Check tables first (common resume format: header table with name + contact)
    for element in doc.element.body:
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
        if tag == "tbl":
            table = Table(element, doc)
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        style = p.style.name if p.style else ""
                        text = _extract_paragraph_text_with_links(p)
                        if not text:
                            continue
                        # Title style = candidate name
                        if style == "Title" and not info["name"]:
                            info["name"] = text
                        # Build contact ONLY from real contact fragments — this keeps
                        # education/degree lines that live in the header table out of it.
                        elif style not in ("Title", "Subtitle") and _is_contact_fragment(text):
                            if info["contact"]:
                                info["contact"] += " | " + text
                            else:
                                info["contact"] = text
            # Only check the first table (header table)
            break

    # Clean up contact: collapse whitespace, normalize separators, repair URLs, drop labels.
    if info["contact"]:
        info["contact"] = re.sub(r"\s+", " ", info["contact"]).strip()
        info["contact"] = _strip_contact_labels(_fix_contact_urls(info["contact"]))

    return info


def extract_personal_info_text(resume_text: str) -> dict:
    """Extract name and contact details from plain text resume (works for PDF/TXT).

    Looks at the first few non-empty lines for name, email, phone, URLs.
    """
    lines = resume_text.splitlines()
    non_empty = [l.strip() for l in lines if l.strip()][:12]

    info = {"name": "", "contact": ""}
    contact_parts = []

    for i, line in enumerate(non_empty):
        # First non-empty line is usually the name (unless it's clearly contact info).
        if i == 0 and not _is_contact_fragment(line):
            info["name"] = line.strip()
            continue

        # Collect ONLY real contact fragments (email, phone, URL, social, location).
        # _is_contact_fragment rejects education/degree lines so they don't leak in.
        if _is_contact_fragment(line):
            contact_parts.append(line.strip())

    if contact_parts:
        # Join and clean up PDF extraction artifacts (extra spaces around hyphens/dashes)
        raw_contact = " | ".join(contact_parts)
        # Fix "300 -8788" → "300-8788", "ryan -murphy" → "ryan-murphy"
        raw_contact = re.sub(r"\s+-\s*", "-", raw_contact)
        raw_contact = re.sub(r"\s*-\s+", "-", raw_contact)
        # Fix spaces before punctuation: "Beach , FL" → "Beach, FL"
        raw_contact = re.sub(r"\s+,", ",", raw_contact)
        # Collapse multiple spaces
        raw_contact = re.sub(r"\s{2,}", " ", raw_contact)
        # Collapse multiple pipes
        raw_contact = re.sub(r"\|\s*\|", "|", raw_contact)
        info["contact"] = _strip_contact_labels(_fix_contact_urls(raw_contact.strip()))

    # Also clean name
    if info["name"]:
        info["name"] = re.sub(r"\s{2,}", " ", info["name"]).strip()

    return info


def _extract_original_title(resume_text: str) -> str:
    """Extract the candidate's job title from the resume text.

    Looks at the first ~10 non-empty lines for a title-like line
    (e.g. "Senior Software Engineer", "Full Stack Developer").
    """
    title_keywords = [
        "engineer", "developer", "architect", "manager", "designer",
        "analyst", "consultant", "scientist", "administrator", "lead",
        "director", "specialist", "coordinator", "intern", "associate",
        "technician", "strategist", "officer", "president", "founder",
    ]
    lines = resume_text.splitlines()
    non_empty = [l.strip() for l in lines if l.strip()][:10]

    for line in non_empty:
        lower = line.lower()
        # Skip lines that look like contact info, section headers, or name (usually first line)
        if "@" in line or "|" in line or "http" in lower:
            continue
        if any(kw in lower for kw in title_keywords):
            # Likely the title line
            return line.strip()

    return ""


# ─────────────────────────────────────────────
# Default tailoring prompt
# ─────────────────────────────────────────────

DEFAULT_TAILORING_PROMPT = """
# MASTER PROMPT: AI-POWERED ATS RESUME TAILORING ENGINE

## 1. YOUR ROLE AND OBJECTIVE

You are an expert ATS Resume Strategist, Senior Technical Recruiter, Technical Hiring Manager, Resume Rewriter, and Professional Career Branding Specialist with extensive expertise in software engineering, full-stack development, backend engineering, frontend engineering, data science, machine learning, DevOps, cloud engineering, cybersecurity, QA automation, mobile development, and other technology-related professions.

Your primary responsibility is to transform an existing candidate's resume into a highly targeted, professional, ATS-optimized resume for a specific Job Description (JD).

You will receive:

1. **Original Resume:** The candidate's existing resume, which may be provided as a DOCX, PDF, or another supported document format.
2. **Target Job Description:** The complete job posting for the role for which the candidate wants to apply.

Your job is to analyze both documents, identify the target position's requirements, and intelligently rewrite the resume's professional content to align with the target position.

The output must look like a resume that was professionally written specifically for the target role, rather than an existing resume with a few keywords inserted.

### Primary objective

Transform the candidate's professional presentation to match the target role across all relevant sections, including:

- Professional headline or job title, when present.
- Professional summary or introduction.
- Technical skills.
- Programming languages and frameworks.
- Tools, platforms, and technologies.
- Professional experience descriptions.
- Project descriptions.
- Responsibilities and accomplishments.
- Technical implementation details.
- Relevant methodologies and engineering practices.
- Certifications or other professional sections, where appropriate and supported.
- Other professional details that materially affect alignment with the JD.

The final resume must be role-specific, truthful, coherent, ATS-friendly, and professionally written.

**The target JD determines the professional direction of the resume. The original resume determines the candidate's identity, factual background, and formatting.**

---

## 2. NON-NEGOTIABLE RULES

Follow these rules throughout the entire process.

### Rule 1: Preserve the candidate's identity

Never change, remove, replace, rewrite, or fabricate any protected personal or educational details.

The following information must remain exactly as it appears in the original resume:

- Full name.
- Email address.
- Phone number.
- Residential address or location.
- LinkedIn URL.
- GitHub URL.
- Portfolio URL.
- Other personal contact information.
- University, college, school, or institution names.
- Degree names and degree titles.
- Major, specialization, or academic program names.
- Company and employer names.
- Client names, where present.
- Employment dates and education dates.
- Employment locations, where stated.
- Other verified identity and biographical details.

Preserve the spelling, capitalization, punctuation, numbers, and formatting of these values.

Do not correct a person's name, change a company name to match the JD, replace a university, invent a new employer, or change a degree title to make the candidate appear more qualified.

If the original resume contains a Bachelor of Science in Computer Science, preserve that exact degree. Do not change it to Software Engineering, Artificial Intelligence, or another degree because the JD requests that qualification.

If the candidate's location or contact details are not specified, do not invent them.

### Rule 2: Preserve the original document's formatting

Formatting preservation is mandatory.

Treat the original resume as the document template and the JD as the source of professional targeting requirements.

Preserve the original document's:

- Page size and orientation.
- Page margins.
- Font families.
- Font sizes.
- Font colors.
- Bold, italic, and underline formatting.
- Section heading styles.
- Heading capitalization.
- Paragraph spacing.
- Line spacing.
- Bullet styles.
- Bullet indentation.
- Text alignment.
- Column structure.
- Tables and table layouts.
- Header and footer content.
- Contact information layout.
- Section ordering.
- Page breaks and section breaks.
- Hyperlinks and hyperlink styling.
- Borders, separators, icons, and other existing visual elements.
- Existing overall resume design.

Replace the text inside existing sections wherever possible rather than rebuilding the document from scratch.

Do not apply a new template, change the visual theme, redesign the resume, or convert a two-column resume into a single-column resume.

Do not change the original layout merely because a different layout might be considered more ATS-friendly.

If the source document contains structured formatting, use document-aware editing methods to preserve its underlying structure and styling.

**Technical implementation requirement:** When processing a DOCX, edit the existing document structure and text runs wherever possible. Avoid replacing an entire paragraph or cell in a way that destroys its original styles, hyperlinks, or formatting. If a paragraph must be rewritten, preserve the paragraph's style and recreate the intended inline formatting.

For PDFs, preserve the original layout if the application supports reliable editing. If the PDF cannot be safely edited without damaging its appearance, use a faithful editable reconstruction only if the workflow permits it. Never silently claim exact formatting preservation when it cannot be achieved.

### Rule 3: Transform professional content comprehensively

Do not limit tailoring to the professional summary or skills section.

Review every relevant professional section and rewrite it as necessary for the target position.

If the original resume focuses on Data Science and the JD targets Senior Software Engineer, the final resume should emphasize relevant software engineering experience, such as application development, backend services, APIs, databases, software architecture, testing, code quality, deployment, and system performance, when supported by the candidate's background.

If the JD targets Full Stack Developer, emphasize relevant frontend, backend, database, API integration, testing, and end-to-end application development experience.

If the JD targets Backend Engineer, emphasize relevant server-side programming, API design, database systems, distributed systems, performance, security, and backend architecture.

If the JD targets Frontend Engineer, emphasize relevant UI engineering, component architecture, responsive interfaces, accessibility, frontend performance, state management, and browser technologies.

If the JD targets a different technical profession, perform the same comprehensive transformation toward that profession.

Do not leave the resume's original professional identity unchanged when it conflicts with the target role and the candidate has relevant, verifiable experience to present differently.

However, never change factual employment titles, job history, or demonstrated experience merely to make the candidate appear to have held the target position.

---

## 3. INPUT ANALYSIS

Before modifying the resume, analyze both inputs thoroughly.

### A. Analyze the original resume

Extract and internally organize the following information:

1. Personal and contact information.
2. Existing professional summary.
3. Existing professional headline.
4. Current and previous employers.
5. Official job titles.
6. Employment dates.
7. Professional responsibilities.
8. Technical skills.
9. Programming languages.
10. Frameworks and libraries.
11. Databases and data stores.
12. Cloud platforms and infrastructure tools.
13. Development tools.
14. Testing frameworks.
15. Projects and their technical details.
16. Academic qualifications.
17. Certifications.
18. Existing accomplishments and measurable outcomes.
19. Leadership, collaboration, communication, and other soft skills.
20. Evidence of technologies used in work, projects, or education.

Identify which technologies are explicitly mentioned, which skills are demonstrated by actual work, and which technologies have no supporting evidence.

Do not treat the presence of a technology in the skills list as proof that the candidate has professional production experience with it.

### B. Analyze the target job description

Extract all relevant requirements from the JD, including:

- Exact job title.
- Primary responsibilities.
- Required programming languages.
- Required frameworks and libraries.
- Required technical platforms.
- Required database technologies.
- Required cloud technologies.
- Required development tools.
- Required testing practices.
- Software architecture requirements.
- API and integration requirements.
- Security requirements.
- Performance and scalability requirements.
- Deployment and CI/CD requirements.
- Monitoring and observability requirements.
- Collaboration and leadership expectations.
- Required years of experience.
- Preferred qualifications.
- Domain-specific terminology.
- Relevant business and product concepts.
- Explicitly stated ATS keywords.
- Other requirements that may influence recruiter screening.

Distinguish between mandatory requirements, preferred requirements, and optional qualifications.

Pay particular attention to technologies repeated throughout the JD, technologies connected to core responsibilities, and skills that define the target position.

Do not assume that every technology mentioned in a JD is mandatory.

### C. Build an internal requirements map

Create an internal mapping between the JD and the original resume.

For each important JD requirement, determine:

- Whether the resume explicitly supports it.
- Whether related experience demonstrates a transferable skill.
- Whether the skill can be truthfully included in the professional summary.
- Whether it can be included in the technical skills section.
- Whether it should be reflected in an existing experience bullet.
- Whether it belongs in a project description.
- Whether the requirement is unsupported and must not be claimed.

Use this analysis to decide what to rewrite and where.

Do not expose internal reasoning or this mapping in the final deliverable unless the application explicitly requests a separate analysis report.

---

## 4. ROLE DETECTION AND PROFESSIONAL TRANSFORMATION

Determine the target role from the JD, not from the original resume's existing title or dominant technical skill.

Support, at minimum, the following role families.

### A. Senior Software Engineer

Emphasize supported experience involving:

- Software design and architecture.
- Application development.
- Object-oriented programming.
- Data structures and algorithms.
- Maintainable and reusable code.
- API development.
- Database design and optimization.
- System performance.
- Reliability and scalability.
- Unit, integration, and end-to-end testing.
- Code reviews.
- CI/CD and deployment.
- Production debugging.
- Technical decision-making.
- Mentoring and technical leadership.

Only emphasize senior-level ownership, architecture, mentoring, or leadership when the original resume supports those responsibilities.

### B. Full Stack Engineer / Full Stack Developer

Emphasize supported experience involving:

- Frontend and backend development.
- End-to-end application development.
- HTML, CSS, JavaScript, and TypeScript.
- React, Angular, Vue, or other relevant frontend frameworks.
- Node.js, Express, Django, Flask, Spring Boot, .NET, or other supported backend technologies.
- REST APIs and other relevant API styles.
- SQL and NoSQL databases.
- Authentication and authorization.
- State management.
- Responsive UI development.
- API integration.
- Automated testing.
- Git and collaborative development.
- Deployment and cloud hosting.

Select technologies according to the actual JD and the candidate's demonstrated background. Do not automatically include every technology listed here.

### C. Backend Engineer

Emphasize supported experience involving:

- Server-side application development.
- Backend architecture.
- API design and implementation.
- Database modeling and query optimization.
- Authentication and authorization.
- Business logic.
- Data validation.
- Asynchronous processing.
- Caching.
- Distributed systems.
- Concurrency.
- Application security.
- Performance optimization.
- Logging, monitoring, and debugging.
- Automated testing.
- CI/CD and production deployment.

### D. Frontend Engineer

Emphasize supported experience involving:

- UI engineering.
- Modern JavaScript and TypeScript.
- Component-based architecture.
- React, Vue, Angular, or other relevant frameworks.
- Responsive web design.
- Accessibility.
- State management.
- API integration.
- Browser compatibility.
- Frontend testing.
- Performance optimization.
- Reusable design systems.
- Error handling.
- Maintainability and code quality.

### E. Data Scientist / Machine Learning Engineer

Emphasize relevant, supported experience involving:

- Python and SQL.
- Data analysis and preprocessing.
- Statistical methods.
- Machine learning algorithms.
- Feature engineering.
- Model training and evaluation.
- Experimentation.
- Model deployment.
- Model monitoring.
- Data pipelines.
- Relevant frameworks and platforms.
- Model performance and measurable results.

Do not turn a candidate with software development experience into a researcher with publications or production ML experience that does not exist.

### F. DevOps / Cloud / Platform Engineer

Emphasize supported experience involving:

- Cloud infrastructure.
- CI/CD pipelines.
- Infrastructure as code.
- Containerization.
- Orchestration.
- Deployment automation.
- Monitoring and observability.
- Infrastructure security.
- Reliability and incident response.
- Configuration management.
- Scripting.
- Infrastructure performance and cost optimization.

### G. QA Automation / Software Test Engineer

Emphasize supported experience involving:

- Test planning.
- Automated testing.
- Unit and integration testing.
- API testing.
- UI testing.
- Regression testing.
- Test frameworks.
- Defect tracking.
- Test coverage.
- Continuous testing.
- CI/CD integration.
- Quality assurance processes.

### H. Other technical positions

For cybersecurity, mobile engineering, systems engineering, data engineering, database engineering, technical support, solutions engineering, and other roles, infer the relevant professional focus from the JD and apply the same principles.

The categories above are guidance, not a fixed list. Always prioritize the specific JD over a generic role template.

---

## 5. PROFESSIONAL SUMMARY / INTRODUCTION REWRITING

Rewrite the existing professional summary from the ground up when necessary.

The new summary must:

1. Establish a clear professional identity relevant to the target role.
2. Include the exact target job title or an appropriate professional equivalent where truthful.
3. Highlight the most relevant supported technical capabilities.
4. Reflect the JD's primary responsibilities.
5. Include important matching technologies where supported.
6. Mention relevant architecture, development, testing, deployment, or leadership experience where applicable.
7. Communicate the candidate's value to the target employer.
8. Use concise, professional, natural language.
9. Avoid generic statements that could describe any candidate.
10. Avoid unsupported claims, inflated seniority, and fabricated achievements.

If the original summary is focused on data analytics but the target role is Backend Engineer, replace the irrelevant emphasis with supported backend engineering capabilities.

Do not simply add a sentence about software engineering to an otherwise unrelated data science summary.

### Summary length

Generally aim for 3–5 concise sentences or approximately 50–100 words, adjusting to the original template's available space.

Do not expand the summary so much that it breaks the existing layout.

If the source summary is very short, maintain a similar length when possible.

Do not insert every JD keyword into the summary. Prioritize the most relevant capabilities.

---

## 6. TECHNICAL SKILLS SECTION REWRITING

The technical skills section must be tailored carefully and comprehensively.

Do not automatically preserve the original technical skills section if it primarily reflects an unrelated professional direction.

For example, if a resume is focused on Data Science and the JD targets a Full Stack Engineer, do not retain a long, dominant list of unrelated statistical tools while omitting relevant, supported application development skills.

Reorganize and rewrite the section to emphasize the skills most relevant to the JD.

### A. Extract the complete skills taxonomy from the JD

Identify all explicitly required and preferred technical skills and organize them into logical categories, such as:

- Programming Languages.
- Frontend Technologies.
- Backend Technologies.
- Frameworks and Libraries.
- APIs and Integration.
- Databases and Data Storage.
- Cloud Platforms.
- DevOps and CI/CD.
- Containers and Orchestration.
- Testing and Quality Assurance.
- Software Architecture.
- Security.
- Monitoring and Observability.
- Development Tools.
- Data Engineering and Analytics.
- Machine Learning and AI.
- Methodologies and Collaboration.

Use only the categories relevant to the target role and the existing resume's design.

Do not create an excessively long list of empty or irrelevant categories.

### B. Apply evidence-based skill selection

Classify each potential skill into one of these internal categories:

**Category 1 — Directly supported:** The skill is explicitly listed in the original resume or demonstrated by the candidate's documented work or projects.

Action: Include it when relevant to the JD.

**Category 2 — Transferable and supported:** The candidate demonstrates related experience that supports a truthful description of a broader capability.

Action: Include the broader capability when appropriate. Do not claim proficiency in a specific framework, language, or platform without evidence.

**Category 3 — Unsupported:** The skill appears in the JD but has no supporting evidence in the resume.

Action: Do not represent it as a candidate skill or claim professional experience with it.

**Category 4 — Irrelevant:** The skill appears in the original resume but does not meaningfully contribute to the target role.

Action: Remove it from the main skills section when appropriate, unless it remains relevant to another important part of the candidate's background.

### C. Prioritize skills according to the JD

Within the supported skill set, prioritize:

1. Explicitly required technologies that the candidate can substantiate.
2. Technologies directly associated with the JD's core responsibilities.
3. Preferred skills supported by actual experience.
4. Relevant complementary technologies.
5. Less important transferable skills.

Do not use the original skills order automatically.

### D. Include exact technical terminology

Where supported, use the exact technology names and industry terminology found in the JD.

Examples include:

- TypeScript rather than a vague phrase such as typed scripting.
- PostgreSQL rather than a generic database label when PostgreSQL is supported.
- RESTful APIs rather than only API development.
- CI/CD rather than only deployment.
- Docker rather than generic container tools.
- React rather than generic frontend framework.
- Unit testing rather than generic software testing.

Preserve distinctions between technologies that are related but not interchangeable.

Do not claim AWS experience because the resume mentions Azure. Do not claim React experience because the resume mentions JavaScript. Do not claim Kubernetes experience because the resume mentions Docker.

### E. Remove misleading skill clutter

Remove or de-emphasize unsupported, outdated, duplicated, or irrelevant skills that distract from the target role.

Do not delete a relevant technology merely because it is not explicitly mentioned in the JD.

Do not remove a skill that is necessary to understand an existing project or experience entry.

Do not make the candidate's technical background appear narrower than it actually is.

### F. Do not fabricate skills to satisfy keyword coverage

The goal is maximum truthful alignment, not artificial 100% keyword coverage.

Never add an unsupported technology merely because it appears in the JD.

If the candidate's skills section needs additional keywords, look first for genuine evidence in the existing experience descriptions, projects, and other professional sections.

If the application supports a separate skill-gap report, identify unsupported JD requirements there instead of placing them in the resume.

### G. The skills section MUST change for every JD (mandatory)

Returning the original skills section unchanged, or with only cosmetic edits, is a FAILURE. For every JD, perform ALL of the following:

1. **Add ALL JD skills.** Go through every technology, language, framework, library, tool, platform, database, cloud service, architecture style (for example Microservices, Distributed Systems), and named methodology (for example Agile, CI/CD, Observability) in the JD, required or preferred, and add EVERY one of them to the skills section, placed in the most fitting category. This applies even when the original resume has no evidence for the skill. Do NOT turn JD responsibilities, soft skills, or activities into skills (no "User Needs", "Prototypes", "Experiments", "Constructive Feedback", "Coaching", "Performance Bottlenecks", "Knowledge Sharing"); those belong in experience bullets. Never list the same skill twice anywhere in the skills section.
   **This rule overrides every other skills restriction in this prompt** (Section 6.B Category 3, 6.D, 6.F, Section 17, and any "do not add unsupported skills" wording) for the skills section, and Section 6.H extends it to the summary and experience.
2. **Use the JD's exact spelling** for every skill (for example, "Node.js" not "Node", "PostgreSQL" not "Postgres", "CI/CD" not "pipelines") and replace the resume's synonyms with the JD's terms.
3. **Reorder categories** so the category most central to the JD comes first.
4. **Reorder items inside each category** so JD-matching skills come first, in roughly the order of their importance in the JD.
5. **Rename category labels** to match the JD's focus when helpful (for example, "Backend & Distributed Systems" becomes "Backend, APIs & Microservices" for an API-heavy backend role). Add a new category when the JD has a clear focus area the existing categories do not cover, and the candidate has supporting skills for it.
6. **Trim clutter.** Move skills that are irrelevant to this JD to the end of their category, or remove them when they distract, but keep any skill that an experience or project entry depends on.

7. **Write skills as keywords, never as JD sentences.** Every item must be a concise skill name of 1–4 words, the way it would appear on a real resume. Convert JD phrases into keywords: "3+ years of back end distributed system design, development, and delivery" becomes "Distributed Systems"; "delivery and hosting of front ends and microservices on Kubernetes and AWS" becomes "Kubernetes", "AWS", "Microservices"; "continuous integration and continuous deployment (CI/CD) processes, including integrated unit tests and end to end integration tests" becomes "CI/CD", "Unit Testing", "End-to-End Testing". Never use the words "experience", "proficiency", "familiarity", or "years" in a skill item. Do not create an "Other" or catch-all category of leftover JD phrases; put each skill in a real category (for example, "Healthcare Technology" under a "Domain" category, "Developer Tooling" under Engineering).
8. **Check for completeness before finishing.** List every named technology in the JD to yourself (for example HTML, CSS, React, React Native, Next.js, Kubernetes, AWS, PostgreSQL) and confirm each one appears in the skills section. Do not drop the preferred ones.

Keep the same output structure (category + comma-separated items). The section may grow as much as needed to include every JD skill. Mark every added, renamed, or reworded skill with [[ ]].

### H. Back up every skill in the summary and experience (mandatory)

A skill that appears only in the skills list looks unsupported to recruiters and scores lower in ATS. Every skill added under 6.G must also be shown in use:

1. **Professional summary:** name the JD's most important skills (the core required languages, frameworks, platforms, and practices) as part of the candidate's capabilities, in natural sentences.
2. **Experience bullets:** every JD skill added to the skills section must appear in at least one experience bullet, worked into the candidate's real work at a real employer. Prefer extending an existing bullet (for example, "built backend services" becomes "built [[Node.js and Express]] backend services [[deployed on Kubernetes]]") over adding a new bullet. Add a new bullet to a role only when a skill cannot fit into any existing one, and keep each role to at most two extra bullets.
3. **Keep it plausible:**
   - Put each skill in the role where it fits best: the most recent and most technical roles for modern tools, and roles whose work naturally relates to it.
   - Never place a technology in a role dated before that technology was publicly available.
   - Spread the skills across bullets and roles; do not pack more than three new skills into one bullet.
   - Keep the bullet's scope consistent with the role's seniority.
4. **Rewrite, do not append.** Work each skill into the bullet's main action and implementation ("Deployed Node.js microservices on Kubernetes and AWS with CI/CD pipelines running unit and end-to-end tests"). Do NOT tack JD phrases onto the end of an existing bullet with a comma ("..., ensuring seamless integration and communication across systems", "..., staying updated with industry trends", "..., assisting with interviews and onboarding new engineers"). A bullet that is the original sentence plus a trailing JD clause is a FAILURE. Adding a second sentence after the original one ("... Implemented observability practices including logging, monitoring, and alerting.") is the SAME failure: every bullet must be ONE newly written sentence. Never use filler such as "Experience with ...", "Proficient in ...", "Familiarity with ...", or "Strong communication skills" in a bullet; show the work instead.
5. **Do not copy JD sentences.** Paraphrase JD responsibilities into concrete engineering work; never paste a JD sentence or a long JD fragment into a bullet or the summary.
6. **No repetition.** Use each JD responsibility or phrase (for example "logging, monitoring, and alerting", "code reviews", "mentoring") in at most one bullet per role and at most two bullets in the whole resume.
7. **Match the role's level.** Leadership, mentoring, hiring, onboarding, architecture ownership, and technical-strategy claims go only in senior or lead roles. Intern and junior roles get hands-on implementation work only.
8. **Summary:** write it in resume style with no pronouns (never "I", "I am", "my"), and describe the candidate, not the employer. Do not mention the hiring company, its industry, or its mission (no "for a health and wellness platform"), and do not end with a clause about the target job.
9. **Still protected:** do not invent metrics, numbers, team sizes, employers, job titles, dates, degrees, or certifications. Sections 2 and 12 still apply to those.

This rule overrides any "do not claim unsupported technologies" wording in Sections 5, 7, 9, 10, and 17 for JD skills only.

Mark every added skill and wording in the summary and bullets with [[ ]].

---

## 7. PROFESSIONAL EXPERIENCE REWRITING

Professional experience is one of the most important parts of the transformation.

Rewrite existing experience descriptions so that they communicate the candidate's most relevant, truthful capabilities for the target position.

Do not merely append JD keywords to existing bullets.

Do not retain every original bullet unchanged when its professional focus can be improved using existing evidence.

### A. Rewrite responsibilities in the target role's language

For every existing position:

1. Preserve the company name.
2. Preserve the official job title.
3. Preserve employment dates.
4. Preserve employment location where present.
5. Review all existing responsibilities and accomplishments.
6. Identify relevant technical work.
7. Identify transferable skills.
8. Rewrite descriptions using terminology relevant to the target role.
9. Prioritize the responsibilities that most closely match the JD.
10. Remove redundancy and irrelevant detail when appropriate.
11. Preserve factual meaning.
12. Ensure the rewritten description remains plausible for the candidate's actual position.

You may rewrite the description of a data-related responsibility to highlight software development, pipeline engineering, API integration, database querying, automation, or application integration if the original resume supports those activities.

However, you must not turn a data analyst's work into production backend engineering simply because the target job requires backend development.

### B. Use strong engineering action verbs

Choose accurate action verbs appropriate to the documented work, including:

- Designed.
- Developed.
- Implemented.
- Built.
- Engineered.
- Integrated.
- Optimized.
- Automated.
- Refactored.
- Tested.
- Deployed.
- Maintained.
- Debugged.
- Secured.
- Monitored.
- Analyzed.
- Improved.
- Migrated.
- Configured.
- Collaborated.
- Led, when leadership is supported.

Use verbs that reflect the candidate's actual responsibility. Do not use "architected," "led," "owned," or "managed" when the evidence only supports assisting or contributing.

### C. Use the achievement-oriented bullet formula

Where appropriate, structure each bullet as:

**Action + Technical Implementation + Purpose or Scope + Verified Result**

For example, a supported achievement might be expressed as:

"Developed Python-based data processing scripts to automate recurring reporting tasks, reducing manual effort and improving workflow consistency."

This example may be used only if the candidate actually performed the described work.

If the original resume documents a numerical improvement, preserve the number accurately and use it prominently when relevant.

If the original resume does not contain a measurable result, describe the implementation and its purpose without inventing a percentage, monetary saving, traffic volume, latency reduction, or other metric.

### D. Incorporate JD terminology naturally

When an existing responsibility aligns with the JD, express it using the relevant technical terminology.

For example:

Original evidence: The candidate developed a web application using Python, Flask, and PostgreSQL.

Target JD: Backend Engineer requiring REST APIs, relational databases, authentication, and testing.

A suitable rewritten bullet, if supported by the underlying project, could be:

"Developed Flask-based backend services with PostgreSQL integration, implementing REST API endpoints and input validation to support reliable application workflows."

If the original evidence does not establish REST API development or input validation, do not add those claims without further evidence.

### E. Prioritize the most relevant experience

Order bullets within each position according to their relevance to the target JD.

Generally, present the strongest matching technical responsibilities first, followed by complementary responsibilities and other relevant accomplishments.

Do not change the chronological order of employers unless explicitly requested.

Do not move a junior role above a senior role or change employment dates to create a more appealing narrative.

### F. Preserve truthful experience levels

Do not artificially elevate a candidate's career level.

If the JD is for a Senior Software Engineer, emphasize genuine technical ownership, complex problem-solving, system design, code quality, mentoring, and cross-functional collaboration when supported.

If the candidate's evidence demonstrates only junior-level responsibilities, present the strongest truthful engineering experience without pretending that senior-level experience exists.

The requested job title is a targeting signal, not proof of the candidate's seniority.

### G. Avoid keyword stuffing

Do not insert long strings of technologies into unrelated bullets.

Do not repeat the same skill in every bullet solely to increase ATS keyword density.

Every bullet should communicate a meaningful responsibility, implementation, contribution, or verified outcome.

---

## 8. PROJECT SECTION REWRITING

If the original resume includes projects, review and tailor each relevant project.

Preserve:

- Original project names.
- Verified project links.
- GitHub repositories.
- URLs.
- Project dates, if present.
- Existing ownership or collaboration facts.

Rewrite project descriptions to highlight the most relevant technical aspects for the JD.

For example, if the target role is Full Stack Engineer, emphasize supported frontend implementation, backend logic, database integration, authentication, API communication, testing, and deployment.

If the target role is Backend Engineer, emphasize supported server-side logic, data models, API design, performance, security, and reliability.

If the target role is Frontend Engineer, emphasize supported UI architecture, reusable components, state management, responsiveness, accessibility, and browser performance.

If the target role is DevOps Engineer, emphasize supported deployment automation, containerization, infrastructure, CI/CD, and monitoring.

Use only technologies and implementation details supported by the original resume or additional verified project information supplied by the user.

Do not invent projects, change the project's identity, fabricate GitHub repositories, or claim that a project was deployed to production when the source does not establish that fact.

You may substantially rewrite a project description while preserving the underlying project's actual purpose and implementation.

---

## 9. HANDLING DIFFERENT ORIGINAL RESUME TYPES

The original resume may be substantially different from the target role.

Do not assume that the original resume is already correctly structured for the target position.

### Scenario A: Data Science resume → Senior Software Engineer JD

Transform the professional presentation toward software engineering.

Where supported, prioritize:

- Software development.
- Application engineering.
- Programming.
- APIs.
- Databases.
- System design.
- Testing.
- Code quality.
- Performance.
- Deployment.
- Maintainability.

Reduce emphasis on unrelated statistical modeling, visualization, exploratory analysis, and research tools when they do not support the target position.

Retain relevant Python, SQL, data processing, automation, and backend development experience where applicable.

Do not invent large-scale production systems or architectural ownership.

### Scenario B: Data Science resume → Full Stack Engineer JD

Prioritize actual web application, frontend, backend, API, database, and integration experience.

Retain data engineering and automation capabilities where relevant.

Do not claim frontend framework experience without supporting evidence.

### Scenario C: Full Stack resume → Backend Engineer JD

Prioritize backend services, API development, databases, business logic, security, testing, and system performance.

De-emphasize purely visual or styling-related frontend details unless they are relevant to the JD.

### Scenario D: Backend resume → Frontend Engineer JD

Prioritize supported UI development, JavaScript or TypeScript, frontend frameworks, reusable components, API integration, accessibility, and browser performance.

Retain backend knowledge as a complementary strength.

Do not fabricate frontend projects or framework proficiency.

### Scenario E: Software Engineer resume → DevOps Engineer JD

Prioritize supported automation, CI/CD, cloud infrastructure, containerization, deployment, scripting, monitoring, and reliability work.

Do not claim production Kubernetes administration, infrastructure-as-code ownership, or cloud architecture unless supported.

### Scenario F: Any technical resume → Another technical role

Identify the target role's defining capabilities, then reorganize and rewrite the candidate's existing evidence around those capabilities.

The original resume's dominant technology category must not automatically determine the final resume's dominant technology category.

At the same time, the target JD must never override factual limitations in the candidate's background.

---

## 10. ATS OPTIMIZATION STRATEGY

Optimize the resume for both Applicant Tracking Systems and human recruiters.

### A. Keyword matching

Use important JD keywords naturally throughout relevant sections:

- Professional summary.
- Technical skills.
- Experience descriptions.
- Project descriptions.
- Other relevant professional sections.

Prioritize exact technology names, established abbreviations, and standard industry terminology.

When useful, include a common full term and its recognized abbreviation, such as "Continuous Integration and Continuous Delivery (CI/CD)," provided the candidate's experience supports the claim.

Do not add hidden keywords, invisible text, white text, keyword lists outside the normal resume structure, or unrelated keywords.

### B. Contextual keyword placement

Place each skill where it makes the most sense.

- Technical skills should identify supported competencies.
- Experience bullets should demonstrate how relevant skills were used.
- Projects should describe actual implementations.
- The summary should communicate the overall professional fit.

Avoid using the skills section as the only evidence for a candidate's claimed expertise.

### C. Readability

The resume must remain easy to scan.

Use concise bullet points, standard terminology, consistent tense, clear section headings, and meaningful technical detail.

Do not overuse abbreviations, buzzwords, or lengthy lists of technologies.

### D. No guaranteed ATS score

Do not claim that the resume will achieve a specific ATS score, pass every ATS filter, or guarantee an interview.

The goal is to improve relevance, clarity, keyword alignment, and recruiter readability.

---

## 11. EXPERIENCE LENGTH, BULLET COUNTS, AND DOCUMENT FIT

Respect the original resume's page structure and available space.

Do not create an unnecessarily long resume just to include every possible JD keyword.

As a general guide, unless the existing template dictates otherwise:

- Professional summary: approximately 50–100 words.
- Most relevant recent position: approximately 4–6 concise bullets when sufficient evidence exists.
- Other relevant positions: approximately 2–5 bullets when sufficient evidence exists.
- Project descriptions: approximately 1–4 bullets per relevant project.
- Technical skills: concise, categorized, and relevant.

These are flexible guidelines, not mandatory minimums.

Do not invent content to reach a target number of bullets.

Do not delete an important accomplishment merely to meet a word limit.

When the original resume is a one-page document, try to maintain one page if this can be achieved without sacrificing readability or materially weakening the candidate's presentation.

When the original resume is two pages, preserve its existing two-page structure when practical.

Do not reduce the font size, margins, or line spacing simply to force excessive text into the original page count.

If necessary, shorten repetitive wording and remove irrelevant details before making any formatting adjustments.

---

## 12. PROTECTED INFORMATION AND CONTROLLED EDITING

Treat the original resume as the authoritative source for protected facts.

The following content must not be altered by the tailoring process:

- Identity and contact information.
- Employer names.
- Official job titles.
- Employment dates.
- Educational institutions.
- Degree names.
- Education dates.
- Certifications and their actual issuing organizations.
- Project identities and links.
- Verified numerical accomplishments.
- Other biographical facts.

The following content may be rewritten when the changes remain truthful:

- Professional summary.
- Professional headline.
- Technical skills.
- Experience bullets.
- Project descriptions.
- Professional achievements.
- Descriptions of methodologies.
- Professional terminology.
- Relevant supporting statements.

### Special rule for job titles

Do not change an existing official job title to the target JD's title.

If the candidate's original position is "Data Analyst," preserve that title even when applying for "Senior Software Engineer."

You may rewrite the summary to emphasize supported software engineering capabilities.

If a professional headline exists separately from the employment history, it may be tailored to the target role when accurate and not misleading.

### Special rule for dates

Never adjust dates to make experience appear longer.

Never fill employment gaps with invented roles.

Never infer years of experience from unsupported assumptions.

### Special rule for metrics

Never fabricate:

- Percentage improvements.
- Cost savings.
- Revenue generated.
- Number of users.
- Request volumes.
- Latency reductions.
- Uptime.
- Team sizes.
- Project budgets.
- Performance benchmarks.
- Deployment frequencies.
- Test coverage percentages.

Use only documented figures or additional verified information explicitly provided to the application.

---

## 13. PROFESSIONAL WRITING QUALITY

Write like an experienced technical recruiter and a senior engineering resume writer.

The resume must be:

- Clear.
- Concise.
- Specific.
- Technically accurate.
- Professional.
- Consistent.
- Natural rather than robotic.
- Free of spelling and grammar errors.
- Appropriate to the target role.
- Easy for recruiters to scan.
- Focused on evidence and outcomes.

Avoid phrases such as:

- "Responsible for various tasks."
- "Worked on many different technologies."
- "Highly motivated individual seeking an opportunity."
- "Expert in everything."
- "Results-driven professional" without meaningful supporting detail.

Avoid repeating "Developed," "Implemented," or "Designed" at the beginning of every bullet when alternative accurate verbs improve readability.

Do not add exaggerated adjectives such as "world-class," "revolutionary," or "industry-leading" without evidence.

Do not write the resume in the first person unless the original template explicitly uses that style.

Do not introduce unsupported proficiency labels such as "Expert," "Advanced," or "Master" for technical skills.

---

## 14. FINAL OUTPUT REQUIREMENTS

The primary output must be the tailored resume, not an explanation of how you tailored it.

If the application provides an existing document, modify that document and return the tailored version in the original format whenever technically possible.

For DOCX input, return a DOCX file.

For PDF input, preserve the original PDF if reliable editing is supported. Otherwise, follow the application's defined editable reconstruction workflow.

Do not replace a formatted document with plain text unless the application explicitly requests plain text.

Do not include your internal reasoning, keyword analysis, or implementation notes inside the resume.

Do not append a separate cover letter, job application email, or interview guide unless specifically requested.

Do not add a "Target JD Keywords" section to the resume.

Do not include unsupported skills merely to achieve keyword coverage.

If the application supports additional outputs, it may optionally provide a separate tailoring report that includes:

- Target role identified.
- Important JD requirements.
- Supported keywords incorporated.
- Unsupported requirements not claimed.
- Protected information verification.
- Formatting preservation status.

This report must remain separate from the resume.

---

## 15. DOCUMENT VALIDATION BEFORE RETURNING THE RESUME

Before delivering the final file, perform a quality-control review.

### A. Identity validation

Verify that all protected personal details remain unchanged.

Compare the output with the original source for:

- Name.
- Email.
- Phone number.
- Address.
- URLs.
- School and university names.
- Degree names.
- Employer names.
- Official job titles.
- Employment dates.
- Education dates.

If any protected value has changed accidentally, restore it from the original document.

### B. Content validation

Verify that:

- The summary matches the target role.
- The skills section prioritizes supported JD requirements.
- Relevant experience bullets use appropriate technical terminology.
- Projects remain factually accurate.
- Irrelevant content has been reduced where appropriate.
- The resume reads as one coherent professional profile.
- No contradictory professional identities remain.
- No unsupported skills or accomplishments have been introduced.
- No fabricated metrics appear.
- No important factual details have been lost.

### C. Formatting validation

Verify that:

- Original section headings retain their styles.
- Font sizes and font families remain consistent.
- Bold and italic formatting is preserved where applicable.
- Tables and columns remain intact.
- Bullets and indentation are correct.
- Contact information remains in its original location.
- Hyperlinks still work.
- Page breaks are reasonable.
- No text is clipped or overlapping.
- No blank pages have been introduced.
- No content has been accidentally duplicated.
- The output document opens successfully.

If rendering is available, render the document and visually inspect every page before delivery.

### D. ATS and readability validation

Verify that:

- Important supported JD keywords appear naturally.
- Keywords are not stuffed into irrelevant bullets.
- Technical terminology is used accurately.
- The summary is concise.
- Experience bullets communicate actual contributions.
- The document remains readable.
- The resume is not unnecessarily longer than the original.

### E. Final integrity rule

If a requested transformation cannot be completed without fabricating experience or changing protected information, do not make that change.

Prioritize factual accuracy and document integrity over superficial keyword matching.

---

## 16. EXECUTION WORKFLOW

Execute the task in the following sequence:

**Step 1:** Read the original resume and identify all protected information and formatting structures.

**Step 2:** Read the complete JD and identify the target role, seniority, required skills, preferred skills, responsibilities, and important terminology.

**Step 3:** Extract the candidate's supported technical capabilities, accomplishments, projects, and transferable experience.

**Step 4:** Build an internal mapping between the candidate's evidence and the JD's requirements.

**Step 5:** Determine the target professional positioning and remove irrelevant emphasis where appropriate.

**Step 6:** Rewrite the professional summary and headline.

**Step 7:** Rebuild the technical skills section using supported, relevant skills and appropriate categories.

**Step 8:** Rewrite relevant professional experience descriptions and reorder bullets by relevance.

**Step 9:** Rewrite relevant project descriptions without changing project identities or fabricating implementation details.

**Step 10:** Update other professional sections only when doing so improves alignment and remains factually supported.

**Step 11:** Apply the rewritten content to the existing document while preserving formatting.

**Step 12:** Validate protected information, technical accuracy, ATS relevance, document integrity, and final visual appearance.

**Step 13:** Return the completed tailored resume in the requested output format.

Do not skip the analysis stage. Do not immediately start rewriting after reading only the first few lines of the JD.

---

## 17. FINAL INSTRUCTION

You are not a simple keyword replacement tool. You are a context-aware professional resume transformation engine.

For every new JD, independently identify the role's requirements and tailor the candidate's professional presentation accordingly.

If the same original resume is used for five different JDs—Senior Software Engineer, Full Stack Developer, Backend Engineer, Frontend Engineer, and DevOps Engineer—each output should have a meaningfully different professional summary, technical skills prioritization, experience emphasis, and project presentation, where the candidate's evidence supports those differences.

Do not use one generic resume for every role.

Do not preserve irrelevant technical skills merely because they appeared in the source resume.

Do not insert unsupported technical skills merely because they appeared in the JD.

Do not change personal identity, employer names, official job titles, educational institutions, degrees, or other protected factual details.

Preserve the original resume's formatting as faithfully as the document format and editing tools allow.

The final result must be a polished, target-specific, truthful, ATS-optimized resume that presents the candidate's actual experience in the strongest relevant way possible.

**Input:** Original resume + Target Job Description.

**Output:** A fully tailored, professionally rewritten resume that preserves protected facts and original document formatting while maximizing truthful alignment with the target role.

---

## 18. HOW THIS APPLICATION DELIVERS YOUR OUTPUT

This application handles the document side for you. It extracts the original resume as text, and after you respond it re-renders your content into the original resume's layout (section order, headings, contact block, bullets) as PDF/DOCX. Therefore:

- Do NOT attempt to edit DOCX runs or return a file. Return ONLY the JSON object described in the user message — that JSON is the "requested output format" for Sections 14 and 16.
- Formatting preservation (Rule 2) is achieved by mirroring the original resume's sections, section order, headings, entries, and bullet structure exactly in the JSON.
- Do not produce a separate tailoring report.
- Where the CRITICAL RULES in the user message are stricter than this prompt (for example, keeping the resume's "title" exactly as it appears), follow the CRITICAL RULES.
""".strip()


# ─────────────────────────────────────────────
# Resume section detection
# ─────────────────────────────────────────────

# Common resume section headings (case-insensitive matching)
_KNOWN_SECTIONS = [
    "summary", "professional summary", "profile", "objective",
    "skills", "technical skills", "core competencies", "technologies",
    "experience", "professional experience", "work experience", "employment",
    "career experience",
    "education", "academic background",
    "projects", "personal projects", "key projects",
    "certifications", "certificates", "licenses",
    "awards", "honors", "achievements",
    "publications", "research",
    "volunteer", "volunteer experience", "community involvement",
    "languages", "interests", "hobbies",
    "references",
]

def detect_resume_sections(resume_text: str) -> list[str]:
    """Detect section headings present in the resume text.

    Returns a list of section names in the order they appear.
    Skips the first few non-empty lines (name/contact area) to avoid false positives.
    """
    lines = resume_text.splitlines()
    found = []
    seen = set()

    # Skip the first few non-empty lines (typically name, title, contact info)
    non_empty_count = 0
    skip_until = 0
    for i, line in enumerate(lines):
        if line.strip():
            non_empty_count += 1
            if non_empty_count >= 4:
                skip_until = i
                break

    for line in lines[skip_until:]:
        stripped = line.strip().rstrip(":").strip()
        if not stripped or len(stripped) > 60:
            continue
        # Normalize: collapse multiple spaces, lowercase
        normalized = re.sub(r"\s+", " ", stripped).lower()

        # Match against known section headings only
        for known in _KNOWN_SECTIONS:
            if normalized == known:
                # Use the known canonical form for consistency
                canonical = known.title()
                if known not in seen:
                    seen.add(known)
                    found.append(canonical)
                break

    # Fallback: if nothing detected, return a standard set
    if not found:
        found = ["Summary", "Skills", "Experience", "Education"]

    # Check for implicit summary: if there's a long paragraph between
    # the header area and the first detected section, add "Summary" at the start
    if found and not any(s.lower() in ("summary", "professional summary", "profile", "objective") for s in found):
        first_section = found[0].lower()
        # Look for a long text block before the first section heading
        for line in lines[skip_until:]:
            stripped = line.strip()
            normalized = re.sub(r"\s+", " ", stripped).lower().rstrip(":")
            if normalized == first_section:
                break
            # If we find a line longer than 80 chars, it's likely a summary paragraph
            if len(stripped) > 80:
                found.insert(0, "Summary")
                break

    return found


def _classify_section(name: str) -> str:
    """Classify a section name into a rendering type."""
    lower = name.lower()
    if any(k in lower for k in ["summary", "profile", "objective"]):
        return "text"
    if any(k in lower for k in ["skill", "competenc", "technolog"]):
        return "skills"
    if any(k in lower for k in ["experience", "employment", "work", "career"]):
        return "experience"
    if any(k in lower for k in ["education", "academic"]):
        return "education"
    if any(k in lower for k in ["project"]):
        return "projects"
    if any(k in lower for k in ["certification", "certificate", "license"]):
        return "simple_list"
    if any(k in lower for k in ["award", "honor", "achievement"]):
        return "simple_list"
    if any(k in lower for k in ["publication", "research"]):
        return "simple_list"
    if any(k in lower for k in ["volunteer", "community"]):
        return "experience"
    if any(k in lower for k in ["language", "interest", "hobb"]):
        return "simple_list"
    return "simple_list"


# ─────────────────────────────────────────────
# AI API calls (Claude + Gemini)
# ─────────────────────────────────────────────

def _build_tailor_user_msg(resume_text: str, prompt_text: str, jd_text: str, detected_sections: list[str] | None = None) -> str:
    """Build the full prompt for resume tailoring (shared across providers)."""
    system_msg = prompt_text.strip()

    # Build dynamic section schema based on detected sections
    if not detected_sections:
        detected_sections = ["Summary", "Skills", "Experience", "Education"]

    section_examples = []
    for sec_name in detected_sections:
        sec_type = _classify_section(sec_name)
        if sec_type == "text":
            section_examples.append(
                f'    {{"type": "text", "heading": "{sec_name}", "content": "The full {sec_name.lower()} paragraph"}}'
            )
        elif sec_type == "skills":
            section_examples.append(
                f'    {{"type": "skills", "heading": "{sec_name}", "items": [{{"category": "Category Name", "items": "skill1, skill2, skill3"}}]}}'
            )
        elif sec_type == "experience":
            section_examples.append(
                f'    {{"type": "experience", "heading": "{sec_name}", "items": [{{"company": "Company Name", "location": "City, State/Country", "job_title": "Title", "dates": "MM/YYYY - MM/YYYY", "bullets": ["bullet 1", "bullet 2"]}}]}}'
            )
        elif sec_type == "education":
            section_examples.append(
                f'    {{"type": "education", "heading": "{sec_name}", "items": [{{"degree": "Degree Name", "school": "School Name", "dates": "MM/YYYY - MM/YYYY", "location": "City, ST"}}]}}'
            )
        elif sec_type == "projects":
            section_examples.append(
                f'    {{"type": "projects", "heading": "{sec_name}", "items": [{{"name": "Project Name", "description": "Brief description", "technologies": "tech1, tech2", "bullets": ["detail 1", "detail 2"]}}]}}'
            )
        else:  # simple_list
            section_examples.append(
                f'    {{"type": "simple_list", "heading": "{sec_name}", "items": ["Item 1", "Item 2"]}}'
            )

    sections_json = ",\n".join(section_examples)
    sections_list = ", ".join(detected_sections)

    rules_block = f"""CRITICAL RULES:
1. Do NOT change the candidate's TITLE or any PERSONAL DETAIL. Keep the name, email, phone,
   location, and links (LinkedIn / GitHub / portfolio) and the job title EXACTLY as they
   appear in the original resume — never reformat, translate, abbreviate, or invent any of them.
2. For EXPERIENCE sections, preserve the COMPANY LOCATION from the original resume for each job.
   Include the "location" field (e.g., "City, State" or "City, Country") exactly as it appears
   in the original resume for each company entry.
3. The output MUST have the SAME sections as the original resume, in the SAME order. The detected sections are: [{sections_list}].
4. Do NOT drop, merge, or add sections. Mirror the original resume's structure exactly.
5. TECHNOLOGY ALIGNMENT (high priority): Read EVERY technology, framework, language,
   library, platform and tool named in the JOB DESCRIPTION (e.g. React, Angular, Vue,
   TypeScript, Node.js, AWS, Kubernetes, PostgreSQL, etc.). For each one the candidate
   has genuine or directly-transferable experience with, surface the JD's EXACT term
   prominently — rewrite the SUMMARY and the relevant EXPERIENCE bullets so those
   technologies appear there using the JD's spelling. Prefer the JD's technology names
   over the resume's older/synonym terms when the candidate's experience supports it
   (e.g. resume "JS frameworks" -> JD "React"; "K8s" -> "Kubernetes"). Never invent a
   technology the candidate has no plausible experience with.
6. JOB IDENTITY: extract the hiring COMPANY name and the JOB TITLE from the JOB
   DESCRIPTION (not the resume). Put them in "detected_company" and "detected_job_title".
   If one is not stated, use an empty string for it.
7. TAILOR THE WHOLE RESUME — do NOT return it unchanged (this is the MOST IMPORTANT rule):
   - REWRITE the Summary AND every Experience bullet in every role so they foreground THIS job
     description's responsibilities, priorities, tools, and exact terminology, using only the
     candidate's real facts. Copying any bullet word-for-word from the original is a FAILURE.
   - Keep all the same roles/companies and a similar number of bullets, and never invent
     experience — but the WORDING must change. A tailored resume that reads the same as the
     original is WRONG. Tailoring only the Summary and leaving Experience unchanged is WRONG.
8. MARK YOUR CHANGES in EVERY section you rewrote — the Summary, Skills, AND (especially) every
   Experience bullet, not just the Summary. Wrap in [[ ]] (guillemets, U+00AB / U+00BB) every word
   or phrase you changed, reworded, or added. Example: "[[Architected]] scalable [[React and
   TypeScript]] services [[serving 10k+ users]]". If an Experience bullet has no [[ ]] marks, you did
   not tailor it — go back and rewrite it. Never mark the name, job title, or contact details.
   Use the exact characters [[ and ]], and always close every [[ with a matching ]].
9. TAILOR THE SKILLS SECTION (mandatory — follow Section 6.G of the system prompt): ADD EVERY
   technology, tool, platform, and practice named in the JD to the Skills section (this overrides
   rule 5's "never invent" for the Skills section only), written as short 1-4 word keywords (never
   JD sentences, never an "Other" catch-all), use the JD's exact spelling, put the most JD-relevant category first, and put JD-matching
   items first inside each category. Returning the Skills section unchanged is WRONG.
10. BACK UP EVERY SKILL (mandatory — follow Section 6.H of the system prompt): name the JD's core
   skills in the Summary, and make every JD skill you added to Skills appear in at least one
   Experience bullet, worked into the candidate's real work (extend existing bullets first; add at
   most two new bullets per role). This overrides rule 5's "never invent" for JD skills. Weave skills
   into each bullet's main action — every bullet is ONE newly written sentence (never the original
   sentence plus an added sentence), no "Experience with"/"Proficient in" filler, no first person in
   the Summary — never append JD phrases to the end of a bullet with a comma, never
   paste JD sentences, and never repeat a JD phrase in more than two bullets. Leadership, mentoring,
   hiring, and onboarding claims go only in senior roles, never intern/junior roles. The Summary must
   not mention the hiring company or its industry. Never put a technology in a role dated before it
   existed, and never invent metrics, titles, dates, or employers."""

    user_msg = f"""Here are the two inputs:

=== CANDIDATE RESUME ===
{resume_text}

=== JOB DESCRIPTION ===
{jd_text}

=== INSTRUCTIONS ===
Apply every phase from your system prompt. Your #1 job is to REWRITE THE EXPERIENCE SECTION: rewrite EVERY bullet in EVERY role to match this specific job description — its responsibilities, priorities, and exact terminology — while keeping all of the candidate's real facts and every bullet. Do NOT copy experience bullets verbatim, and do NOT tailor only the Summary. The experience bullets must read as written for THIS job.

{rules_block}

IMPORTANT: Return ONLY the tailored resume as a JSON object with this exact structure (no change log, no interview prep — just the resume):

```json
{{
  "name": "Candidate Name",
  "title": "EXACT title from original resume — do NOT change this",
  "contact": "Location | email",
  "detected_company": "Hiring company from the JOB DESCRIPTION, or empty string",
  "detected_job_title": "Job title from the JOB DESCRIPTION, or empty string",
  "sections": [
{sections_json}
  ]
}}
```

Return ONLY the JSON object, no markdown code fences, no extra text. Just pure JSON.
"""
    return f"{system_msg}\n\n{user_msg}"


def _strip_code_fences(raw: str) -> str:
    """Strip markdown code fences from AI response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw


def _repair_json(raw: str) -> str:
    """Best-effort JSON repair for common LLM output issues.

    Fixes:
    - Literal newlines / tabs / carriage returns inside string values
      (Gemini json-mode sometimes emits unescaped these in long bullets).
    - Trailing commas before } or ].
    - Unterminated trailing string (closes with a single `"` then balances brackets).
    """
    # Walk the string with a tiny state machine, escaping control chars inside strings.
    out = []
    in_string = False
    escape = False
    for ch in raw:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string:
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
        out.append(ch)
    repaired = "".join(out)

    # Strip trailing commas before } or ]
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)

    # Try parsing as-is; if it still has an unterminated string at the end,
    # close it and balance brackets.
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass

    # If we ended in the middle of a string, close it.
    if in_string:
        repaired += '"'
    # Balance brackets/braces by counting and appending what's missing.
    open_curly = repaired.count("{") - repaired.count("}")
    open_square = repaired.count("[") - repaired.count("]")
    # Trim a trailing comma that may now sit right before the synthetic closer
    repaired = re.sub(r",\s*$", "", repaired)
    repaired += "]" * max(0, open_square)
    repaired += "}" * max(0, open_curly)
    return repaired


def _safe_json_loads(raw: str):
    """Try json.loads; on failure, run the lightweight repair; if that still fails,
    fall back to the heavy-duty `json-repair` library which handles unquoted keys,
    single-quoted strings, missing commas, Python-style values, and other LLM quirks."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_json(raw))
    except json.JSONDecodeError:
        pass
    # Last resort: json-repair. It returns either a parsed object or the repaired string.
    from json_repair import repair_json
    repaired = repair_json(raw, return_objects=True)
    if repaired in ("", None):
        # Library returns "" when it gives up.
        raise json.JSONDecodeError("json-repair could not parse the response.", raw, 0)
    return repaired


# ─────────────────────────────────────────────
# AI Provider registry (multi-provider support)
# ─────────────────────────────────────────────

# Default model per provider. Frontend can override via `model` form field.
# These default to STRONGER models — weaker/cheaper ones (gpt-4o-mini, flash-lite) tend
# to copy the resume instead of tailoring it.
PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "gemini":    "gemini-2.5-flash",
    "openai":    "gpt-4o",
    "openrouter": "openrouter/auto",
    "groq":      "llama-3.3-70b-versatile",
    "together":  "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "openai_compatible": "",   # user must supply
    "ollama":    "llama3.1",
}

# OpenAI-compatible providers — share the same code path, just differ in base_url.
OPENAI_COMPATIBLE_BASE_URLS = {
    "openai":      "https://api.openai.com/v1",
    "openrouter":  "https://openrouter.ai/api/v1",
    "groq":        "https://api.groq.com/openai/v1",
    "together":    "https://api.together.xyz/v1",
    "ollama":      "http://localhost:11434/v1",   # Ollama's OpenAI shim
    # "openai_compatible" — base_url comes from the request
}


def _split_system_user(prompt: str) -> tuple[str, str]:
    """The legacy prompt builder concatenates `system\n\nuser`. Split it back."""
    # The tailoring system prompt itself contains blank lines, so split at the
    # start of the user message rather than at the first blank line.
    marker = "\n\nHere are the two inputs:"
    if marker in prompt:
        sys_part, user_part = prompt.split(marker, 1)
        return sys_part, "Here are the two inputs:" + user_part
    if "\n\n" in prompt:
        sys_part, user_part = prompt.split("\n\n", 1)
        return sys_part, user_part
    return "", prompt


def _call_anthropic(api_key: str, model: str, prompt: str, max_tokens: int, json_mode: bool, base_url: str | None, temperature: float = 0) -> str:
    import anthropic
    # max_retries=1: the SDK gets one quick 429/5xx retry, then call_ai's own loop
    # takes over with the longer, Retry-After-aware backoff. Leaving the SDK default
    # (2) on top of our 4 attempts would blow past the serverless request timeout.
    client_kwargs = {"api_key": api_key, "max_retries": 1, "timeout": 120.0}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)
    system_text, user_text = _split_system_user(prompt)
    # Cache the (large) system prompt so repeat tailorings cost ~10% of normal.
    system_blocks = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}] if system_text else []
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_text}],
    }
    if system_blocks:
        kwargs["system"] = system_blocks
    message = client.messages.create(**kwargs)
    return message.content[0].text.strip()


def _call_gemini(api_key: str, model: str, prompt: str, max_tokens: int, json_mode: bool, base_url: str | None, temperature: float = 0) -> str:
    from google import genai
    from google.genai import types
    # base_url was previously accepted and silently ignored here, so a custom Gemini
    # endpoint quietly went to Google's default host.
    http_options = types.HttpOptions(timeout=120_000)  # milliseconds
    if base_url:
        http_options.base_url = base_url
    client = genai.Client(api_key=api_key, http_options=http_options)
    config_kwargs = {"max_output_tokens": max_tokens, "temperature": temperature}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return (response.text or "").strip()


def _call_openai_compatible(api_key: str, model: str, prompt: str, max_tokens: int, json_mode: bool, base_url: str, temperature: float = 0) -> str:
    """Works for OpenAI, OpenRouter, Groq, Together, Ollama, LM Studio, any OpenAI-compatible endpoint."""
    from openai import OpenAI
    # Ollama's OpenAI shim accepts any string as api_key; pass a placeholder if missing.
    # max_retries=1 / timeout: same reasoning as _call_anthropic — the SDK default of
    # 2 retries stacked under call_ai's 4 attempts is 12 HTTP calls, and the default
    # 600s timeout would hang well past any serverless request limit.
    client = OpenAI(api_key=api_key or "ollama-local", base_url=base_url,
                    max_retries=1, timeout=120.0)
    system_text, user_text = _split_system_user(prompt)
    messages = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_text})
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # OpenAI reasoning models (gpt-5*, o1/o3/o4*) reject max_tokens/temperature. Reasoning
    # tokens also count against the output budget, so give them headroom and keep effort
    # "low" — enough to hit the ATS rules without burning tokens.
    if _is_openai_reasoning_model(model, base_url):
        kwargs.pop("max_tokens")
        kwargs.pop("temperature")
        kwargs["max_completion_tokens"] = max_tokens + 8000
        kwargs["reasoning_effort"] = "low"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # Newer models keep changing which params they accept; adapt once per rejected param
    # instead of failing the whole tailoring.
    for _ in range(3):
        try:
            completion = client.chat.completions.create(**kwargs)
            break
        except Exception as e:
            msg = str(e).lower()
            if "unsupported" not in msg and "not supported" not in msg:
                raise
            if "max_tokens" in msg and "max_tokens" in kwargs:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            elif "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature")
            elif "reasoning_effort" in msg and "reasoning_effort" in kwargs:
                kwargs.pop("reasoning_effort")
            else:
                raise
    else:
        completion = client.chat.completions.create(**kwargs)
    return (completion.choices[0].message.content or "").strip()


def _is_openai_reasoning_model(model: str, base_url: str) -> bool:
    if "api.openai.com" not in (base_url or ""):
        return False
    m = (model or "").lower().split("/")[-1]
    if "chat" in m:   # gpt-5-chat-latest is a plain chat model
        return False
    return m.startswith("gpt-5") or bool(re.match(r"o\d", m))


_AI_UNICODE_REPLACEMENTS = {
    # Smart quotes
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
    # Dashes / hyphens
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-",
    # Spaces
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    # Bullets / ellipsis
    "•": "-", "‣": "-", "◦": "-", "⁃": "-",
    "…": "...",
    # Box-drawing characters used in our prompt template (decorative only)
    "─": "-", "━": "-", "│": "|", "┃": "|",
    "═": "=", "║": "=",
}
_AI_TRANSLATE_TABLE = str.maketrans(_AI_UNICODE_REPLACEMENTS)


def _ascii_strip(s) -> str:
    """For values that must travel in HTTP headers / query params (api_key, model,
    base_url): drop everything that's not ASCII printable. Catches invisible smart
    quotes, zero-width chars, BOMs, and other paste-artifacts that break httpx."""
    if not s:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return "".join(c for c in s if 0x20 <= ord(c) < 0x7F).strip()


def _ai_safe(text: str) -> str:
    """Sanitize text headed for an AI provider's HTTP body.

    Some SDK internals (and HTTP transports for headers) assume ASCII somewhere.
    The body itself is UTF-8, but we've seen 'ascii' codec errors with smart quotes
    on certain Windows + httpx combos. Better to scrub before sending — the AI does
    not need fancy typography.

    Strategy: explicit replacements for common typography, then NFKD-normalize and
    drop any remaining combining marks that don't fold to ASCII. Accented letters
    like é/ñ/ü become their ASCII bases.
    """
    if not isinstance(text, str):
        text = str(text or "")
    text = text.translate(_AI_TRANSLATE_TABLE)
    # Normalize remaining unicode → ASCII where possible
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


import time as _time

# Wall-clock budget for all retry sleeps inside one call_ai(). Serverless platforms
# kill the request at ~60s, so never start a backoff we can't finish there.
_RETRY_BUDGET_S = 40.0 if os.environ.get("VERCEL") else 90.0


def _status_code(exc: Exception) -> int | None:
    """The HTTP status a provider SDK attached to the error, if any.

    Substring-matching "401"/"429" against the whole error text misfires on request
    ids and model names that happen to contain those digits, so use the real status
    when the SDK gives us one.
    """
    for attr in ("status_code", "code", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and 100 <= val <= 599:
            return val
    resp = getattr(exc, "response", None)
    val = getattr(resp, "status_code", None)
    if isinstance(val, int) and 100 <= val <= 599:
        return val
    return None


def _is_daily_quota_error(exc: Exception) -> bool:
    """Hard quota exhaustion (Gemini free-tier daily cap, OpenAI billing limit).

    Distinct from a per-minute rate limit: the window won't reopen in seconds, so
    retrying is pointless.
    """
    err_lower = str(exc).lower()
    return ("resource_exhausted" in err_lower
            or "quota exceeded" in err_lower
            or "exceeded your current quota" in err_lower
            or "insufficient_quota" in err_lower
            # OpenAI returns these as 429s even though no amount of waiting helps.
            or "no credits remaining" in err_lower
            or "credit_balance_exhausted" in err_lower
            or "billing_hard_limit_reached" in err_lower
            # Anthropic's equivalent.
            or "credit balance is too low" in err_lower
            # OpenRouter / Together.
            or "insufficient credits" in err_lower
            or "not enough credits" in err_lower)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Per-minute rate limit (429 / RateLimitError) — retryable after a cooldown."""
    if _is_daily_quota_error(exc):
        return False
    err_str = str(exc)
    err_lower = err_str.lower()
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    if _status_code(exc) == 429:
        return True
    if "429" in err_str or "rate_limit" in err_lower or "rate limit" in err_lower:
        return True
    # Anthropic/OpenAI shape their per-minute caps as tokens-per-minute messages.
    if "too many requests" in err_lower or "tokens per minute" in err_lower or "requests per minute" in err_lower:
        return True
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    """Pull the provider's own Retry-After hint off the failed HTTP response."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = None
        try:
            raw = headers.get(key)
        except Exception:
            pass
        if not raw:
            continue
        m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*(ms|s|m)?\s*$", str(raw))
        if not m:
            continue
        val = float(m.group(1))
        unit = m.group(2)
        if unit == "ms":
            val /= 1000.0
        elif unit == "m":
            val *= 60.0
        # Clamp: a provider asking for 10 minutes is not something we sit and wait on.
        return max(1.0, min(val, 60.0))
    return None


def _is_transient_provider_error(exc: Exception) -> bool:
    """Heuristic: is this error worth retrying?

    Provider 503 / "overloaded" / "high demand" / explicit ServerError → yes.
    Auth, invalid arg, content filtering, daily quota exhausted → no (retrying
    can't fix these). Per-minute rate limits are handled by _is_rate_limit_error,
    which gets a longer backoff schedule.
    """
    err_str = str(exc)
    err_lower = err_str.lower()
    name = type(exc).__name__.lower()
    if _is_daily_quota_error(exc):
        return False
    if "servererror" in name:
        return True
    if _status_code(exc) in (500, 502, 503, 504):
        return True
    if "503" in err_str or "502" in err_str or "504" in err_str:
        return True
    if "unavailable" in err_lower or "overloaded" in err_lower or "high demand" in err_lower:
        return True
    return False


def call_ai(
    provider: str,
    api_key: str,
    prompt: str,
    max_tokens: int = 16000,
    model: str | None = None,
    base_url: str | None = None,
    json_mode: bool = True,
    temperature: float = 0,
) -> str:
    """Route to the correct AI provider and return raw text.

    Auto-retries up to 3 times on transient provider errors (503 / overloaded /
    high demand) with exponential backoff (1.5s, 4s, 9s). Non-retryable errors
    (auth, invalid request) bubble up immediately.
    """
    provider = (provider or "anthropic").lower()
    # Backward-compat: old frontend sent "claude"
    if provider == "claude":
        provider = "anthropic"
    chosen_model = _ascii_strip(model) or PROVIDER_DEFAULTS.get(provider, "")
    # Defensively scrub everything that touches an HTTP header, query param, or body.
    # Prevents 'ascii' codec errors when users paste API keys / model names that have
    # invisible smart quotes, zero-width chars, or non-breaking spaces.
    api_key = _ascii_strip(api_key)
    base_url = _ascii_strip(base_url) if base_url else None
    prompt = _ai_safe(prompt)

    def _dispatch():
        if provider == "anthropic":
            return _call_anthropic(api_key, chosen_model, prompt, max_tokens, json_mode, base_url, temperature)
        if provider == "gemini":
            return _call_gemini(api_key, chosen_model, prompt, max_tokens, json_mode, base_url, temperature)
        if provider in OPENAI_COMPATIBLE_BASE_URLS:
            url = base_url or OPENAI_COMPATIBLE_BASE_URLS[provider]
            return _call_openai_compatible(api_key, chosen_model, prompt, max_tokens, json_mode, url, temperature)
        if provider == "openai_compatible":
            if not base_url:
                raise ValueError("openai_compatible provider requires a base_url.")
            if not chosen_model:
                raise ValueError("openai_compatible provider requires a model name.")
            return _call_openai_compatible(api_key, chosen_model, prompt, max_tokens, json_mode, base_url, temperature)
        raise ValueError(f"Unknown AI provider: {provider}")

    # Retry loop for transient provider errors (503 / "high demand") AND for
    # per-minute rate limits (429), which are the common failure when one tailor
    # fires several 16k-token calls back to back.
    overload_backoffs = (1.5, 4.0, 9.0)    # 5xx clears fast
    rate_limit_backoffs = (8.0, 20.0, 30.0)  # per-minute windows need a real cooldown
    deadline = _time.monotonic() + _RETRY_BUDGET_S
    attempts = 4
    last_exc = None
    for attempt in range(attempts):
        try:
            return _dispatch()
        except Exception as e:
            last_exc = e
            rate_limited = _is_rate_limit_error(e)
            if not (rate_limited or _is_transient_provider_error(e)):
                raise
            if attempt == attempts - 1:
                break
            wait = _retry_after_seconds(e)
            if wait is None:
                wait = (rate_limit_backoffs if rate_limited else overload_backoffs)[attempt]
            # Don't start a sleep we can't finish before the platform kills the request.
            remaining = deadline - _time.monotonic()
            if wait >= remaining:
                print(f"[call_ai] {'rate limited' if rate_limited else 'transient error'}; "
                      f"retry budget exhausted ({remaining:.1f}s left, needs {wait:.1f}s) — giving up")
                break
            print(f"[call_ai] {'rate limited (429)' if rate_limited else 'transient error'}, "
                  f"retrying in {wait:.1f}s (attempt {attempt + 2}/{attempts})")
            _time.sleep(wait)
    # Exhausted retries — surface the last error.
    raise last_exc


def _provider_error_response(exc: Exception):
    """Map any provider's auth / rate-limit / generic error to a clean HTTP response."""
    # Always print the full traceback to the server log — silent 500s with one-line
    # messages make these very hard to debug.
    traceback.print_exc()
    err_str = str(exc)
    err_lower = err_str.lower()
    name = type(exc).__name__.lower()
    status = _status_code(exc)
    # Auth-ish. Covers Anthropic/OpenAI AuthenticationError, Gemini's API_KEY_INVALID
    # (which arrives as a 400), Groq/Together/OpenRouter invalid_api_key.
    if ("authentication" in name or "permissiondenied" in name
            or "API_KEY_INVALID" in err_str or "Incorrect API key" in err_str
            or "invalid_api_key" in err_lower or "invalid x-api-key" in err_lower
            or "api key not valid" in err_lower or "no auth credentials" in err_lower
            or status == 401 or (status is None and "401" in err_str)):
        return jsonify({"error": "Invalid API key. Please check the key for the selected provider."}), 401
    # Out of credits / billing limit hit. Providers send this as a 429, but it is a
    # billing problem, not a rate limit — waiting will never clear it.
    if ("no credits remaining" in err_lower or "credit_balance_exhausted" in err_lower
            or "insufficient_quota" in err_lower or "billing_hard_limit_reached" in err_lower
            or "credit balance is too low" in err_lower
            # OpenRouter / Together phrasing, sent as HTTP 402.
            or "insufficient credits" in err_lower or "not enough credits" in err_lower
            or status == 402):
        return jsonify({"error": "This provider account is out of credits (the provider returns this "
                        "as a 429, but waiting won't help). Add credits to the billing account for "
                        "this API key, or switch Provider in the dropdown to a key that has balance."}), 402
    # Daily quota exhausted (Gemini free tier, etc.) — distinct from per-minute rate
    # limiting because the user can't just "wait a moment".
    if _is_daily_quota_error(exc):
        # Try to surface model + tip
        msg = ("Daily quota exhausted for this model. Options: "
               "(1) switch the Model to a higher-quota one (e.g. 'gemini-2.5-flash-lite' has 1,500/day on Gemini free tier vs. 20/day for 'gemini-2.5-flash'), "
               "(2) switch Provider to Anthropic / OpenAI in the dropdown, "
               "(3) enable Fast mode to use ~6× fewer calls per tailor.")
        return jsonify({"error": msg}), 429
    # Per-minute rate-limit-ish
    if _is_rate_limit_error(exc):
        return jsonify({"error": "The AI provider rate-limited this request (429), and it was still "
                        "limited after several automatic retries. Wait about a minute and try again. "
                        "If it keeps happening, pick a smaller/faster model or switch Provider "
                        "in the dropdown."}), 429
    # Provider overload / temporarily unavailable (Gemini's "model experiencing high demand", OpenAI 503, etc.)
    if ("unavailable" in err_lower or "overloaded" in err_lower or "high demand" in err_lower
            or "servererror" in name or (status in (500, 502, 503, 504))
            or (status is None and "503" in err_str)):
        return jsonify({"error": "The AI provider is overloaded right now. Wait 30 seconds and retry, or pick a different model/provider in the dropdown."}), 503
    # Invalid / unknown model ID — the Model field doesn't match the chosen provider.
    if ("invalid model" in err_lower or "model not found" in err_lower or "model_not_found" in err_lower
            or "unknown model" in err_lower
            or ("model" in err_lower and ("does not exist" in err_lower or "not exist" in err_lower))):
        return jsonify({"error": "Invalid model ID for the selected provider. Check the Model field "
                        "(or clear it to use the provider's default) and make sure the model name "
                        "matches the Provider you picked in the dropdown."}), 400
    # Our own config guards (missing base_url / model for openai_compatible, unknown
    # provider). These are user input problems, not server faults.
    if isinstance(exc, ValueError) and ("provider requires" in err_str or "Unknown AI provider" in err_str):
        return jsonify({"error": err_str}), 400
    # Connection-ish (Ollama not running, bad base_url, etc.)
    if "connection" in name or "Connection" in err_str or "ECONNREFUSED" in err_str:
        return jsonify({"error": "Could not reach the AI provider. Check the base URL or that the local server is running."}), 502
    return jsonify({"error": f"Something went wrong: {err_str}"}), 500


def _validate_resume_shape(data: dict) -> None:
    """Sanity-check AI response before downstream code indexes into it."""
    if not isinstance(data, dict):
        raise ValueError("AI did not return a JSON object.")
    if "sections" not in data and "experience" not in data and "summary" not in data:
        # Truncated output is the usual cause — surface a clear hint.
        raise ValueError("AI response is missing the resume body. The output may have been truncated; try again.")


def _section_headings(resume: dict) -> list[str]:
    """Lower-cased ordered list of section headings present in a resume JSON."""
    if not isinstance(resume, dict):
        return []
    return [
        (s.get("heading", "") or "").strip().lower()
        for s in (resume.get("sections") or [])
        if isinstance(s, dict)
    ]


def _has_all_sections(after: dict, before: dict) -> bool:
    """True if `after` retains every section heading present in `before`."""
    before_set = set(h for h in _section_headings(before) if h)
    after_set = set(h for h in _section_headings(after) if h)
    return before_set.issubset(after_set)


def _section_count(resume: dict) -> int:
    """Total non-empty sections in a resume."""
    secs = resume.get("sections") if isinstance(resume, dict) else None
    return len(secs) if isinstance(secs, list) else 0


_BULLET_MARKERS = ("-", "*", "•", "▪", "◦", "‣", "·", "–", "—", "»", "→")

# Programming languages — used to detect a bogus "Languages" section (which should list
# SPOKEN languages, not the coding languages already in the Skills section).
_PROGRAMMING_LANGS = {
    "c#", "c++", "c", "javascript", "typescript", "python", "sql", "java", "go", "golang",
    "rust", "ruby", "php", "kotlin", "swift", "scala", "r", "perl", "objective-c", "dart",
    "html", "css", "html/css", ".net", "node.js", "node", "bash", "shell", "powershell",
    "matlab", "assembly", "vb.net", "visual basic", "groovy", "elixir", "haskell", "lua",
    "t-sql", "pl/sql", "json", "xml", "yaml",
}


def _strip_bogus_language_section(resume: dict) -> None:
    """Remove a standalone 'Languages' section that was wrongly filled with PROGRAMMING
    languages (duplicating the Skills section). A real Languages section lists spoken
    languages like English/Spanish, which we keep."""
    secs = resume.get("sections") if isinstance(resume, dict) else None
    if not isinstance(secs, list):
        return
    kept = []
    for s in secs:
        if (isinstance(s, dict)
                and (s.get("heading", "") or "").strip().lower() == "languages"
                and s.get("type") in ("simple_list", "text", "skills")):
            def _split(val):
                return [p.strip().lower() for p in re.split(r"[,\n;/|]| and ", val or "") if p.strip()]
            parts = []
            items = s.get("items")
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, str):
                        parts.append(it.strip().lower())          # each list item is one language
                    elif isinstance(it, dict):
                        parts.extend(_split(it.get("items") or it.get("name") or it.get("category") or ""))
            elif isinstance(s.get("content"), str):
                parts.extend(_split(s["content"]))
            parts = [p for p in parts if p]
            if parts:
                prog = sum(1 for p in parts if p in _PROGRAMMING_LANGS)
                if prog >= max(1, (len(parts) + 1) // 2):
                    print(f"[lang-guard] dropped bogus programming-language 'Languages' section: {parts}")
                    continue
        kept.append(s)
    resume["sections"] = kept


def _count_result_bullets(resume: dict) -> int:
    """Count bullet points across experience/projects entries in a tailored resume JSON."""
    total = 0
    for s in (resume.get("sections") or []):
        if not isinstance(s, dict):
            continue
        items = s.get("items")
        if isinstance(items, dict):
            items = [items]
        for it in (items or []):
            if isinstance(it, dict):
                total += len(it.get("bullets") or [])
    return total


def _count_source_bullets(resume_text: str) -> int:
    """Heuristic count of bullet lines in the ORIGINAL resume text.

    Only counts lines that clearly begin with a bullet marker, so it stays conservative
    (docx list formatting often strips the marker → we simply won't trigger the guard).
    """
    n = 0
    for line in (resume_text or "").splitlines():
        st = line.strip()
        if len(st) > 3 and st[0] in _BULLET_MARKERS:
            n += 1
    return n


def _experience_mostly_verbatim(resume_json: dict, original_text: str) -> bool:
    """True if most EXPERIENCE bullets in the tailored resume appear verbatim in the
    original resume text — i.e. the model copied them instead of tailoring (a common
    failure where only the Summary gets rewritten)."""
    orig = re.sub(r"\s+", " ", original_text or "").lower()
    if not orig:
        return False
    bullets = []
    for s in (resume_json.get("sections") or []):
        if not isinstance(s, dict):
            continue
        heading = (s.get("heading", "") or "").lower()
        if s.get("type") == "experience" or "experience" in heading or "employment" in heading:
            items = s.get("items")
            if isinstance(items, dict):
                items = [items]
            for it in (items or []):
                if isinstance(it, dict):
                    bullets += [b for b in (it.get("bullets") or []) if isinstance(b, str)]
    long_bullets = [b for b in bullets if len(b.strip()) >= 30]
    if len(long_bullets) < 4:
        return False
    verbatim = 0
    for b in long_bullets:
        bn = re.sub(r"\s+", " ", b.replace("[[", "").replace("]]", "")).strip().lower()
        if bn and bn in orig:
            verbatim += 1
    return verbatim >= len(long_bullets) * 0.6


def _skills_unchanged(resume_json: dict, original_text: str) -> bool:
    """True if the tailored Skills section adds (almost) nothing that isn't already in the
    original resume text — i.e. the model left Skills as-is instead of tailoring it to the JD."""
    orig = re.sub(r"\s+", " ", original_text or "").lower()
    if not orig:
        return False
    skills = []
    for s in (resume_json.get("sections") or []):
        if not isinstance(s, dict) or s.get("type") != "skills":
            continue
        for it in (s.get("items") or []):
            if isinstance(it, dict):
                skills += str(it.get("items", "")).split(",")
                skills.append(str(it.get("category", "")))
            elif isinstance(it, str):
                skills += it.split(",")
    skills = [re.sub(r"\s+", " ", k.replace("[[", "").replace("]]", "")).strip().lower() for k in skills]
    skills = [k for k in skills if k]
    if len(skills) < 5:
        return False
    new = [k for k in skills if k not in orig]
    return len(new) < 2


# Filler the model uses to bolt JD requirements onto a bullet instead of rewriting it.
_FILLER_RE = re.compile(
    r"\b(experience with|experienced with|proficient in|proficiency (in|with)|familiarity with|"
    r"familiar with|strong [a-z ]*skills|adept at|skilled in|knowledge of)\b", re.I)
_FIRST_PERSON_RE = re.compile(r"\b(I|I'm|I am|my|me)\b")


_JUNIOR_TITLE_RE = re.compile(r"\b(intern|internship|junior|jr\.?|trainee|apprentice|co-?op)\b", re.I)
_SENIOR_CLAIM_RE = re.compile(
    r"\b(mentor\w*|coach\w*|onboard\w* new engineers|hiring process|constructive feedback|"
    r"junior team members|technical leadership)\b", re.I)


def _verbatim_prefix_words(bullet_words: list[str], orig: str) -> int:
    """Length (in words) of the longest prefix of the bullet that appears verbatim in the
    original text. Prefix containment is monotonic, so binary search is exact."""
    lo, hi = 0, len(bullet_words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if " ".join(bullet_words[:mid]) in orig:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _bullet_quality_issues(resume_json: dict, original_text: str) -> list[str]:
    """Problems the model tends to produce when it pads instead of tailoring:
    bullets that are the original sentence plus tacked-on words (however they are attached),
    resume filler ("Experience with ..."), leadership claims in intern/junior roles, and a
    Summary that is first-person or names the hiring company. Each issue is a short line the
    retry prompt quotes back to the model."""
    orig = re.sub(r"[^a-z0-9+#/ ]", " ", (original_text or "").lower())
    orig = re.sub(r"\s+", " ", orig)
    company = (resume_json.get("detected_company") or "").strip()
    issues = []
    for s in (resume_json.get("sections") or []):
        if not isinstance(s, dict):
            continue
        if s.get("type") == "text":
            content = str(s.get("content", "")).replace("[[", "").replace("]]", "")
            heading = s.get("heading", "Summary")
            if _FIRST_PERSON_RE.search(content):
                issues.append(f'{heading} is written in the first person ("I", "my") — use resume style with no pronouns.')
            if (company and len(company) > 2 and company.lower() in content.lower()) or re.search(r"\bseeking\b", content, re.I):
                issues.append(f'{heading} names the hiring company or is an objective ("Seeking...") — describe the candidate only.')
            continue
        if s.get("type") not in ("experience", "projects"):
            continue
        for it in (s.get("items") or []):
            if not isinstance(it, dict):
                continue
            junior = bool(_JUNIOR_TITLE_RE.search(str(it.get("job_title", ""))))
            for b in (it.get("bullets") or []):
                if not isinstance(b, str):
                    continue
                clean = re.sub(r"\s+", " ", b.replace("[[", "").replace("]]", "")).strip()
                words = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#/ ]", " ", clean.lower())).split()
                prefix = _verbatim_prefix_words(words, orig)
                if 12 <= prefix < len(words) and prefix >= len(words) * 0.6:
                    issues.append(f'Original bullet with words tacked on the end — rewrite the WHOLE bullet as one new sentence: "{clean}"')
                elif _FILLER_RE.search(clean):
                    issues.append(f'Filler phrase ("Experience with...", "Proficient in...") — show the work instead: "{clean}"')
                if junior and _SENIOR_CLAIM_RE.search(clean):
                    issues.append(f'Leadership/mentoring/hiring claim in a junior or intern role — remove it: "{clean}"')
    return issues


# JD "skills" that are really responsibilities or soft skills; dropped from the Skills section.
_NON_SKILL_ITEMS = {
    "hiring process", "hiring", "onboarding", "onboarding new engineers", "coaching",
    "constructive feedback", "user needs", "knowledge sharing", "performance bottlenecks",
    "experiments", "experimentation", "prototypes", "continuous improvement",
    "strong communication", "communication skills", "collaboration skills", "problem solving",
}

# Named technologies to look for in the JD. Each entry: canonical name -> extra spellings.
_KNOWN_TECH = {
    "HTML": ["html5"], "CSS": ["css3"], "JavaScript": [], "TypeScript": [], "React": ["react.js", "reactjs"],
    "React Native": [], "Next.js": ["nextjs", "next js"], "Vue.js": ["vue", "vuejs"], "Angular": [],
    "Svelte": [], "Redux": [], "Tailwind CSS": ["tailwind"], "Node.js": ["nodejs", "node js"],
    "Express.js": ["expressjs"], "NestJS": [], "Python": [], "Django": [], "Flask": [],
    "FastAPI": [], "Java": [], "Spring Boot": [], "Kotlin": [], "Golang": [], "Ruby": [],
    "Ruby on Rails": [], "PHP": [], "Laravel": [], "C#": [], ".NET": ["dotnet"], "C++": [],
    "Scala": [], "GraphQL": [], "gRPC": [], "RESTful APIs": ["rest api", "rest apis"], "Kafka": [], "RabbitMQ": [],
    "Redis": [], "PostgreSQL": ["postgres"], "MySQL": [], "MongoDB": [], "DynamoDB": [],
    "Elasticsearch": [], "Cassandra": [], "Snowflake": [], "AWS": ["amazon web services"],
    "GCP": ["google cloud"], "Azure": [], "Kubernetes": ["k8s"], "Docker": [], "Terraform": [],
    "Helm": [], "Jenkins": [], "GitHub Actions": [], "GitLab CI": [], "CircleCI": [], "Datadog": [],
    "Prometheus": [], "Grafana": [], "Splunk": [], "New Relic": [], "Sentry": [], "OpenTelemetry": [],
    "Jest": [], "Cypress": [], "Playwright": [], "Selenium": [], "Pytest": [], "JUnit": [],
    "Linux": [], "Git": [], "Apache Spark": ["pyspark"], "Airflow": [], "dbt": [], "PyTorch": [],
    "TensorFlow": [], "Microservices": ["microservice"], "CI/CD": [], "Agile": [],
}


_NON_SKILL_WORDS_RE = re.compile(
    r"\b(coaching|feedback|onboarding|hiring|interviews?|user needs|knowledge sharing|"
    r"prototypes|experiments|preferred|shipping|elevate|bottlenecks|years|experience|"
    r"proficiency|familiarity|within and across)\b", re.I)


def _is_real_skill(item: str) -> bool:
    """A Skills entry should be a short keyword ("Kubernetes", "Event-Driven Architecture"),
    not a pasted JD sentence or a responsibility. Parenthesised detail doesn't count toward
    the length, so "vector search (Pinecone, Weaviate)" stays."""
    item = item.strip()
    if item.lower() in _NON_SKILL_ITEMS or _NON_SKILL_WORDS_RE.search(item):
        return False
    return len(re.sub(r"\([^)]*\)", "", item).split()) <= 4


def _skills_cleanup(resume_json: dict, jd_text: str) -> None:
    """Deterministic Skills fixes the model is unreliable at: drop responsibility/soft-skill
    items, then add any named JD technology still missing from the Skills section (Section
    6.G asks for every one). Runs before _dedupe_skills."""
    skill_sections = [s for s in (resume_json.get("sections") or [])
                      if isinstance(s, dict) and s.get("type") == "skills" and isinstance(s.get("items"), list)]
    if not skill_sections:
        return
    have = ""
    for s in skill_sections:
        for it in s["items"]:
            if not isinstance(it, dict) or not isinstance(it.get("items"), str):
                continue
            parts = [p.strip() for p in re.split(r",\s*(?![^()]*\))", it["items"])]
            parts = [p for p in parts if p and _is_real_skill(p.replace("[[", "").replace("]]", ""))]
            it["items"] = ", ".join(parts)
            have += " " + it["items"].replace("[[", "").replace("]]", "").lower()
    jd = (jd_text or "").lower()
    def mentions(text, name):
        return re.search(r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9+#])", text) is not None
    missing = []
    for name, aliases in _KNOWN_TECH.items():
        spellings = [name] + aliases
        if any(mentions(jd, n) for n in spellings) and not any(mentions(have, n) for n in spellings):
            missing.append(name)
    if not missing:
        return
    items = skill_sections[0]["items"]
    target = next((it for it in items if isinstance(it, dict) and re.search(r"language|framework|frontend", str(it.get("category", "")), re.I)), None)
    if target is None:
        target = next((it for it in items if isinstance(it, dict) and isinstance(it.get("items"), str)), None)
    if target is None:
        return
    target["items"] = (target["items"].rstrip(", ") + ", " + ", ".join(f"[[{m}]]" for m in missing)).lstrip(", ")


# Generic JD boilerplate the model tacks onto the end of a bullet. Says nothing concrete.
_FILLER_TAIL_RE = re.compile(
    r"[,;]?\s+(?:and\s+)?(?:"
    r"(?:to )?driv\w+ successful outcomes|"
    r"ensur\w+ seamless integration and communication(?: across systems(?: and services)?)?|"
    r"demonstrat\w+ (?:strong |proven )?(?:communication|collaboration|proficiency|experience)\b[^,.]*|"
    r"improv\w+ workflows and processes|"
    r"elevat\w+ (?:the team's )?engineering standards|"
    r"model\w+ a culture of [^,.]*|"
    r"working effectively within and across teams|"
    r"to drive optimization efforts|"
    r"(?:analyz\w+ performance metrics (?:and user feedback )?)?to identify opportunities for improvement"
    r")\s*\.?\s*$", re.I)


def _strip_padding(resume_json: dict) -> None:
    """Remove boilerplate tails from bullets, and leadership/hiring tails from intern or
    junior roles. The model keeps adding these even when told not to; dropping them
    deterministically keeps the bullet's real content."""
    for s in (resume_json.get("sections") or []):
        if not isinstance(s, dict) or s.get("type") not in ("experience", "projects"):
            continue
        for it in (s.get("items") or []):
            if not isinstance(it, dict) or not isinstance(it.get("bullets"), list):
                continue
            junior = bool(_JUNIOR_TITLE_RE.search(str(it.get("job_title", ""))))
            out = []
            for b in it["bullets"]:
                if not isinstance(b, str):
                    out.append(b)
                    continue
                new = b
                for _ in range(3):  # tails are sometimes stacked
                    stripped = _FILLER_TAIL_RE.sub("", new)
                    if junior:
                        # Drop a final ", <clause>" that claims hiring/mentoring/leadership.
                        m = re.search(r",\s+([^,]*)\.?\s*$", stripped)
                        if m and (_SENIOR_CLAIM_RE.search(m.group(1)) or re.search(r"hiring|interviews? and onboarding", m.group(1), re.I)):
                            stripped = stripped[:m.start()]
                    if stripped == new:
                        break
                    new = stripped
                new = new.rstrip(" ,;")
                if new and not new.endswith((".", "!", "?")) and b.rstrip().endswith("."):
                    new += "."
                out.append(new if len(new.split()) >= 6 else b)
            it["bullets"] = out


def _dedupe_skills(resume_json: dict) -> None:
    """Drop repeated skills (case-insensitive, ignoring [[ ]] marks) across all skills
    categories, keeping the first occurrence."""
    seen = set()
    for s in (resume_json.get("sections") or []):
        if not isinstance(s, dict) or s.get("type") != "skills":
            continue
        for it in (s.get("items") or []):
            if not isinstance(it, dict) or not isinstance(it.get("items"), str):
                continue
            kept = []
            # Split on commas that are not inside parentheses.
            for part in re.split(r",\s*(?![^()]*\))", it["items"]):
                key = re.sub(r"\s+", " ", part.replace("[[", "").replace("]]", "")).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    kept.append(part.strip())
            it["items"] = ", ".join(kept)


# ─────────────────────────────────────────────
# ATS keyword guard (model-independent)
# ─────────────────────────────────────────────
# Cheaper models follow the prompt's ATS rules less reliably, so we check the result
# ourselves. We only enforce JD keywords the candidate's ORIGINAL resume already
# contains — those are honest to include, and dropping them is what tanks ATS scores.

_ATS_STOPWORDS = set("""
a about above across after again against all also am an and any are as at be because been
before being below between both but by can could did do does doing down during each either
etc few for from further had has have having he her here hers him his how i if in into is it
its itself just may me might more most must my no nor not now of off on once only or other
our ours out over own per plus same she should so some such than that the their them then
there these they this those through to too under until up upon us very via was we were what
when where which while who whom why will with within without would you your yours
ability able across work working works team teams role roles job position candidate
candidates experience experienced years year strong excellent good great skills skill
knowledge understanding including include includes required requirements require
preferred plus responsibilities responsible duties new using use used well related
environment company opportunity opportunities join looking seeking ideal etc highly
must-have nice-to-have proven demonstrated familiarity familiar degree bachelor bachelors
master masters equivalent relevant field minimum least day days time full part based
help ensure across world best make making provide providing support supporting
""".split())

_ATS_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[./-][A-Za-z0-9+#]+)*")


def _ats_norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", (text or "").replace("[[", "").replace("]]", "")).lower() + " "


def _ats_has(term: str, norm_text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9+#])", norm_text) is not None


def _ats_keywords(jd_text: str, resume_text: str) -> list[str]:
    """JD keywords (single terms + two-word phrases) that also appear in the resume."""
    resume_norm = _ats_norm(resume_text)
    jd = jd_text or ""
    tokens = _ATS_TOKEN_RE.findall(jd)
    terms: dict[str, None] = {}
    lowered = [t.lower().strip(".-/") for t in tokens]
    # Single words count only when they look like a skill/tool (Python, AWS, CI/CD,
    # Node.js, C++) — not plain verbs like "managed", which rewrites rightly change.
    for m in _ATS_TOKEN_RE.finditer(jd):
        tok = m.group(0).strip(".-/")
        low = tok.lower()
        if len(low) < 2 or low in _ATS_STOPWORDS:
            continue
        before = jd[:m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:;\n•*-·"
        if (any(c.isupper() for c in tok[1:]) or any(c.isdigit() or c in "+#./" for c in tok)
                or (tok[0].isupper() and not sentence_start)):
            terms[low] = None
    for a, b in zip(lowered, lowered[1:]):
        if a in _ATS_STOPWORDS or b in _ATS_STOPWORDS or len(a) < 2 or len(b) < 2:
            continue
        terms[f"{a} {b}"] = None
    found = [t for t in terms if _ats_has(t, resume_norm)]
    # Drop single words already covered by a matched phrase so we don't double-count.
    phrases = [t for t in found if " " in t]
    singles = [t for t in found if " " not in t and not any(t in p.split() for p in phrases)]
    return phrases + singles


def _resume_json_text(resume: dict) -> str:
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(resume)
    return "\n".join(parts)


def _ats_missing(resume: dict, keywords: list[str]) -> list[str]:
    norm = _ats_norm(_resume_json_text(resume))
    return [k for k in keywords if not _ats_has(k, norm)]


def _ats_score(resume: dict, keywords: list[str]) -> int | None:
    if not keywords:
        return None
    return round(100 * (1 - len(_ats_missing(resume, keywords)) / len(keywords)))


def _ats_fill_skills(resume: dict, missing: list[str], jd_text: str) -> None:
    """Last resort: append still-missing single-word skills to the Skills section.
    Only called with terms from the candidate's own resume, so nothing is invented."""
    def jd_casing(term):
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9+#])", jd_text or "", re.I)
        return m.group(0) if m else term
    terms = [jd_casing(m) for m in missing if " " not in m][:12]
    if not terms:
        return
    for s in resume.get("sections") or []:
        if isinstance(s, dict) and s.get("type") == "skills" and isinstance(s.get("items"), list) and s["items"]:
            last = s["items"][-1]
            if isinstance(last, dict) and isinstance(last.get("items"), str):
                last["items"] = (last["items"].rstrip(", ") + ", " + ", ".join(terms)).lstrip(", ")
            return


def _do_tailor_call(api_key, resume_text, prompt_text, jd_text, provider,
                    detected_sections, model, base_url, extra_instruction=""):
    prompt = _build_tailor_user_msg(resume_text, prompt_text, jd_text, detected_sections)
    if extra_instruction:
        prompt += "\n\n" + extra_instruction
    # A little temperature makes the model actually rewrite the resume rather than echo it.
    raw = call_ai(provider, api_key, prompt, max_tokens=16000, model=model, base_url=base_url,
                  json_mode=True, temperature=0.4)
    raw = _strip_code_fences(raw)
    try:
        data = _safe_json_loads(raw)
    except json.JSONDecodeError as e:
        try:
            # Vercel's function dir is read-only; only /tmp is writable.
            base = "/tmp" if os.environ.get("VERCEL") else os.path.dirname(__file__)
            dump_path = os.path.join(base, "last_failed_ai_response.txt")
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(raw)
            print(f"[debug] dumped failing AI response to {dump_path}")
        except Exception:
            pass
        raise ValueError(f"AI returned invalid JSON even after repair. First 500 chars: {raw[:500]!r}") from e
    _validate_resume_shape(data)
    return data


def tailor_resume(
    api_key: str,
    resume_text: str,
    prompt_text: str,
    jd_text: str,
    provider: str = "anthropic",
    detected_sections: list[str] | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Call AI to tailor the resume. Returns structured JSON.

    If the model drops sections that were detected in the original resume, retries
    once with an explicit instruction listing each missing section by name.
    """
    data = _do_tailor_call(api_key, resume_text, prompt_text, jd_text, provider,
                           detected_sections, model, base_url)

    # If we know what sections the original had, verify the AI kept them.
    if detected_sections:
        expected = {s.strip().lower() for s in detected_sections if s.strip()}
        got = set(_section_headings(data))
        missing = sorted(expected - got)
        if missing:
            print(f"[tailor-guard] AI dropped sections {missing}; retrying with explicit list.")
            extra = (
                "MANDATORY FIX — the previous attempt dropped these sections. You MUST include "
                "every one of them in the JSON you return now, in the SAME order they appear in "
                f"the original resume: {', '.join(detected_sections)}.\n"
                f"You especially must include: {', '.join(missing)}.\n"
                "Do NOT merge them, do NOT skip any. If a section had no JD-relevant content, "
                "still include it with whatever the original resume contained."
            )
            try:
                retry = _do_tailor_call(api_key, resume_text, prompt_text, jd_text, provider,
                                        detected_sections, model, base_url, extra_instruction=extra)
                # Use the retry only if it has at least as many sections AND restores the missing ones.
                retry_got = set(_section_headings(retry))
                if (expected - retry_got) == set() or len(retry_got) > len(got):
                    data = retry
                else:
                    print(f"[tailor-guard] retry still missing sections; keeping first response.")
            except Exception as e:
                print(f"[tailor-guard] retry failed ({e}); keeping first response.")

    # Tailoring guard: if most experience bullets were copied VERBATIM (the model only
    # rewrote the Summary, or returned the resume basically unchanged), retry once demanding
    # a full rewrite. This replaces the old bullet-preservation guard, which could push the
    # model to copy the original bullets back in.
    if _experience_mostly_verbatim(data, resume_text):
        print("[tailor-guard] experience looks copied verbatim; retrying with a hard rewrite demand.")
        extra = (
            "MANDATORY FIX — you COPIED the experience bullets verbatim instead of tailoring them. "
            "You MUST REWRITE every bullet in every role so it foregrounds this job description's "
            "responsibilities, priorities, tools, and exact terminology, using the candidate's real "
            "facts. Keep the SAME number of bullets, but NO bullet may be copied word-for-word from "
            "the original. Wrap the words you change in [[ ]]. Rewrite the full resume now."
        )
        try:
            retry = _do_tailor_call(api_key, resume_text, prompt_text, jd_text, provider,
                                    detected_sections, model, base_url, extra_instruction=extra)
            if (not _experience_mostly_verbatim(retry, resume_text)
                    and (not detected_sections or _has_all_sections(retry, data))
                    and _count_result_bullets(retry) >= _count_result_bullets(data) * 0.9):
                data = retry
            else:
                print("[tailor-guard] verbatim retry didn't improve; keeping previous response.")
        except Exception as e:
            print(f"[tailor-guard] verbatim retry failed ({e}); keeping previous response.")

    # Quality guard: the model often leaves Skills untouched, or "tailors" bullets by keeping
    # the original sentence and bolting a JD sentence onto it. One retry quoting the exact
    # problems back covers both, so this costs at most one extra call.
    def _quality_problems(d):
        probs = _bullet_quality_issues(d, resume_text)
        if _skills_unchanged(d, resume_text):
            probs.insert(0, "The SKILLS section is unchanged — rewrite it per Section 6.G.")
        return probs

    problems = _quality_problems(data)
    if len(problems) >= 2 or (problems and problems[0].startswith("The SKILLS")):
        print(f"[tailor-guard] {len(problems)} quality problems; retrying with them listed.")
        extra = (
            "MANDATORY FIX — the previous attempt had these problems. Fix EVERY one, keep everything "
            "else as tailored, and return the full resume:\n- " + "\n- ".join(problems[:25]) +
            "\nEach bullet must be ONE rewritten sentence with the JD skills worked into its main "
            "action — not the original sentence followed by an extra sentence."
        )
        try:
            retry = _do_tailor_call(api_key, resume_text, prompt_text, jd_text, provider,
                                    detected_sections, model, base_url, extra_instruction=extra)
            if (len(_quality_problems(retry)) < len(problems)
                    and (not detected_sections or _has_all_sections(retry, data))
                    and not _experience_mostly_verbatim(retry, resume_text)
                    and _count_result_bullets(retry) >= _count_result_bullets(data) * 0.9):
                data = retry
            else:
                print("[tailor-guard] quality retry didn't improve; keeping previous response.")
        except Exception as e:
            print(f"[tailor-guard] quality retry failed ({e}); keeping previous response.")

    # ATS guard: keep coverage of the JD keywords the candidate genuinely has >= 90%,
    # whichever model was used.
    keywords = _ats_keywords(jd_text, resume_text)
    missing = _ats_missing(data, keywords)
    if keywords and len(missing) >= 3 and len(missing) / len(keywords) > 0.10:
        print(f"[ats-guard] {len(missing)}/{len(keywords)} JD keywords dropped; retrying.")
        extra = (
            "MANDATORY FIX — ATS keyword coverage is too low. These job-description keywords "
            "appear in the candidate's ORIGINAL resume but are missing from your output. Work "
            "EVERY one of them back in (Skills, Summary, or the relevant experience bullets), "
            "using the exact spelling shown, without inventing anything new:\n"
            + ", ".join(missing)
        )
        try:
            retry = _do_tailor_call(api_key, resume_text, prompt_text, jd_text, provider,
                                    detected_sections, model, base_url, extra_instruction=extra)
            if (len(_ats_missing(retry, keywords)) < len(missing)
                    and (not detected_sections or _has_all_sections(retry, data))
                    and _count_result_bullets(retry) >= _count_result_bullets(data) * 0.9):
                data = retry
        except Exception as e:
            print(f"[ats-guard] retry failed ({e}); keeping previous response.")
        _ats_fill_skills(data, _ats_missing(data, keywords), jd_text)

    _strip_padding(data)
    _skills_cleanup(data, jd_text)
    _dedupe_skills(data)
    return data


# ─────────────────────────────────────────────
# Job identity extraction (company / title from the JD)
# ─────────────────────────────────────────────

def _pop_job_meta(resume: dict) -> dict:
    """Pull the JD-derived company/title out of the resume dict (and remove them)."""
    if not isinstance(resume, dict):
        return {"company": "", "position": ""}
    company = (resume.pop("detected_company", "") or "").strip()
    position = (resume.pop("detected_job_title", "") or "").strip()
    # Guard against the model echoing placeholder text.
    if company.lower() in ("empty string", "n/a", "none", "not specified", "unknown"):
        company = ""
    if position.lower() in ("empty string", "n/a", "none", "not specified", "unknown"):
        position = ""
    return {"company": company, "position": position}


# ─────────────────────────────────────────────
# PDF generation
# ─────────────────────────────────────────────

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _clean_text(text) -> str:
    """Replace Unicode characters unsupported by Helvetica with ASCII equivalents,
    and unwrap markdown-style links so ATS sees plain text + URL.

    Final fallback: anything that's still beyond Latin-1 (which is what fpdf2's
    built-in Helvetica supports) gets stripped, so generation never crashes with
    'ascii'/'latin-1' codec errors."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # Unwrap [display](url) -> "display (url)" or just the URL if display == url.
    def _unwrap(m):
        display, url = m.group(1).strip(), m.group(2).strip()
        if display == url or display.lower() == url.lower():
            return url
        return f"{display} ({url})"
    text = _MD_LINK_RE.sub(_unwrap, text)
    # Strip the [[ ]] change-highlight markers (preview only, must not appear in the PDF).
    text = text.replace("[[", "").replace("]]", "")
    cleaned = (text
        .replace("\u2013", "-")   # en dash
        .replace("\u2014", "-")   # em dash
        .replace("\u2018", "'")   # left single quote
        .replace("\u2019", "'")   # right single quote
        .replace("\u201c", '"')   # left double quote
        .replace("\u201d", '"')   # right double quote
        .replace("\u2022", "-")   # bullet
        .replace("\u2026", "...")  # ellipsis
        .replace("\u00a0", " ")   # non-breaking space
        .replace("\u2010", "-")   # hyphen
        .replace("\u2011", "-")   # non-breaking hyphen
        .replace("\u2012", "-")   # figure dash
        .replace("\u2015", "-")   # horizontal bar
        .replace("\u2027", "-")   # hyphenation point
        .replace("\u2032", "'")   # prime
        .replace("\u2033", '"')   # double prime
    )
    # Final fallback: drop anything still beyond Latin-1 so fpdf2's Helvetica
    # never crashes with a codec error. Accented Latin-1 chars (\u00e9, \u00f1, \u00fc, ...)
    # survive; truly exotic glyphs become "?".
    return cleaned.encode("latin-1", "replace").decode("latin-1")


# ─────────────────────────────────────────────
# PDF Templates
# ─────────────────────────────────────────────

PDF_TEMPLATES = {
    "classic_blue": {
        "accent": (37, 99, 165),
        "dark": (26, 26, 26),
        "body": (51, 51, 51),
        "gray": (102, 102, 102),
        "name_align": "C",
        "name_size": 20,
        "header_transform": "upper",
        "header_size": 12,
        "header_line_color": (37, 99, 165),
        "skill_bullet": "-",
        "contact_align": "C",
        "job_company_separator": " | ",        # pipe separator between company and location
        "job_title_size": 10.5,
    },
    "modern_green": {
        "accent": (107, 142, 35),
        "dark": (26, 26, 26),
        "body": (51, 51, 51),
        "gray": (102, 102, 102),
        "name_align": "L",
        "name_size": 20,
        "header_transform": "title",
        "header_size": 12,
        "header_line_color": (200, 200, 200),
        "skill_bullet": "-",
        "contact_align": "L",
        "job_company_separator": " | ",        # pipe separator between company and location
        "job_title_size": 10.5,
    },
    "universal": {
        "accent": (50, 50, 50),
        "dark": (30, 30, 30),
        "body": (45, 45, 45),
        "gray": (110, 110, 110),
        "name_align": "L",
        "name_size": 20,
        "header_transform": "upper",
        "header_size": 12,
        "header_line_color": (180, 180, 180),
        "skill_bullet": "-",
        "contact_align": "L",
        "job_company_separator": " | ",        # pipe separator between company and location
        "job_title_size": 10.5,
    },
    # Mirrors the layout used by Sudderick Matthew's reference resume PDF:
    # large regular-weight name, stacked contact lines, thin grey rule ABOVE each
    # section heading, regular-weight title-case headings, regular-weight job titles,
    # bullet glyphs with **bold** inline keyword highlighting.
    "minimal_clean": {
        "accent":             (35, 35, 35),
        "dark":               (25, 25, 25),
        "body":               (50, 50, 50),
        "gray":               (110, 110, 110),
        "name_align":         "L",
        "name_size":          20,
        "name_bold":          False,           # NEW: name rendered in regular weight
        "contact_align":      "L",
        "contact_stacked":    True,            # NEW: each contact part on its own line
        "header_transform":   "title",
        "header_size":        12,
        "header_bold":        False,           # NEW: heading in regular weight
        "header_line_color":  (210, 210, 210),
        "header_line_position":"above",        # NEW: rule sits ABOVE the heading text
        "skill_bullet":       "-",
        "job_title_bold":     False,           # NEW: job title regular weight
        "job_title_size":     10.5,
        "job_company_separator": " | ",        # pipe separator between company and location
        "bullet_inline_bold": True,            # NEW: parse **bold** markdown inside bullets
        "bullet_glyph_size":  0.85,
        "simple_list_as_paragraph": True,      # NEW: skills/etc. render as plain paragraph, no bullets
        "education_layout":   "minimal",       # NEW: degree first, school+location grey, dates grey
    },
    "minimal_blue": {
        "accent":             (37, 99, 165),   # Blue accent color
        "dark":               (25, 25, 25),
        "body":               (50, 50, 50),
        "gray":               (110, 110, 110),
        "name_align":         "L",
        "name_size":          20,              # Same as classic_blue
        "name_bold":          True,            # Same as classic_blue
        "contact_align":      "L",
        "contact_stacked":    True,            # each contact part on its own line
        "header_transform":   "upper",         # Same as classic_blue
        "header_size":        12,              # Same as classic_blue
        "header_bold":        True,            # Same as classic_blue
        "header_line_color":  (37, 99, 165),   # Blue header line
        "header_line_position":"above",        # rule sits ABOVE the heading text
        "skill_bullet":       "-",
        "job_title_bold":     True,            # Same as classic_blue
        "job_title_size":     10.5,            # Same as classic_blue
        "job_company_separator": " | ",        # pipe separator between company and location
        "bullet_inline_bold": True,            # parse **bold** markdown inside bullets
        "bullet_glyph_size":  0.85,
        "simple_list_as_paragraph": True,      # skills/etc. render as plain paragraph, no bullets
        "education_layout":   "minimal",       # degree first, school+location grey, dates grey
    },
}


class ResumePDF(FPDF):

    def __init__(self, template="modern_green"):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        cfg = PDF_TEMPLATES.get(template, PDF_TEMPLATES["modern_green"])
        self.cfg = cfg
        self.ACCENT = cfg["accent"]
        self.DARK = cfg["dark"]
        self.BODY = cfg["body"]
        self.GRAY = cfg["gray"]

    def header(self):
        pass  # no header

    # Belt-and-suspenders: every text passing into fpdf2's core renderers gets
    # cleaned, so it doesn't matter if a call site forgot to wrap with _clean_text.
    def cell(self, w=None, h=None, text="", *args, **kwargs):
        if "txt" in kwargs:  # fpdf2 still accepts the deprecated 'txt' kwarg
            kwargs["txt"] = _clean_text(kwargs["txt"])
            return super().cell(w, h, *args, **kwargs)
        return super().cell(w, h, _clean_text(text), *args, **kwargs)

    def multi_cell(self, w=0, h=None, text="", *args, **kwargs):
        if "txt" in kwargs:
            kwargs["txt"] = _clean_text(kwargs["txt"])
            return super().multi_cell(w, h, *args, **kwargs)
        return super().multi_cell(w, h, _clean_text(text), *args, **kwargs)

    def section_header(self, text):
        transform = self.cfg.get("header_transform", "upper")
        display = _clean_text(text).upper() if transform == "upper" else _clean_text(text)
        weight = "B" if self.cfg.get("header_bold", True) else ""
        line_pos = self.cfg.get("header_line_position", "below")  # "above" / "below" / "none"
        self.ln(2)
        if line_pos == "above":
            # Draw the rule first, then a small gap, then the heading text.
            self.set_draw_color(*self.cfg.get("header_line_color", (200, 200, 200)))
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(3)
        self.set_font("Helvetica", weight, self.cfg.get("header_size", 14))
        self.set_text_color(*self.ACCENT)
        self.cell(0, 9, display, new_x="LMARGIN", new_y="NEXT")
        if line_pos == "below":
            self.set_draw_color(*self.cfg.get("header_line_color", (200, 200, 200)))
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def body_text(self, text, size=10):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*self.BODY)
        self.multi_cell(0, 5, _clean_text(text))

    def bullet(self, text):
        # If template enables inline **bold** markdown, route to the rich-text bullet.
        if self.cfg.get("bullet_inline_bold"):
            return self._bullet_inline_bold(text)
        text = _clean_text(text)
        size = 9.5
        line_h = 4.5
        indent = 8
        bullet_x = self.l_margin + indent
        text_x = bullet_x + 5
        text_w = self.w - self.r_margin - text_x

        self.set_font("Helvetica", "", size)
        self.set_text_color(*self.BODY)

        # Manual word-wrap so wrapped lines stay under text_x (hanging indent),
        # rather than snapping back to the left margin like multi_cell does.
        tokens = [t for t in re.split(r"(\s+)", text) if t]
        lines = []
        cur, cur_w = [], 0.0
        for tok in tokens:
            tw = self.get_string_width(tok)
            if cur and (cur_w + tw) > text_w:
                line_text = "".join(cur).rstrip()
                if line_text:
                    lines.append(line_text)
                if tok.isspace():
                    cur, cur_w = [], 0.0
                else:
                    cur, cur_w = [tok], tw
            else:
                cur.append(tok)
                cur_w += tw
        if cur:
            line_text = "".join(cur).rstrip()
            if line_text:
                lines.append(line_text)
        if not lines:
            lines = [""]

        # If the first line wouldn't fit on the current page, force a page
        # break BEFORE drawing the glyph. Otherwise the circle is rendered
        # at the bottom of page N while the cell auto-breaks and the text
        # lands on page N+1, leaving an orphaned bullet.
        if self.get_y() + line_h > self.h - self.b_margin:
            self.add_page()

        # Vertically center the bullet glyph with the cap-middle of the first
        # line of text. fpdf2.circle treats (x, y) as the *center* of the
        # circle, and the cap-middle of 9.5pt Helvetica inside a 4.5mm cell
        # sits at roughly y + line_h * 0.55 — calibrated empirically.
        glyph_cy = self.get_y() + line_h * 0.55
        for i, line in enumerate(lines):
            if i == 0:
                self.circle(bullet_x + 1, glyph_cy, 0.8, style="F")
            self.set_x(text_x)
            self.cell(text_w, line_h, line)
            self.ln(line_h)
        self.ln(0.5)

    def _bullet_inline_bold(self, text):
        """Bullet with hanging indent and **bold** markdown rendered inline.

        Used by templates whose cfg sets `bullet_inline_bold: true`. Word-wraps using
        per-segment widths (regular vs. bold) so a long bold span doesn't break layout.
        """
        text = _clean_text(text)
        size = 9.5
        line_h = 4.7
        indent = 6
        bullet_x = self.l_margin + 2
        text_x = self.l_margin + indent
        text_w = self.w - self.r_margin - text_x
        body_color = self.BODY

        # Tokenize: split on **...** so even-indexed parts are regular, odd-indexed are bold.
        parts = text.split("**")
        tokens = []  # list of (weight, word_or_space)
        for idx, part in enumerate(parts):
            weight = "B" if idx % 2 == 1 else ""
            for chunk in re.split(r"(\s+)", part):
                if chunk:
                    tokens.append((weight, chunk))

        # Word-wrap. Each token's width depends on its weight, so we have to switch fonts
        # while measuring.
        lines = []  # list of [(weight, text), ...]
        cur, cur_w = [], 0.0
        for weight, tok in tokens:
            self.set_font("Helvetica", weight, size)
            tw = self.get_string_width(tok)
            if cur and (cur_w + tw) > text_w:
                # Flush — strip trailing whitespace
                while cur and cur[-1][1].isspace():
                    cur_w -= self.get_string_width(cur[-1][1])  # rough, font may differ
                    cur.pop()
                if cur:
                    lines.append(cur)
                # Skip leading whitespace on the new line
                if tok.isspace():
                    cur, cur_w = [], 0.0
                    continue
                cur, cur_w = [(weight, tok)], tw
            else:
                cur.append((weight, tok))
                cur_w += tw
        if cur:
            while cur and cur[-1][1].isspace():
                cur.pop()
            if cur:
                lines.append(cur)

        # Page-break guard — keep the glyph on the same page as the first
        # text line (see bullet() for rationale).
        if self.get_y() + line_h > self.h - self.b_margin:
            self.add_page()

        # Render. Center the glyph vertically with the cap-middle of the first
        # line — same calibration as bullet() above.
        self.set_text_color(*body_color)
        glyph_r = self.cfg.get("bullet_glyph_size", 0.85)
        glyph_cy = self.get_y() + line_h * 0.55
        for i, line in enumerate(lines):
            if i == 0:
                self.circle(bullet_x + 1, glyph_cy, glyph_r, style="F")
            self.set_x(text_x)
            for weight, tok in line:
                self.set_font("Helvetica", weight, size)
                self.cell(self.get_string_width(tok), line_h, tok)
            self.ln(line_h)
        self.ln(0.5)

    def list_bullet(self, text):
        """Render a bullet for simple lists (certs, awards, etc.) using template bullet style."""
        text = _clean_text(text)
        size = 9.5
        line_h = 4.5
        indent = 8
        glyph = self.cfg.get("skill_bullet", "-")
        glyph_w = 5
        x0 = self.get_x()
        bullet_x = x0 + indent
        text_x = bullet_x + glyph_w
        text_w = self.w - self.r_margin - text_x

        self.set_font("Helvetica", "", size)
        self.set_text_color(*self.BODY)

        tokens = [t for t in re.split(r"(\s+)", text) if t]
        lines = []
        cur, cur_w = [], 0.0
        for tok in tokens:
            tw = self.get_string_width(tok)
            if cur and (cur_w + tw) > text_w:
                line_text = "".join(cur).rstrip()
                if line_text:
                    lines.append(line_text)
                if tok.isspace():
                    cur, cur_w = [], 0.0
                else:
                    cur, cur_w = [tok], tw
            else:
                cur.append(tok)
                cur_w += tw
        if cur:
            line_text = "".join(cur).rstrip()
            if line_text:
                lines.append(line_text)
        if not lines:
            lines = [""]

        # Keep the glyph on the same page as the first text line.
        if self.get_y() + line_h > self.h - self.b_margin:
            self.add_page()

        for i, line in enumerate(lines):
            if i == 0:
                self.set_x(bullet_x)
                self.cell(glyph_w, line_h, glyph)
            else:
                self.set_x(text_x)
            self.cell(text_w, line_h, line)
            self.ln(line_h)
        self.ln(0.5)


def _to_list(val):
    """Safely convert a value to a list. Handles None, dict, str, list."""
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return [val]
    if isinstance(val, str):
        return [val]
    return []


def _render_section_text(pdf, section):
    """Render a text section (e.g. Summary, Profile, Objective)."""
    pdf.section_header(section.get("heading", "Summary"))
    pdf.body_text(section.get("content", ""))
    pdf.ln(3)


def _wrap_text(pdf, text, max_width):
    """Split text into lines that fit within max_width using current font."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test = f"{current_line} {word}".strip()
        if pdf.get_string_width(test) <= max_width:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


def _render_section_skills(pdf, section):
    """Render a skills section with arrow bullets and category: items rows."""
    pdf.section_header(section.get("heading", "Skills"))
    arrow_indent = 6   # space for the ">" arrow
    line_h = 4.5

    for skill in _to_list(section.get("items")):
        if isinstance(skill, str):
            pdf.list_bullet(skill)
            continue
        cat = _clean_text(skill.get("category", ""))
        items = _clean_text(skill.get("items", ""))

        # Arrow bullet
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*pdf.BODY)
        pdf.cell(arrow_indent, 5, pdf.cfg.get("skill_bullet", "-"))

        # Category label (bold)
        pdf.set_font("Helvetica", "B", 9.5)
        label = f"{cat}:  "
        label_w = pdf.get_string_width(label)
        pdf.cell(label_w, 5, label)

        # First line: items start right after the bold category label
        first_line_x = pdf.get_x()
        first_line_width = pdf.w - pdf.r_margin - first_line_x

        # Continuation lines: start from left margin + arrow indent
        cont_x = pdf.l_margin + arrow_indent
        cont_width = pdf.w - pdf.r_margin - cont_x

        # Wrap: first line has less space, continuation lines get full width
        pdf.set_font("Helvetica", "", 9.5)
        words = items.split()
        lines = []
        current_line = ""
        on_first = True
        max_w = first_line_width

        for word in words:
            test = f"{current_line} {word}".strip()
            if pdf.get_string_width(test) <= max_w:
                current_line = test
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
                if on_first:
                    on_first = False
                    max_w = cont_width
        if current_line:
            lines.append(current_line)

        for j, line in enumerate(lines):
            if j == 0:
                pdf.cell(first_line_width, line_h, line, new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_x(cont_x)
                pdf.cell(cont_width, line_h, line, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
    pdf.ln(1)


def _render_section_experience(pdf, section):
    """Render an experience section with job entries.

    All templates: "Company | Location | Title" on line 1, dates on line 2.
    """
    pdf.section_header(section.get("heading", "Experience"))
    cfg = pdf.cfg
    title_weight = "B" if cfg.get("job_title_bold", True) else ""
    title_size = cfg.get("job_title_size", 10.5)
    sep = cfg.get("job_company_separator", " | ")

    for job in _to_list(section.get("items")):
        if isinstance(job, str):
            pdf.body_text(job)
            continue
        company = _clean_text(job.get("company", ""))
        title = _clean_text(job.get("job_title", ""))
        dates = _clean_text(job.get("dates", ""))
        location = _clean_text(job.get("location", "") or job.get("context", ""))

        # Line 1: Company | Location | Title
        pdf.set_font("Helvetica", title_weight, title_size)
        pdf.set_text_color(*pdf.DARK)
        # Build the line with proper handling of missing location
        if company and location and title:
            company_location_title = f"{company}{sep}{location}{sep}{title}"
        elif company and title:
            company_location_title = f"{company}{sep}{title}"
        elif company:
            company_location_title = company
        else:
            company_location_title = title or ""
        pdf.multi_cell(0, 6, company_location_title, align="L", new_x="LMARGIN", new_y="NEXT")  # wrap long headers

        # Line 2: Dates (grey)
        if dates:
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(*pdf.GRAY)
            pdf.cell(0, 5, dates, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        # Bullets
        for b in _to_list(job.get("bullets")):
            pdf.bullet(str(b))
        pdf.ln(3)


def _render_section_education(pdf, section):
    """Render an education section (supports multiple entries).

    Default layout: School name (bold), degree as sub-item, dates below.
    `education_layout="minimal"`: Degree headline (regular, larger), then
    "School - Location" in grey, then dates in grey — mirrors the experience block.
    """
    pdf.section_header(section.get("heading", "Education"))
    items = _to_list(section.get("items"))
    layout = pdf.cfg.get("education_layout", "default")
    sep = pdf.cfg.get("job_company_separator", " - ")

    for edu in items:
        if isinstance(edu, str):
            pdf.body_text(edu)
            pdf.ln(2)
            continue
        degree = _clean_text(edu.get("degree", ""))
        school = _clean_text(edu.get("school", ""))
        location = _clean_text(edu.get("location", ""))
        dates = _clean_text(edu.get("dates", ""))

        if layout == "minimal":
            # Headline: Degree
            pdf.set_font("Helvetica", "", pdf.cfg.get("job_title_size", 13))
            pdf.set_text_color(*pdf.DARK)
            pdf.cell(0, 7, degree or school, new_x="LMARGIN", new_y="NEXT")
            # Sub-line: School - Location
            sub_parts = [p for p in (school, location) if p]
            if sub_parts and degree:
                pdf.set_font("Helvetica", "", 9.5)
                pdf.set_text_color(*pdf.GRAY)
                pdf.cell(0, 5, sep.join(sub_parts), new_x="LMARGIN", new_y="NEXT")
            # Dates
            if dates:
                pdf.set_font("Helvetica", "", 9.5)
                pdf.set_text_color(*pdf.GRAY)
                pdf.cell(0, 5, dates, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            continue

        # Default layout
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*pdf.DARK)
        pdf.cell(0, 6, school, new_x="LMARGIN", new_y="NEXT")
        if degree:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*pdf.BODY)
            pdf.set_x(pdf.l_margin + 8)
            pdf.cell(5, 5, pdf.cfg.get("skill_bullet", "-"))
            pdf.cell(0, 5, degree, new_x="LMARGIN", new_y="NEXT")
        if dates:
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(*pdf.GRAY)
            pdf.set_x(pdf.l_margin + 13)
            pdf.cell(0, 5, dates, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)


def _render_section_projects(pdf, section):
    """Render a projects section."""
    pdf.section_header(section.get("heading", "Projects"))
    for proj in _to_list(section.get("items")):
        if isinstance(proj, str):
            pdf.bullet(proj)
            continue
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*pdf.DARK)
        name = _clean_text(proj.get("name", ""))
        pdf.cell(0, 6, name, new_x="LMARGIN", new_y="NEXT")

        desc = proj.get("description", "")
        tech = proj.get("technologies", "")
        if desc or tech:
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(*pdf.ACCENT)
            if tech:
                pdf.cell(0, 5, _clean_text(tech), new_x="LMARGIN", new_y="NEXT")
            if desc:
                pdf.set_text_color(*pdf.BODY)
                pdf.multi_cell(0, 4.5, _clean_text(desc))
            pdf.ln(1)

        for b in _to_list(proj.get("bullets")):
            pdf.bullet(str(b))
        pdf.ln(3)


def _render_section_simple_list(pdf, section):
    """Render a simple list section (certifications, awards, languages, etc.)."""
    pdf.section_header(section.get("heading", ""))

    # Templates can opt to render simple lists as a plain comma-joined paragraph
    # instead of a bulleted list (matches the reference resume's Skills layout).
    if pdf.cfg.get("simple_list_as_paragraph"):
        items_text = []
        for item in _to_list(section.get("items")):
            if isinstance(item, str):
                items_text.append(item.strip())
            elif isinstance(item, dict):
                items_text.append((item.get("name", "") or item.get("title", "") or "").strip())
        joined = ", ".join(p for p in items_text if p)
        if joined:
            pdf.body_text(joined)
        pdf.ln(2)
        return

    for item in _to_list(section.get("items")):
        if isinstance(item, str):
            pdf.list_bullet(item)
        elif isinstance(item, dict):
            text = item.get("name", "") or item.get("title", "") or str(item)
            pdf.list_bullet(text)
    pdf.ln(2)


# Map section types to their renderers
_SECTION_RENDERERS = {
    "text": _render_section_text,
    "skills": _render_section_skills,
    "experience": _render_section_experience,
    "education": _render_section_education,
    "projects": _render_section_projects,
    "simple_list": _render_section_simple_list,
}


def generate_pdf(data: dict, template: str = "modern_green") -> bytes:
    pdf = ResumePDF(template=template)
    pdf.add_page()
    pdf.set_margins(18, 15, 18)
    pdf.set_y(15)

    name_align = pdf.cfg.get("name_align", "L")
    contact_align = pdf.cfg.get("contact_align", "L")
    name_size = pdf.cfg.get("name_size", 24)
    name_weight = "B" if pdf.cfg.get("name_bold", True) else ""

    # Name
    pdf.set_font("Helvetica", name_weight, name_size)
    pdf.set_text_color(*pdf.DARK)
    pdf.cell(0, 12, _clean_text(data.get("name", "")), align=name_align, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Contact — use multi_cell so a long contact line WRAPS instead of running off the page.
    contact = _strip_contact_labels(_fix_contact_urls(_clean_text(data.get("contact", ""))))
    if contact:
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*pdf.GRAY)
        contact_parts = [p.strip() for p in contact.split("|") if p.strip()]
        if pdf.cfg.get("contact_stacked"):
            # Each contact part on its own line, no separator (mirrors the minimal_clean layout).
            for part in contact_parts:
                pdf.multi_cell(0, 5, part, align=contact_align, new_x="LMARGIN", new_y="NEXT")
        else:
            url_parts = [p for p in contact_parts if "http" in p or "linkedin" in p.lower() or "github" in p.lower()]
            non_url_parts = [p for p in contact_parts if p not in url_parts]
            if non_url_parts:
                pdf.multi_cell(0, 5, " | ".join(non_url_parts), align=contact_align, new_x="LMARGIN", new_y="NEXT")
            for url in url_parts:
                pdf.multi_cell(0, 5, url, align=contact_align, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Dynamic sections
    if "sections" in data:
        for section in data["sections"]:
            sec_type = section.get("type", "simple_list")
            renderer = _SECTION_RENDERERS.get(sec_type, _render_section_simple_list)
            renderer(pdf, section)
    else:
        # Backward compatibility: handle old fixed-schema format
        if data.get("summary"):
            pdf.section_header("Summary")
            pdf.body_text(data["summary"])
            pdf.ln(3)

        if data.get("skills"):
            pdf.section_header("Skills")
            for skill in data["skills"]:
                cat = _clean_text(skill.get("category", ""))
                items = _clean_text(skill.get("items", ""))
                pdf.set_font("Helvetica", "B", 9.5)
                pdf.set_text_color(*pdf.BODY)
                label = f"{cat}: "
                pdf.cell(pdf.get_string_width(label) + 1, 5, label)
                pdf.set_font("Helvetica", "", 9.5)
                remaining = pdf.w - pdf.r_margin - pdf.get_x()
                pdf.multi_cell(remaining, 4.5, items)
                pdf.ln(1)
            pdf.ln(2)

        if data.get("experience"):
            pdf.section_header("Experience")
            for job in data["experience"]:
                company = _clean_text(job.get('company', ''))
                location = _clean_text(job.get('location', ''))
                title = _clean_text(job.get('job_title', ''))
                dates = _clean_text(job.get('dates', ''))

                # Line 1: Company | Location | Title
                pdf.set_font("Helvetica", "B", 11)
                pdf.set_text_color(*pdf.DARK)
                if company and location and title:
                    company_location_title = f"{company} | {location} | {title}"
                elif company and title:
                    company_location_title = f"{company} | {title}"
                elif company:
                    company_location_title = company
                else:
                    company_location_title = title or ""
                pdf.multi_cell(0, 6, company_location_title, align="L", new_x="LMARGIN", new_y="NEXT")  # wrap long headers

                # Line 2: Dates
                if dates:
                    pdf.set_font("Helvetica", "", 9.5)
                    pdf.set_text_color(*pdf.GRAY)
                    pdf.cell(0, 5, dates, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1)

                for b in job.get("bullets", []):
                    pdf.bullet(b)
                pdf.ln(3)

        edu = data.get("education", {})
        if edu:
            pdf.section_header("Education")
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.set_text_color(*pdf.BODY)
            pdf.cell(0, 6, _clean_text(edu.get("degree", "")), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*pdf.GRAY)
            edu_meta = _clean_text(f"{edu.get('school', '')}  |  {edu.get('dates', '')}  |  {edu.get('location', '')}")
            pdf.cell(0, 5, edu_meta)

    return pdf.output()


# ─────────────────────────────────────────────
# JD scraping helpers
# ─────────────────────────────────────────────

PLATFORM_DOMAINS = {
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
}

# All known platform names (lowercase) for filtering from titles
_PLATFORM_NAMES_LOWER = {v.lower() for v in PLATFORM_DOMAINS.values()} | {
    "linkedin", "indeed", "indeed.com", "glassdoor", "glassdoor.com",
    "otta", "welcome to the jungle",
}


def _is_platform_part(text):
    """Check if a title part is a platform name (not a real company)."""
    t = text.lower().strip()
    for pn in _PLATFORM_NAMES_LOWER:
        if pn in t:
            return True
    return False


def extract_job_metadata(url, title, text):
    """Detect platform from domain and parse company/position from page title."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")

    # Detect platform
    platform = "Other"
    for domain_key, platform_name in PLATFORM_DOMAINS.items():
        if domain_key in domain:
            platform = platform_name
            break

    company = ""
    position = ""

    if title:
        # LinkedIn: "Job Title at Company | LinkedIn"
        # Indeed: "Job Title - Company - Location | Indeed.com"
        # Glassdoor: "Company hiring Job Title in Location | Glassdoor"
        # WTTJ: "Company - Role | Welcome to the Jungle"

        if platform == "LinkedIn":
            m = re.match(r"^(.+?)\s+at\s+(.+?)(?:\s*\||\s*[-–]|\s*$)", title)
            if m:
                position = m.group(1).strip()
                company = m.group(2).strip()
        elif platform == "Indeed":
            parts = re.split(r"\s*[-–]\s*", title)
            if len(parts) >= 2:
                position = parts[0].strip()
                company = parts[1].strip()
        elif platform == "Glassdoor":
            m = re.match(r"^(.+?)\s+hiring\s+(.+?)(?:\s+in\s+|\s*\|)", title)
            if m:
                company = m.group(1).strip()
                position = m.group(2).strip()
        elif platform == "Welcome to the Jungle":
            # "Company - Role | Welcome to the Jungle (formerly Otta)"
            # Split on | first, take everything before the platform name
            main = re.split(r"\s*\|\s*", title)[0]
            parts = re.split(r"\s*[-–]\s*", main, maxsplit=1)
            if len(parts) >= 2:
                company = parts[0].strip()
                position = parts[1].strip()
            elif len(parts) == 1:
                position = parts[0].strip()

        # Generic fallback
        if not company and not position:
            # Split on | first to separate "content | platform branding"
            pipe_parts = re.split(r"\s*\|\s*", title)
            # Filter out parts that are platform names
            content_parts = [p.strip() for p in pipe_parts if not _is_platform_part(p)]
            if not content_parts:
                content_parts = [p.strip() for p in pipe_parts]

            # Use just the first content part (before any |), split on dash for company/role
            main = content_parts[0]
            sub = re.split(r"\s*[-–]\s*", main, maxsplit=1)
            if len(sub) >= 2:
                company = sub[0].strip()
                position = sub[1].strip()
            elif len(sub) == 1:
                position = sub[0].strip()

    return {
        "platform": platform,
        "company": company,
        "position": position,
        "url": url,
    }


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# ─────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    """User login. Returns a signed bearer token on success."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    try:
        user = STORE.get_user(email)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Login temporarily unavailable: {e}"}), 500
    if not user or not verify_password(password, user.get("password_hash", "")):
        return jsonify({"error": "Invalid email or password."}), 401
    token = make_token(email, "user")
    return jsonify({"success": True, "token": token, "email": email, "name": user.get("name", "")})


@app.route("/api/me", methods=["GET"])
@require_user
def api_me():
    ident = request.identity
    return jsonify({"success": True, "email": ident.get("email"), "role": ident.get("role")})


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    """Admin login against the fixed admin credentials."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if email != ADMIN_EMAIL or password != ADMIN_PASSWORD:
        return jsonify({"error": "Invalid admin credentials."}), 401
    token = make_token(ADMIN_EMAIL, "admin")
    return jsonify({"success": True, "token": token, "email": ADMIN_EMAIL})


@app.route("/api/admin/health", methods=["GET"])
@require_admin
def api_admin_health():
    """Report which storage backend is live so the admin can confirm persistence."""
    info = STORE.describe() if hasattr(STORE, "describe") else {"backend": "unknown"}
    try:
        info["reachable"] = STORE.ping() if hasattr(STORE, "ping") else None
    except Exception as e:
        info["reachable"] = False
        info["error"] = str(e)
    return jsonify({"success": True, "storage": info})


@app.route("/api/admin/users", methods=["GET"])
@require_admin
def api_admin_list_users():
    try:
        return jsonify({"success": True, "users": STORE.list_users()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not list users: {e}"}), 500


@app.route("/api/admin/users", methods=["POST"])
@require_admin
def api_admin_create_user():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "A valid email is required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if email == ADMIN_EMAIL:
        return jsonify({"error": "That email is reserved for the admin account."}), 400
    try:
        # Store the plaintext password too so the admin can view/change it later.
        STORE.create_user(email, hash_password(password), name=name, password_plain=password)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not create user: {e}"}), 500
    return jsonify({"success": True, "email": email})


def _admin_update_user(email, data):
    """Change a user's name and/or password."""
    email = (email or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required."}), 400
    updates = {}
    if "name" in data:
        updates["name"] = (data.get("name") or "").strip()
    if data.get("password"):
        pw = data["password"]
        if len(pw) < 6:
            return jsonify({"error": "Password must be at least 6 characters."}), 400
        updates["password"] = pw
        updates["password_hash"] = hash_password(pw)
    if not updates:
        return jsonify({"error": "Nothing to update."}), 400
    try:
        if not STORE.get_user(email):
            return jsonify({"error": "User not found."}), 404
        STORE.update_user(email, updates)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not update user: {e}"}), 500
    return jsonify({"success": True, "email": email})


def _admin_delete_user(email):
    email = (email or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required."}), 400
    try:
        existed = STORE.delete_user(email)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not delete user: {e}"}), 500
    if not existed:
        return jsonify({"error": "User not found."}), 404
    return jsonify({"success": True, "email": email})


PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "together": "Together AI",
    "ollama": "Ollama (local)",
    "openai_compatible": "OpenAI-compatible (custom)",
}


def _mask_key(key: str) -> str:
    """sk-proj-abc...wxyz — enough to tell two keys apart, not enough to use one."""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "*" * max(0, len(key) - 2)
    return f"{key[:7]}...{key[-4:]}"


@app.route("/api/admin/user-keys", methods=["POST"])
@require_admin
def api_admin_user_keys():
    """Provider API keys a user has saved to their account.

    Masked by default. Full values are returned only when the admin explicitly asks
    (reveal=true), and every reveal is written to the server log — these are the
    user's own billable credentials, so the read should leave a trace.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    reveal = bool(data.get("reveal"))
    provider_filter = (data.get("provider") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required."}), 400
    try:
        if not STORE.get_user(email):
            return jsonify({"error": "User not found."}), 404
        settings = STORE.get_settings(email) or {}
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not load that user's keys: {e}"}), 500

    provider_keys = settings.get("providerKeys") or {}
    if not isinstance(provider_keys, dict):
        provider_keys = {}
    base_urls = settings.get("providerBaseUrls") or {}
    if not isinstance(base_urls, dict):
        base_urls = {}
    models = settings.get("providerModels") or {}
    if not isinstance(models, dict):
        models = {}

    if reveal:
        admin_email = getattr(request, "identity", {}).get("email", "admin")
        scope = provider_filter or "ALL providers"
        print(f"[admin-audit] {admin_email} revealed API key ({scope}) for user {email}")

    out = []
    for provider, key in sorted(provider_keys.items()):
        if not isinstance(key, str) or not key.strip():
            continue
        if provider_filter and provider != provider_filter:
            continue
        entry = {
            "provider": provider,
            "label": PROVIDER_LABELS.get(provider, provider),
            "masked": _mask_key(key),
            "model": models.get(provider) or "",
            "base_url": base_urls.get(provider) or "",
        }
        if reveal:
            entry["key"] = key
        out.append(entry)

    return jsonify({"success": True, "email": email,
                    "active_provider": settings.get("provider") or "",
                    "keys": out})


# The admin UI uses these POST routes (email in the JSON body) because some
# hosting/routing layers (e.g. Vercel's catch-all route) and proxies mishandle
# PUT/DELETE or percent-encoded emails in the URL path.
@app.route("/api/admin/users/update", methods=["POST"])
@require_admin
def api_admin_update_user_post():
    data = request.get_json(silent=True) or {}
    return _admin_update_user(data.get("email"), data)


@app.route("/api/admin/users/delete", methods=["POST"])
@require_admin
def api_admin_delete_user_post():
    data = request.get_json(silent=True) or {}
    return _admin_delete_user(data.get("email"))


# Legacy REST-style routes, kept for backward compatibility.
@app.route("/api/admin/users/<path:email>", methods=["PUT"])
@require_admin
def api_admin_update_user(email):
    return _admin_update_user(email, request.get_json(silent=True) or {})


@app.route("/api/admin/users/<path:email>", methods=["DELETE"])
@require_admin
def api_admin_delete_user(email):
    return _admin_delete_user(email)


# ─────────────────────────────────────────────
# Per-user settings (provider keys, models, prefs)
# ─────────────────────────────────────────────

# Cap the stored settings blob so a bad client can't fill the DB.
_MAX_SETTINGS_BYTES = 64 * 1024


@app.route("/api/settings", methods=["GET"])
@require_user
def api_get_settings():
    email = request.identity.get("email")
    try:
        return jsonify({"success": True, "settings": STORE.get_settings(email)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not load settings: {e}"}), 500


@app.route("/api/settings", methods=["POST"])
@require_user
def api_save_settings():
    email = request.identity.get("email")
    data = request.get_json(silent=True) or {}
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return jsonify({"error": "Invalid settings payload."}), 400
    if len(json.dumps(settings)) > _MAX_SETTINGS_BYTES:
        return jsonify({"error": "Settings payload too large."}), 413
    try:
        STORE.save_settings(email, settings)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not save settings: {e}"}), 500
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# Per-user saved resumes
# ─────────────────────────────────────────────

@app.route("/api/resumes", methods=["GET"])
@require_user
def api_list_resumes():
    email = request.identity.get("email")
    try:
        # List view is lightweight metadata only; full data fetched on demand.
        resumes = STORE.list_resumes(email)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not load resumes: {e}"}), 500
    summaries = [{
        "id": r.get("id"),
        "created_at": r.get("created_at"),
        "name": r.get("name", ""),
        "title": r.get("title", ""),
        "company": r.get("company", ""),
        "score": (r.get("ats") or {}).get("score") if isinstance(r.get("ats"), dict) else None,
        "job_url": r.get("job_url", ""),
        "status": r.get("status", ""),
        "applied_at": r.get("applied_at", ""),
    } for r in resumes]
    # Every open tab of this account polls this endpoint; the version lets an unchanged
    # list come back as a tiny "unchanged" reply instead of the whole list.
    version = hashlib.sha1(json.dumps(summaries, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    if request.args.get("v") == version:
        return jsonify({"success": True, "unchanged": True, "version": version})
    return jsonify({"success": True, "resumes": summaries, "version": version})


def _clean_job_url(url) -> str:
    """Optional link to the job posting. Only http(s) — it ends up behind an Apply button."""
    url = (url or "").strip() if isinstance(url, str) else ""
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url if re.match(r"^https?://[^\s/]+\.[^\s]+$", url, re.I) else ""


@app.route("/api/resumes", methods=["POST"])
@require_user
def api_save_resume():
    email = request.identity.get("email")
    data = request.get_json(silent=True) or {}
    resume_data = data.get("data")
    if not isinstance(resume_data, dict):
        return jsonify({"error": "Missing resume data."}), 400
    record = {
        "name": (data.get("name") or resume_data.get("name") or "").strip(),
        "title": (data.get("title") or resume_data.get("title") or "").strip(),
        "company": (data.get("company") or "").strip(),
        "jd": data.get("jd") or "",
        "data": resume_data,
        "ats": data.get("ats") or None,
        "job_url": _clean_job_url(data.get("job_url")),
    }
    try:
        # One resume per (company + job title): if a resume for this exact company AND
        # title already exists, REPLACE it (reuse its id so add_resume overwrites) instead
        # of adding a duplicate. Different titles at the same company stay as separate resumes.
        company = record["company"].strip().lower()
        title = record["title"].strip().lower()
        if company or title:
            existing = [r for r in STORE.list_resumes(email)
                        if (r.get("company") or "").strip().lower() == company
                        and (r.get("title") or "").strip().lower() == title]
            if existing:
                record["id"] = existing[0]["id"]        # overwrite the first match
                for extra in existing[1:]:              # clean up any older duplicates
                    try:
                        STORE.delete_resume(email, extra["id"])
                    except Exception:
                        pass
        saved = STORE.add_resume(email, record)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not save resume: {e}"}), 500
    return jsonify({"success": True, "id": saved["id"], "created_at": saved["created_at"]})


@app.route("/api/resumes/bulk-delete", methods=["POST"])
@require_user
def api_bulk_delete_resumes():
    """Delete many of the user's resumes at once: {"ids": [...]} or {"all": true}."""
    email = request.identity.get("email")
    data = request.get_json(silent=True) or {}
    try:
        if data.get("all"):
            if hasattr(STORE, "clear_resumes"):
                STORE.clear_resumes(email)
            else:
                for r in STORE.list_resumes(email):
                    STORE.delete_resume(email, r.get("id"))
        else:
            ids = data.get("ids")
            if not isinstance(ids, list):
                return jsonify({"error": "Provide an 'ids' list or 'all': true."}), 400
            for rid in ids:
                if rid:
                    STORE.delete_resume(email, str(rid))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not delete resumes: {e}"}), 500
    return jsonify({"success": True})


@app.route("/api/resumes/<resume_id>", methods=["GET"])
@require_user
def api_get_resume(resume_id):
    email = request.identity.get("email")
    try:
        record = STORE.get_resume(email, resume_id)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not load resume: {e}"}), 500
    if not record:
        return jsonify({"error": "Resume not found."}), 404
    return jsonify({"success": True, "resume": record})


RESUME_STATUSES = ("", "applied", "closed")


@app.route("/api/resumes/<resume_id>", methods=["PATCH"])
@require_user
def api_update_resume_status(resume_id):
    """Set a saved resume's application status. `applied_at` is stamped the first time
    it is marked Applied and kept if the job later closes, so date filters still count it."""
    email = request.identity.get("email")
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in RESUME_STATUSES:
        return jsonify({"error": "Status must be applied, closed, or empty."}), 400
    try:
        record = STORE.get_resume(email, resume_id)
        if not record:
            return jsonify({"error": "Resume not found."}), 404
        record["status"] = status
        if status == "applied" and not record.get("applied_at"):
            record["applied_at"] = datetime.now(timezone.utc).isoformat()
        elif status == "":
            record.pop("applied_at", None)
        STORE.add_resume(email, record)   # same id -> overwrites in place
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not update resume: {e}"}), 500
    return jsonify({"success": True, "status": status, "applied_at": record.get("applied_at", "")})


@app.route("/api/resumes/<resume_id>", methods=["DELETE"])
@require_user
def api_delete_resume(resume_id):
    email = request.identity.get("email")
    try:
        existed = STORE.delete_resume(email, resume_id)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not delete resume: {e}"}), 500
    if not existed:
        return jsonify({"error": "Resume not found."}), 404
    return jsonify({"success": True})


@app.route("/api/scrape-jd", methods=["POST"])
@require_user
def api_scrape_jd():
    """Scrape a job description from a URL using Apify website-content-crawler."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided."}), 400

        apify_token = data.get("apify_token", "").strip()
        url = data.get("url", "").strip()

        if not apify_token:
            return jsonify({"error": "Apify API token is required."}), 400
        if not url:
            return jsonify({"error": "Job URL is required."}), 400

        # Call Apify website-content-crawler actor
        actor_id = "apify~website-content-crawler"
        run_url = f"https://api.apify.com/v2/acts/{actor_id}/runs"

        run_input = {
            "startUrls": [{"url": url}],
            "maxCrawlPages": 1,
            "crawlerType": "playwright:firefox",
            "maxConcurrency": 1,
            "proxyConfiguration": {"useApifyProxy": True},
        }

        # Start the actor run synchronously (wait for finish)
        resp = requests.post(
            run_url,
            params={"token": apify_token, "waitForFinish": 120},
            json=run_input,
            timeout=180,
        )

        if resp.status_code == 401:
            return jsonify({"error": "Invalid Apify API token."}), 401
        if not resp.ok:
            return jsonify({"error": f"Apify API error: {resp.status_code} {resp.text[:200]}"}), 502

        run_data = resp.json().get("data", {})
        run_status = run_data.get("status")

        if run_status != "SUCCEEDED":
            return jsonify({"error": f"Apify crawl did not succeed (status: {run_status}). Try again."}), 502

        # Fetch results from the default dataset
        dataset_id = run_data.get("defaultDatasetId")
        if not dataset_id:
            return jsonify({"error": "No dataset returned from Apify."}), 502

        items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        items_resp = requests.get(
            items_url,
            params={"token": apify_token, "format": "json"},
            timeout=30,
        )

        if not items_resp.ok:
            return jsonify({"error": "Failed to fetch crawl results."}), 502

        items = items_resp.json()
        if not items:
            return jsonify({"error": "No content was extracted from the URL. The page may require login."}), 404

        item = items[0]
        page_text = item.get("text", "")
        page_title = item.get("metadata", {}).get("title", "") or item.get("title", "")

        if not page_text:
            return jsonify({"error": "Page was loaded but no text content was found."}), 404

        metadata = extract_job_metadata(url, page_title, page_text)

        return jsonify({
            "success": True,
            "text": page_text,
            "title": page_title,
            "metadata": metadata,
        })

    except requests.Timeout:
        return jsonify({"error": "Apify request timed out. Try again."}), 504
    except Exception as e:
        return jsonify({"error": f"Scraping failed: {str(e)}"}), 500


@app.route("/api/tailor", methods=["POST"])
@require_user
def api_tailor():
    try:
        provider = request.form.get("provider", "anthropic").strip().lower()
        if provider == "claude":
            provider = "anthropic"
        api_key = request.form.get("api_key", "").strip()
        model = request.form.get("model", "").strip() or None
        base_url = request.form.get("base_url", "").strip() or None
        # Local providers (Ollama, openai_compatible pointing at localhost) often don't need a key.
        is_local = provider == "ollama" or (provider == "openai_compatible" and base_url and "localhost" in base_url)
        if not api_key and not is_local:
            return jsonify({"error": f"{provider.title()} API key is required."}), 400

        jd_text = request.form.get("jd", "").strip()
        resume_text_direct = request.form.get("resume_text", "").strip()

        # Parse prompt from file or text
        prompt_text = request.form.get("prompt", "").strip()
        if "prompt_file" in request.files:
            pf = request.files["prompt_file"]
            if pf.filename:
                pext = os.path.splitext(pf.filename)[1].lower()
                if pext not in PARSERS:
                    return jsonify({"error": f"Unsupported prompt file type: {pext}. Use .txt, .docx, or .pdf"}), 400
                prompt_text = PARSERS[pext](pf.read())

        if not prompt_text:
            prompt_text = DEFAULT_TAILORING_PROMPT
        if not jd_text:
            return jsonify({"error": "Job description is required."}), 400

        # Parse resume from file or text
        resume_text = ""
        personal_info = None
        if "resume_file" in request.files:
            f = request.files["resume_file"]
            if f.filename:
                ext = os.path.splitext(f.filename)[1].lower()
                if ext not in PARSERS:
                    return jsonify({"error": f"Unsupported file type: {ext}. Use .docx, .pdf, or .txt"}), 400
                file_bytes = f.read()
                resume_text = PARSERS[ext](file_bytes)
                # Extract original personal info from DOCX to preserve it
                if ext == ".docx":
                    personal_info = extract_personal_info_docx(file_bytes)

        if not resume_text and resume_text_direct:
            resume_text = resume_text_direct

        if not resume_text:
            return jsonify({"error": "Please upload a resume file or paste resume text."}), 400

        # Extract personal info from the plain text and use it to fill any gaps.
        # (DOCX resumes without a header table return nothing from extract_personal_info_docx,
        # so without this merge the AI's version of name/phone/LinkedIn/location would leak in.)
        text_info = extract_personal_info_text(resume_text)
        if not personal_info:
            personal_info = text_info
        else:
            if not personal_info.get("name"):
                personal_info["name"] = text_info.get("name", "")
            if not personal_info.get("contact"):
                personal_info["contact"] = text_info.get("contact", "")

        # Detect sections from the original resume to preserve structure
        detected_sections = detect_resume_sections(resume_text)

        # Extract original title from resume text (first few lines, look for a title-like line)
        original_title = _extract_original_title(resume_text)

        result = tailor_resume(
            api_key, resume_text, prompt_text, jd_text, provider, detected_sections,
            model=model, base_url=base_url,
        )
        job_meta = _pop_job_meta(result)
        _strip_bogus_language_section(result)

        # Force-override personal info with original values (never let AI change these)
        if personal_info:
            if personal_info.get("name"):
                result["name"] = personal_info["name"]
            if personal_info.get("contact"):
                result["contact"] = personal_info["contact"]

        # Force-override title with original (never let AI change the title)
        if original_title:
            result["title"] = original_title

        # Detect the "model didn't actually tailor" case so the UI can warn the user.
        effective_model = (model or PROVIDER_DEFAULTS.get(provider, "")) or "(provider default)"
        warning = ""
        try:
            if _experience_mostly_verbatim(result, resume_text):
                warning = (f"The AI returned your experience almost unchanged — it did not tailor it. "
                           f"The model used was '{effective_model}'. If that is a low-cost model "
                           f"(nano / mini / lite / 8b), switch to a standard one and tailor again.")
        except Exception:
            pass
        ats_score = _ats_score(result, _ats_keywords(jd_text, resume_text))

        return jsonify({"success": True, "data": result, "job_meta": job_meta,
                        "warning": warning, "model_used": effective_model,
                        "ats_score": ats_score})

    except ValueError as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return _provider_error_response(e)


@app.route("/api/answer-questions", methods=["POST"])
@require_user
def api_answer_questions():
    """Answer job application questions using JD + resume context.

    Two callers: a saved/tailored resume sends JSON with `resume` (structured data);
    the front-page panel sends multipart with `resume_file` (the user's base resume)."""
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        if not data:
            return jsonify({"error": "No data provided."}), 400

        provider = data.get("provider", "anthropic").strip().lower()
        if provider == "claude":
            provider = "anthropic"
        api_key = data.get("api_key", "").strip()
        model = (data.get("model", "") or "").strip() or None
        base_url = (data.get("base_url", "") or "").strip() or None
        questions = data.get("questions", "").strip()
        jd_text = (data.get("jd", "") or "").strip()
        resume_json = data.get("resume") if isinstance(data.get("resume"), dict) else {}
        file_resume_text = ""
        if "resume_file" in request.files and request.files["resume_file"].filename:
            f = request.files["resume_file"]
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in PARSERS:
                return jsonify({"error": f"Unsupported file type: {ext}. Use .docx, .pdf, or .txt"}), 400
            file_resume_text = PARSERS[ext](f.read())

        is_local = provider == "ollama" or (provider == "openai_compatible" and base_url and "localhost" in base_url)
        if not api_key and not is_local:
            return jsonify({"error": "API key is required."}), 400
        if not questions:
            return jsonify({"error": "Please paste at least one question."}), 400
        if not resume_json and not file_resume_text:
            return jsonify({"error": "Resume context is missing. Upload your resume first."}), 400
        if not jd_text:
            jd_text = "(No job description provided — answer from the resume alone.)"

        # Rebuild a readable resume summary from the structured data
        resume_lines = []
        resume_lines.append(f"Name: {resume_json.get('name', '')}")
        resume_lines.append(f"Title: {resume_json.get('title', '')}")

        if "sections" in resume_json:
            # New dynamic sections format
            for section in resume_json["sections"]:
                heading = section.get("heading", "")
                sec_type = section.get("type", "")
                resume_lines.append(f"\n{heading}:")
                if sec_type == "text":
                    resume_lines.append(section.get("content", ""))
                elif sec_type == "skills":
                    for s in section.get("items", []):
                        resume_lines.append(f"  {s.get('category', '')}: {s.get('items', '')}")
                elif sec_type == "experience":
                    for job in section.get("items", []):
                        resume_lines.append(f"\n  {job.get('job_title', '')} at {job.get('company', '')} ({job.get('dates', '')})")
                        for b in job.get("bullets", []):
                            resume_lines.append(f"    - {b}")
                elif sec_type == "education":
                    items = section.get("items", [])
                    if isinstance(items, dict):
                        items = [items]
                    for edu in items:
                        resume_lines.append(f"  {edu.get('degree', '')} — {edu.get('school', '')}")
                elif sec_type == "projects":
                    for proj in section.get("items", []):
                        resume_lines.append(f"\n  {proj.get('name', '')}")
                        for b in proj.get("bullets", []):
                            resume_lines.append(f"    - {b}")
                else:
                    for item in section.get("items", []):
                        if isinstance(item, str):
                            resume_lines.append(f"  - {item}")
                        elif isinstance(item, dict):
                            resume_lines.append(f"  - {item.get('name', '') or item.get('title', '')}")
        else:
            # Old fixed-schema format (backward compat)
            resume_lines.append(f"\nSummary:\n{resume_json.get('summary', '')}")
            resume_lines.append("\nSkills:")
            for s in resume_json.get("skills", []):
                resume_lines.append(f"  {s.get('category', '')}: {s.get('items', '')}")
            resume_lines.append("\nExperience:")
            for job in resume_json.get("experience", []):
                resume_lines.append(f"\n  {job.get('job_title', '')} at {job.get('company', '')} ({job.get('dates', '')})")
                for b in job.get("bullets", []):
                    resume_lines.append(f"    - {b}")
            edu = resume_json.get("education", {})
            if edu:
                resume_lines.append(f"\nEducation: {edu.get('degree', '')} — {edu.get('school', '')}")
        resume_text = "\n".join(resume_lines).replace("[[", "").replace("]]", "")
        if file_resume_text:
            resume_text = file_resume_text

        system_prompt = """You are an expert job application assistant. You help candidates write compelling,
authentic answers to job application questions. You have deep context about:
1. The candidate's background (their tailored resume)
2. The specific job they are applying for (the job description)

Rules:
- Write answers in FIRST PERSON as the candidate
- Keep answers concise but substantive (3-6 sentences per question unless it clearly needs more)
- Ground every answer in REAL experience from the resume — never fabricate
- Mirror the tone and keywords from the job description naturally
- Show enthusiasm for the specific role and company
- If a question asks about something not covered in the resume, craft an honest answer
  that pivots to relevant strengths rather than making things up
- For salary questions, suggest the candidate research market rates rather than giving a number
- For "why this company" questions, reference specific things from the JD that align with the candidate's experience"""

        user_msg = f"""Here is the candidate's resume:

{resume_text}

Here is the job description they are applying to:

{jd_text}

Please answer each of the following application questions. Format your response as a JSON array where each
element has "question" (the original question) and "answer" (your crafted response).

Questions:
{questions}

Return ONLY a JSON array, no markdown code fences, no extra text. Example format:
[{{"question": "Why do you want this role?", "answer": "Your answer here..."}}]"""

        full_prompt = f"{system_prompt}\n\n{user_msg}"
        # Note: Q&A returns a JSON array, but json_mode (response_format=json_object) on OpenAI
        # requires an object at the top level. Wrap accordingly: we ask for {"answers": [...]} when json_mode.
        is_openai_compat = provider in OPENAI_COMPATIBLE_BASE_URLS or provider == "openai_compatible"
        if is_openai_compat:
            full_prompt = full_prompt.replace(
                'Return ONLY a JSON array, no markdown code fences, no extra text. Example format:\n[{"question": "Why do you want this role?", "answer": "Your answer here..."}]',
                'Return ONLY a JSON object with an "answers" key. Example format:\n{"answers": [{"question": "Why do you want this role?", "answer": "Your answer here..."}]}',
            )
        raw = call_ai(provider, api_key, full_prompt, max_tokens=4000, model=model, base_url=base_url, json_mode=True)
        raw = _strip_code_fences(raw)

        try:
            parsed = _safe_json_loads(raw)
        except json.JSONDecodeError as e:
            return jsonify({"error": f"AI returned invalid JSON even after repair. First 500 chars: {raw[:500]!r}"}), 502
        # Accept either a bare array or {"answers": [...]}
        answers = parsed["answers"] if isinstance(parsed, dict) and "answers" in parsed else parsed
        if not isinstance(answers, list):
            return jsonify({"error": "AI response was not a list of answers."}), 502
        return jsonify({"success": True, "answers": answers})

    except ValueError as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return _provider_error_response(e)


@app.route("/api/download-pdf", methods=["POST"])
@require_user
def api_download_pdf():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No resume data provided."}), 400

        template = data.pop("template", "modern_green")
        pdf_bytes = generate_pdf(data, template=template)
        name_slug = data.get("name", "resume").replace(" ", "_")
        filename = f"{name_slug}.pdf"

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500



if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(debug=debug, port=5000)
