# AI-Powered Job Search & Application Agent

Production-ready job search agent for Android/Mobile developer roles, tailored to your resume.

## Features

- **Multi-source job discovery** — Adzuna, JSearch (Indeed/LinkedIn), Remotive, Arbeitnow, Greenhouse
- **Resume-aware matching** — Transparent 0–100 scoring across skills, experience, seniority, location
- **Truthful resume tailoring** — LLM-powered customization that never fabricates experience
- **LaTeX/PDF generation** — ATS-friendly resumes compilable in Overleaf
- **Excel reports** — Daily `android_jobs_YYYY-MM-DD.xlsx` with clickable links
- **Application tracker** — Full lifecycle from DISCOVERED → APPLIED → INTERVIEW → OFFER
- **Daily automation** — Scheduled morning search with notifications
- **Dashboard UI** — Review jobs, approve applications, download resumes

## Brave Browser Discovery (Logged-In Portals)

The agent uses your **Brave "Switch" profile** (`Profile 2`) for read-only job discovery on logged-in portals: LinkedIn, Indeed, Naukri, Wellfound, Instahyre, Cutshort.

**The browser is read-only** — it never clicks Apply/Submit, never modifies your profile, and never exports cookies or credentials.

### Setup (one-time)

Because Brave locks profiles when open, you must launch with remote debugging:

```bash
# Step 1: Fully quit Brave (Cmd+Q — all windows)
# Step 2: Launch Switch profile with debugging
./scripts/start_brave_debug.sh

# Step 3: In another terminal, run discovery
source .venv/bin/activate
python scripts/run_browser_discovery.py
```

Alternatively, set in `.env`:
```
BRAVE_CDP_URL=http://127.0.0.1:9222
```

### If Brave is already open

You cannot attach to an existing Brave session without the debug flag. Either:
1. **Quit Brave completely** (Cmd+Q) and relaunch via `start_brave_debug.sh`, OR
2. Close only the Switch profile windows and run the script

### What the browser collects

- Job title, company, location, salary, posting date
- Full job description
- Direct application URL (never clicks it)
- Skips CAPTCHA/MFA/OTP pages automatically

## Quick Start

### 1. Install dependencies

```bash
cd /Users/abhishekpandey/Downloads/job
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys (see below)
```

**Minimum required for job search:**
- `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` — [developer.adzuna.com](https://developer.adzuna.com/) (free, India supported)
- OR `RAPIDAPI_KEY` — [JSearch on RapidAPI](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)

**Optional but recommended:**
- `GEMINI_API_KEY` — Gemini 3.8 Flash for match explanations, resume tailoring, cover letters
- `SERPAPI_KEY` — Google Jobs search
- `GREENHOUSE_BOARDS` — Comma-separated company slugs (e.g. `stripe,airbnb`)

### 3. Run job search

```bash
python -m job_agent search
```

Output appears in `./output/YYYY-MM-DD/`:
```
output/2026-09-02/
  android_jobs_2026-09-02.xlsx
  resumes/
    company_name/
      resume.tex
      resume.pdf
      cover_letter.txt
```

### 4. Start API + Dashboard

```bash
# Terminal 1: API server + daily scheduler
python -m job_agent serve

# Terminal 2: Dashboard
cd frontend && npm install && npm run dev
```

Open http://localhost:5173

## Architecture

```
Job Discovery (Adzuna, JSearch, Remotive, Arbeitnow, Greenhouse)
    ↓
Normalization & Deduplication
    ↓
Android/Mobile Relevance Filter
    ↓
Location Filter (Mumbai → India → Remote India-eligible)
    ↓
Recency Filter (24 hours)
    ↓
Match Scoring (deterministic + LLM enhancement)
    ↓
Resume Tailoring (LLM, truth-constrained)
    ↓
LaTeX/PDF Generation
    ↓
Excel Export + Database Persistence
    ↓
Dashboard + Notifications
```

## Match Scoring

| Category | Weight |
|----------|--------|
| Technical Skills | 35 pts |
| Experience | 20 pts |
| Role Alignment | 20 pts |
| Seniority | 10 pts |
| Location | 10 pts |
| Industry/Domain | 5 pts |

| Score | Category |
|-------|----------|
| 90–100 | Excellent Match |
| 80–89 | Strong Match |
| 70–79 | Good Match |
| 60–69 | Moderate Match |
| < 60 | Weak Match |

## Configuration

All settings in `.env` or via dashboard `/api/config`:

| Setting | Default | Description |
|---------|---------|-------------|
| `RESUME_GENERATION_THRESHOLD` | 75 | Min score to generate tailored resume |
| `TOP_JOBS_COUNT` | 30 | Jobs in daily Excel report |
| `JOB_RECENCY_HOURS` | 24 | Only jobs posted within this window |
| `DAILY_SEARCH_TIME` | 07:00 | Morning search time (Asia/Kolkata) |
| `REQUIRE_APPROVAL_BEFORE_APPLY` | true | Manual approval before applying |

## Tests

```bash
pytest tests/ -v
```

## Resume Source of Truth

Your master resume is at `data/resume/master_resume.json`, extracted from your PDF. The system will **never** invent experience, technologies, or achievements. Tailored resumes only reorder and rephrase existing content.

## Application Workflow

```
DISCOVERED → ANALYZING → MATCHED → RESUME_GENERATED → READY_TO_APPLY
    → APPLICATION_STARTED → APPLIED → REJECTED / INTERVIEW → OFFER
```

Applications require manual approval by default. Use the dashboard "Approve & Apply" button to review before submission.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/dashboard/stats` | Dashboard statistics |
| GET | `/api/jobs` | List jobs with filters |
| GET | `/api/jobs/{id}` | Job details |
| POST | `/api/pipeline/run` | Trigger job search |
| POST | `/api/jobs/{id}/apply` | Approve & start application |
| GET | `/api/resume/master` | View master resume |
| GET | `/api/resume/download/{id}` | Download tailored resume |

## Security

- No credentials in code — all via `.env`
- `.env` is gitignored
- No CAPTCHA bypass or ToS violations
- Respects rate limits on all APIs
