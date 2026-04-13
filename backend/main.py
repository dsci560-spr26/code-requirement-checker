"""
Code-Requirement Checker — FastAPI Backend
AI-powered PM-Engineer collaboration platform.
Projects → Requirements (Kanban) → Group Chat with AI analysis.
"""

import os
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="Code-Requirement Checker",
    description="AI-powered PM-Engineer collaboration platform",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)


# ── Pydantic Models ──────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    background: str


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    background: Optional[str] = None


class RequirementCreate(BaseModel):
    title: str
    description: str
    priority: str = "medium"


class RequirementUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None


class ChatSendRequest(BaseModel):
    role: str  # "pm" or "engineer"
    content: str


class GitAnalyzeRequest(BaseModel):
    repo_path: str
    commit_sha: Optional[str] = None


# ── In-memory Storage ────────────────────────────────────────────────

projects_db: list[dict] = []
requirements_db: list[dict] = []
messages_db: list[dict] = []


# ── Seed sample data ─────────────────────────────────────────────────

def seed_data():
    sample_project = {
        "id": "PROJ-001",
        "name": "User System v2",
        "background": (
            "Our current login system only supports email/password. "
            "Users have been requesting OAuth support (Google, GitHub). "
            "We need to rebuild the auth module with JWT tokens, add OAuth providers, "
            "and implement a proper password reset flow. "
            "The goal is to reduce sign-up friction and improve security."
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    projects_db.append(sample_project)

    sample_reqs = [
        {
            "id": "REQ-001",
            "project_id": "PROJ-001",
            "title": "JWT Authentication",
            "description": (
                "Implement JWT-based login/logout with email + password. "
                "Must include input validation, password hashing (bcrypt), "
                "and return a 401 on invalid credentials."
            ),
            "priority": "high",
            "status": "todo",
        },
        {
            "id": "REQ-002",
            "project_id": "PROJ-001",
            "title": "Google OAuth Integration",
            "description": (
                "Add Google OAuth 2.0 login flow. User clicks 'Sign in with Google', "
                "gets redirected to Google consent screen, and returns with an auth token. "
                "Must create a new user record if first-time login."
            ),
            "priority": "high",
            "status": "todo",
        },
        {
            "id": "REQ-003",
            "project_id": "PROJ-001",
            "title": "Password Reset Flow",
            "description": (
                "Implement forgot password flow: user enters email, receives a reset link "
                "with a time-limited token (15 min expiry), clicks link to set a new password. "
                "Must validate token and enforce password strength rules."
            ),
            "priority": "medium",
            "status": "todo",
        },
    ]
    requirements_db.extend(sample_reqs)


seed_data()


# ── Helper: generate IDs ────────────────────────────────────────────

_proj_counter = 1
_req_counters: dict[str, int] = {"PROJ-001": 3}


def next_project_id() -> str:
    global _proj_counter
    _proj_counter += 1
    return f"PROJ-{_proj_counter:03d}"


def next_requirement_id(project_id: str) -> str:
    count = _req_counters.get(project_id, 0) + 1
    _req_counters[project_id] = count
    return f"REQ-{count:03d}"


def make_message(project_id: str, role: str, content: str, analysis_result: dict = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analysis_result": analysis_result,
    }


# ── Helper: git diff ────────────────────────────────────────────────

def get_git_diff(repo_path: str, commit_sha: Optional[str] = None) -> dict:
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"Not a git repository: {repo}")
    try:
        if commit_sha:
            diff = subprocess.check_output(
                ["git", "diff", f"{commit_sha}~1", commit_sha],
                cwd=str(repo), text=True,
            )
            msg = subprocess.check_output(
                ["git", "log", "-1", "--format=%s", commit_sha],
                cwd=str(repo), text=True,
            ).strip()
        else:
            diff = subprocess.check_output(
                ["git", "diff", "HEAD~1", "HEAD"],
                cwd=str(repo), text=True,
            )
            msg = subprocess.check_output(
                ["git", "log", "-1", "--format=%s"],
                cwd=str(repo), text=True,
            ).strip()
        return {"diff": diff, "commit_message": msg}
    except subprocess.CalledProcessError as e:
        raise ValueError(f"Git error: {e}")


# ── Helper: detect code in message ──────────────────────────────────

def looks_like_code(text: str) -> bool:
    code_indicators = ["diff --git", "+++", "---", "```", "def ", "function ", "class ", "import "]
    text_lower = text.lower()
    hits = sum(1 for ind in code_indicators if ind.lower() in text_lower)
    return hits >= 2 or "diff --git" in text


# ── AI Prompts ───────────────────────────────────────────────────────

ANALYSIS_PROMPT = """You are a senior engineering lead reviewing code changes for the project "{project_name}".

## Project Background:
{project_background}

## Requirements to check against:
{requirements}

## Git Diff / Code:
```
{diff}
```

## Commit Message: {commit_message}

## Your Task:
Analyze whether this code change fulfills, partially fulfills, or misses the requirements listed above.

For EACH requirement, provide:
1. **status**: one of "match" (fully addressed), "partial" (some aspects addressed), "gap" (not addressed), or "clarification_needed" (intent unclear)
2. **confidence**: 0-100 score
3. **evidence**: specific lines/patterns from the diff
4. **gaps**: what's missing if not "match"
5. **suggestions**: actionable next steps

Respond in this exact JSON format:
{{
  "summary": "one-line overall assessment",
  "overall_score": <0-100>,
  "requirements": [
    {{
      "requirement_id": "REQ-XXX",
      "requirement_title": "...",
      "status": "match|partial|gap|clarification_needed",
      "confidence": <0-100>,
      "evidence": ["line or pattern 1", "line or pattern 2"],
      "gaps": ["missing thing 1"],
      "suggestions": ["do X"]
    }}
  ],
  "code_quality_notes": ["observations"],
  "pm_action_items": ["things PM should clarify"]
}}

Be specific. Reference actual code from the diff."""


CHAT_PROMPT = """You are an AI assistant in a project team chat for "{project_name}".
You collaborate with the PM and Engineers. Be concise and helpful.

## Project Background:
{project_background}

## Current Requirements:
{requirements}

## Recent Chat History:
{chat_history}

## New message from {role}:
{content}

Respond helpfully. Reference requirement IDs (e.g., REQ-001) when relevant.
If the PM asks about progress, summarize based on requirement statuses.
If the engineer asks technical questions, give concrete suggestions.
Keep responses concise — this is a chat, not an essay."""


# ── Core: AI calls ───────────────────────────────────────────────────

def call_ai(prompt: str) -> str:
    response = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def run_analysis(project: dict, reqs: list[dict], diff: str, commit_message: str) -> dict:
    reqs_text = "\n".join(
        f"- [{r['id']}] {r['title']} (Priority: {r['priority']}, Status: {r['status']}): {r['description']}"
        for r in reqs
    )
    prompt = ANALYSIS_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"],
        requirements=reqs_text,
        diff=diff[:15000],
        commit_message=commit_message or "(no commit message)",
    )
    text = call_ai(prompt)
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"summary": text, "overall_score": 0, "requirements": []}


def run_chat(project: dict, reqs: list[dict], messages: list[dict], role: str, content: str) -> str:
    reqs_text = "\n".join(
        f"- [{r['id']}] {r['title']} (Status: {r['status']}, Priority: {r['priority']}): {r['description']}"
        for r in reqs
    )
    recent = messages[-20:] if len(messages) > 20 else messages
    history_text = "\n".join(
        f"[{m['role'].upper()}]: {m['content'][:300]}" for m in recent
    )
    prompt = CHAT_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"],
        requirements=reqs_text,
        chat_history=history_text or "(no prior messages)",
        role=role,
        content=content,
    )
    return call_ai(prompt)


# ── Helper: sync kanban statuses after analysis ─────────────────────

def sync_requirement_statuses(project_id: str, analysis_result: dict):
    status_map = {"match": "done", "partial": "in_progress", "gap": "todo"}
    rank = {"todo": 0, "in_progress": 1, "done": 2}

    for req_result in analysis_result.get("requirements", []):
        ai_status = req_result.get("status", "")
        confidence = req_result.get("confidence", 0)
        new_status = status_map.get(ai_status)
        if not new_status:
            continue
        if ai_status == "match" and confidence < 70:
            new_status = "in_progress"

        req_id = req_result.get("requirement_id", "")
        for r in requirements_db:
            if r["project_id"] == project_id and r["id"] == req_id:
                if rank.get(new_status, 0) > rank.get(r["status"], 0):
                    r["status"] = new_status
                break


# ── API: Projects ────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "running", "service": "Code-Requirement Checker v0.2.0"}


@app.get("/api/projects")
def list_projects():
    return {"projects": projects_db}


@app.post("/api/projects")
def create_project(body: ProjectCreate):
    project = {
        "id": next_project_id(),
        "name": body.name,
        "background": body.background,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    projects_db.append(project)
    return project


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    for p in projects_db:
        if p["id"] == project_id:
            reqs = [r for r in requirements_db if r["project_id"] == project_id]
            return {**p, "requirements": reqs}
    raise HTTPException(404, "Project not found")


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate):
    for p in projects_db:
        if p["id"] == project_id:
            if body.name is not None:
                p["name"] = body.name
            if body.background is not None:
                p["background"] = body.background
            return p
    raise HTTPException(404, "Project not found")


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    for i, p in enumerate(projects_db):
        if p["id"] == project_id:
            projects_db.pop(i)
            # Clean up related data
            requirements_db[:] = [r for r in requirements_db if r["project_id"] != project_id]
            messages_db[:] = [m for m in messages_db if m["project_id"] != project_id]
            return {"message": "Deleted"}
    raise HTTPException(404, "Project not found")


# ── API: Requirements (scoped to project) ────────────────────────────

@app.get("/api/projects/{project_id}/requirements")
def list_requirements(project_id: str):
    reqs = [r for r in requirements_db if r["project_id"] == project_id]
    return {"requirements": reqs}


@app.post("/api/projects/{project_id}/requirements")
def add_requirement(project_id: str, body: RequirementCreate):
    if not any(p["id"] == project_id for p in projects_db):
        raise HTTPException(404, "Project not found")
    req = {
        "id": next_requirement_id(project_id),
        "project_id": project_id,
        "title": body.title,
        "description": body.description,
        "priority": body.priority,
        "status": "todo",
    }
    requirements_db.append(req)
    return req


@app.put("/api/projects/{project_id}/requirements/{req_id}")
def update_requirement(project_id: str, req_id: str, body: RequirementUpdate):
    for r in requirements_db:
        if r["project_id"] == project_id and r["id"] == req_id:
            if body.title is not None:
                r["title"] = body.title
            if body.description is not None:
                r["description"] = body.description
            if body.priority is not None:
                r["priority"] = body.priority
            if body.status is not None:
                r["status"] = body.status
            return r
    raise HTTPException(404, "Requirement not found")


@app.delete("/api/projects/{project_id}/requirements/{req_id}")
def delete_requirement(project_id: str, req_id: str):
    for i, r in enumerate(requirements_db):
        if r["project_id"] == project_id and r["id"] == req_id:
            requirements_db.pop(i)
            return {"message": "Deleted"}
    raise HTTPException(404, "Requirement not found")


# ── API: Chat ────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/chat")
def get_chat(project_id: str):
    msgs = [m for m in messages_db if m["project_id"] == project_id]
    return {"messages": msgs}


@app.post("/api/projects/{project_id}/chat")
def send_chat(project_id: str, body: ChatSendRequest):
    # Find project
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    reqs = [r for r in requirements_db if r["project_id"] == project_id]
    proj_messages = [m for m in messages_db if m["project_id"] == project_id]

    # Save user message
    user_msg = make_message(project_id, body.role, body.content)
    messages_db.append(user_msg)
    proj_messages.append(user_msg)

    # Determine if this is code/diff → analysis mode, or conversational
    if body.role == "engineer" and looks_like_code(body.content):
        # Analysis mode
        analysis = run_analysis(project, reqs, body.content, "(pasted in chat)")
        sync_requirement_statuses(project_id, analysis)

        # Build readable summary
        summary_parts = [analysis.get("summary", "Analysis complete.")]
        for rr in analysis.get("requirements", []):
            status_emoji = {"match": "✅", "partial": "🟡", "gap": "❌", "clarification_needed": "🟠"}.get(rr.get("status"), "")
            summary_parts.append(f"{status_emoji} **{rr.get('requirement_id')}** ({rr.get('requirement_title')}): {rr.get('status')} — confidence {rr.get('confidence', 0)}%")
            if rr.get("gaps"):
                summary_parts.append(f"   Gaps: {', '.join(rr['gaps'])}")

        if analysis.get("pm_action_items"):
            summary_parts.append("\n**PM Action Items:** " + "; ".join(analysis["pm_action_items"]))

        ai_content = "\n".join(summary_parts)
        ai_msg = make_message(project_id, "ai", ai_content, analysis_result=analysis)
    else:
        # Conversational mode
        ai_text = run_chat(project, reqs, proj_messages, body.role, body.content)
        ai_msg = make_message(project_id, "ai", ai_text)

    messages_db.append(ai_msg)

    return {"user_message": user_msg, "ai_message": ai_msg}


# ── API: Git analysis via chat ───────────────────────────────────────

@app.post("/api/projects/{project_id}/analyze/git")
def analyze_git(project_id: str, body: GitAnalyzeRequest):
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    try:
        git_data = get_git_diff(body.repo_path, body.commit_sha)
    except ValueError as e:
        raise HTTPException(400, str(e))

    reqs = [r for r in requirements_db if r["project_id"] == project_id]
    proj_messages = [m for m in messages_db if m["project_id"] == project_id]

    # Save engineer message with the diff
    diff_preview = git_data["diff"][:500] + ("..." if len(git_data["diff"]) > 500 else "")
    eng_content = f"I just committed: **{git_data['commit_message']}**\n\n```diff\n{diff_preview}\n```"
    eng_msg = make_message(project_id, "engineer", eng_content)
    messages_db.append(eng_msg)

    # Run analysis
    analysis = run_analysis(project, reqs, git_data["diff"], git_data["commit_message"])
    sync_requirement_statuses(project_id, analysis)

    # Build AI response
    summary_parts = [analysis.get("summary", "Analysis complete.")]
    for rr in analysis.get("requirements", []):
        status_emoji = {"match": "✅", "partial": "🟡", "gap": "❌", "clarification_needed": "🟠"}.get(rr.get("status"), "")
        summary_parts.append(f"{status_emoji} **{rr.get('requirement_id')}** ({rr.get('requirement_title')}): {rr.get('status')} — confidence {rr.get('confidence', 0)}%")
        if rr.get("gaps"):
            summary_parts.append(f"   Gaps: {', '.join(rr['gaps'])}")

    if analysis.get("pm_action_items"):
        summary_parts.append("\n**PM Action Items:** " + "; ".join(analysis["pm_action_items"]))

    ai_content = "\n".join(summary_parts)
    ai_msg = make_message(project_id, "ai", ai_content, analysis_result=analysis)
    messages_db.append(ai_msg)

    return {"engineer_message": eng_msg, "ai_message": ai_msg}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
