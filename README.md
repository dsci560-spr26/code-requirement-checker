# Code-Requirement Checker

AI-powered system that reads git commits and checks if code changes match PM requirements. Uses Claude AI for intelligent code-requirement matching.

## Architecture

```
Git Commits → FastAPI Backend → Claude AI Analysis → Structured Feedback
                    ↑                                        ↓
            PM Requirements                          Developer Dashboard
            (CRUD API)                               (Vue.js Frontend)
```

## Quick Start

### 1. Setup
```bash
cd backend
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### 2. Install & Run
```bash
# Option A: Use the run script
chmod +x run.sh
./run.sh

# Option B: Manual
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000

# In another terminal:
cd frontend
python -m http.server 3000
```

### 3. Open
- Frontend: http://localhost:3000
- API Docs: http://localhost:8000/docs

## Features (Milestone 2 — 30-50%)

### Working Now
- [x] PM requirement management (CRUD)
- [x] Manual diff paste + analysis
- [x] Git repo integration (read real commits)
- [x] AI-powered code-requirement matching via Claude
- [x] Structured feedback: match / partial / gap / needs clarification
- [x] Per-requirement evidence, gaps, and suggestions
- [x] PM action items generation
- [x] Analysis history

### Planned (Milestone 3)
- [ ] GitHub/GitLab webhook integration
- [ ] Jira/Linear requirement sync
- [ ] Slack/Teams notifications
- [ ] Dashboard with trend analytics
- [ ] Multi-language support (JS, Go, Rust, etc.)
- [ ] Learning system (improves matching over time)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/requirements` | List all requirements |
| POST | `/api/requirements` | Add a requirement |
| PUT | `/api/requirements/{id}` | Update a requirement |
| DELETE | `/api/requirements/{id}` | Delete a requirement |
| POST | `/api/analyze` | Analyze a pasted diff |
| POST | `/api/analyze/git` | Analyze from git repo |
| GET | `/api/history` | Get analysis history |

## Tech Stack
- **Backend**: Python, FastAPI, Anthropic SDK
- **Frontend**: Vue.js 3 (CDN), vanilla CSS
- **AI**: Claude Sonnet for code analysis
