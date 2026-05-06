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

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from pypdf import PdfReader
import io

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
    username: Optional[str] = None


class GitAnalyzeRequest(BaseModel):
    repo_path: str
    commit_sha: Optional[str] = None


class ScanRepoRequest(BaseModel):
    repo_path: str


class PlanGenerateRequest(BaseModel):
    request: str               # PM's free-form ask, e.g. "optimize login speed"
    requested_by: Optional[str] = None  # who triggered it (username)


class PlanIterateRequest(BaseModel):
    feedback: str
    requested_by: Optional[str] = None


class PlanExecuteRequest(BaseModel):
    requested_by: Optional[str] = None


class PlanReviewRequest(BaseModel):
    decision: str              # "approve" | "request_changes"
    notes: Optional[str] = ""
    requested_by: Optional[str] = None



# ── In-memory Storage ────────────────────────────────────────────────

projects_db: list[dict] = []
requirements_db: list[dict] = []
messages_db: list[dict] = []
plans_db: list[dict] = []         # generated implementation plans
artifacts_db: list[dict] = []     # code/docs/tests produced during execution
ledger_db: list[dict] = []        # audit trail of all decisions


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


def make_message(project_id: str, role: str, content: str, analysis_result: dict = None, suggested_requirements: list = None, username: str = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "role": role,
        "username": username or "",
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analysis_result": analysis_result,
        "suggested_requirements": suggested_requirements or [],
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
Keep responses concise — this is a chat, not an essay.

## IMPORTANT — When suggesting new requirements:
If your response suggests ANY new requirements (features, tasks, endpoints, modules) that
are not already in the current requirements list, append a JSON block at the END of your
response using this EXACT format:

```suggested_requirements
[
  {{"title": "Short title (<60 chars)", "description": "Detailed description with acceptance criteria", "priority": "high|medium|low"}},
  ...
]
```

Only include this block if you're actually suggesting new requirements for the project.
Do NOT include it for existing requirements or general discussion.
The conversational part of your response should come BEFORE this block and should be natural —
don't say "here's the JSON". Users see the JSON rendered as clickable "Add to Todo" buttons."""


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


def run_chat(project: dict, reqs: list[dict], messages: list[dict], role: str, content: str) -> tuple[str, list]:
    """Returns (cleaned_text, suggested_requirements)."""
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
    raw = call_ai(prompt)

    # Extract suggested_requirements block if present
    suggestions = []
    marker_start = raw.find("```suggested_requirements")
    if marker_start != -1:
        # Find the closing ```
        after = raw[marker_start + len("```suggested_requirements"):]
        marker_end = after.find("```")
        if marker_end != -1:
            json_block = after[:marker_end].strip()
            try:
                suggestions = json.loads(json_block)
                if not isinstance(suggestions, list):
                    suggestions = []
            except json.JSONDecodeError:
                suggestions = []
            # Remove the entire block from the visible text
            cleaned = raw[:marker_start] + raw[marker_start + len("```suggested_requirements") + marker_end + 3:]
            raw = cleaned.strip()

    return raw, suggestions


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


# ── API: Plans (AI-generated implementation plans) ───────────────────

PLAN_DRAFT_PROMPT = """You are a senior tech lead drafting an implementation plan for the project "{project_name}".

## Project Background:
{project_background}

## Existing Requirements (for context):
{requirements}

## PM's Request:
{request}

## Your Task:
Break this request down into 3-6 concrete, sequential subtasks. Assign priority and time estimate.
- P0 = critical/blocking, P1 = important, P2 = nice-to-have
- Time estimates like: "30 min", "1 hour", "2 hours", "4 hours"

Return JSON in EXACT format (no markdown fences, just JSON):
{{
  "title": "Short title for this work (e.g., 'Login Speed Optimization')",
  "summary": "One-sentence summary of the plan",
  "subtasks": [
    {{"title": "Action-oriented subtask title", "priority": "P0|P1|P2", "estimated_time": "30 min"}}
  ]
}}

Order subtasks by execution sequence (most fundamental first). Be specific and concrete."""


PLAN_ITERATE_PROMPT = """You are revising an existing implementation plan based on team feedback.

## Project: {project_name}
## Project Background:
{project_background}

## Current Plan (v{version}):
Title: {title}
Subtasks (ordered):
{subtasks_text}

## Team Feedback:
{feedback}

## Your Task:
Produce a revised plan. You can reorder, add, remove, or modify subtasks. Highlight what changed via the "change_note" field.

Return JSON in EXACT format (no markdown fences):
{{
  "title": "Same or refined title",
  "summary": "One-sentence summary",
  "change_note": "Brief note on what changed and why (1-2 sentences, will be shown to PM)",
  "subtasks": [
    {{"title": "...", "priority": "P0|P1|P2", "estimated_time": "1 hour", "promoted": true_if_moved_up_in_priority}}
  ]
}}"""


def add_ledger_entry(project_id: str, actor: str, action: str, summary: str, plan_id: str = None, meta: dict = None):
    entry = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "plan_id": plan_id,
        "actor": actor,       # "pm" / "engineer" / "ai" / username
        "action": action,     # "plan_drafted" / "plan_iterated" / "plan_approved" / "execution_started" / "execution_completed" / "review_approved" / "review_rejected"
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "meta": meta or {},
    }
    ledger_db.append(entry)
    return entry


def make_plan(project_id: str, request: str, ai_output: dict, requested_by: str = None) -> dict:
    pid = str(uuid.uuid4())
    subtasks = []
    for i, s in enumerate(ai_output.get("subtasks", [])):
        subtasks.append({
            "id": f"st-{i+1}",
            "title": s.get("title", "Untitled subtask"),
            "priority": s.get("priority", "P1").upper(),
            "estimated_time": s.get("estimated_time", "1 hour"),
            "status": "pending",
            "promoted": s.get("promoted", False),
        })
    plan = {
        "id": pid,
        "project_id": project_id,
        "request": request,
        "title": ai_output.get("title", "Untitled plan"),
        "summary": ai_output.get("summary", ""),
        "version": 1,
        "history": [],   # past versions
        "subtasks": subtasks,
        "status": "draft",   # draft | approved | executing | awaiting_review | shipped | rejected
        "change_note": "",
        "requested_by": requested_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    plans_db.append(plan)
    return plan


@app.get("/api/projects/{project_id}/plans")
def list_plans(project_id: str):
    plans = [p for p in plans_db if p["project_id"] == project_id]
    return {"plans": plans}


@app.get("/api/projects/{project_id}/plans/{plan_id}")
def get_plan(project_id: str, plan_id: str):
    for p in plans_db:
        if p["id"] == plan_id and p["project_id"] == project_id:
            return p
    raise HTTPException(404, "Plan not found")


@app.post("/api/projects/{project_id}/plans")
def create_plan(project_id: str, body: PlanGenerateRequest):
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    reqs = [r for r in requirements_db if r["project_id"] == project_id]
    reqs_text = "\n".join(
        f"- [{r['id']}] {r['title']} ({r['priority']}): {r['description']}"
        for r in reqs
    ) or "(no existing requirements)"

    prompt = PLAN_DRAFT_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"] or "(no background set)",
        requirements=reqs_text,
        request=body.request,
    )
    raw = call_ai(prompt)
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        ai_output = json.loads(raw[start:end])
    except Exception as e:
        raise HTTPException(500, f"Failed to parse AI plan output: {e}")

    plan = make_plan(project_id, body.request, ai_output, requested_by=body.requested_by)
    add_ledger_entry(
        project_id, "ai", "plan_drafted",
        f"Drafted plan '{plan['title']}' with {len(plan['subtasks'])} subtasks",
        plan_id=plan["id"],
    )
    return plan


@app.post("/api/projects/{project_id}/plans/{plan_id}/iterate")
def iterate_plan(project_id: str, plan_id: str, body: PlanIterateRequest):
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    plan = None
    for p in plans_db:
        if p["id"] == plan_id and p["project_id"] == project_id:
            plan = p
            break
    if not plan:
        raise HTTPException(404, "Plan not found")

    subtasks_text = "\n".join(
        f"  {i+1}. [{s['priority']}] {s['title']} ({s['estimated_time']})"
        for i, s in enumerate(plan["subtasks"])
    )
    prompt = PLAN_ITERATE_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"] or "",
        version=plan["version"],
        title=plan["title"],
        subtasks_text=subtasks_text,
        feedback=body.feedback,
    )
    raw = call_ai(prompt)
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        ai_output = json.loads(raw[start:end])
    except Exception as e:
        raise HTTPException(500, f"Failed to parse AI revision: {e}")

    # Save old version into history
    plan["history"].append({
        "version": plan["version"],
        "title": plan["title"],
        "subtasks": [dict(s) for s in plan["subtasks"]],
        "saved_at": datetime.now(timezone.utc).isoformat(),
    })
    plan["version"] += 1
    plan["title"] = ai_output.get("title", plan["title"])
    plan["summary"] = ai_output.get("summary", plan["summary"])
    plan["change_note"] = ai_output.get("change_note", "")
    plan["subtasks"] = []
    for i, s in enumerate(ai_output.get("subtasks", [])):
        plan["subtasks"].append({
            "id": f"st-{i+1}",
            "title": s.get("title", "Untitled"),
            "priority": s.get("priority", "P1").upper(),
            "estimated_time": s.get("estimated_time", "1 hour"),
            "status": "pending",
            "promoted": s.get("promoted", False),
        })
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    plan["status"] = "draft"

    add_ledger_entry(
        project_id, body.requested_by or "ai", "plan_iterated",
        f"Plan revised to v{plan['version']}. {plan['change_note']}",
        plan_id=plan["id"],
    )
    return plan


@app.post("/api/projects/{project_id}/plans/{plan_id}/approve")
def approve_plan(project_id: str, plan_id: str, body: PlanExecuteRequest):
    for plan in plans_db:
        if plan["id"] == plan_id and plan["project_id"] == project_id:
            plan["status"] = "approved"
            plan["updated_at"] = datetime.now(timezone.utc).isoformat()
            add_ledger_entry(
                project_id, body.requested_by or "pm", "plan_approved",
                f"Plan v{plan['version']} approved — {plan['title']}",
                plan_id=plan_id,
            )
            return plan
    raise HTTPException(404, "Plan not found")


# ── API: Plan execution → AI generates code/tests/docs artifacts ────

EXECUTION_PROMPT = """You are an autonomous engineering agent executing an approved plan.

## Project: {project_name}
## Background: {project_background}

## Approved Plan: {plan_title}
{plan_summary}

## Subtasks to execute (in order):
{subtasks_text}

## Your Task:
Produce 3 deliverables that, together, fully implement this plan. Be concrete and specific to the subtasks above.

1. **Code** — actual code (or a unified diff) that implements the technical changes. Aim for 30-80 lines of realistic, working code in an appropriate language (Python, TypeScript, etc.). Include filename and comments where useful.
2. **Tests** — 5-8 test cases as a list. Each test has a short name + brief description + expected result. Make them sound like real engineering tests, not placeholders.
3. **Documentation** — markdown document (200-350 words) explaining what was changed, why, and the resulting impact / metrics. Use headings, bullet points, and code-style notation where helpful.

Return JSON in EXACTLY this format (no markdown fences around the JSON, no commentary outside):
{{
  "code": {{
    "filename": "e.g. src/auth/login.ts",
    "language": "typescript|python|javascript|...",
    "content": "Full code or diff as a single string with \\n newlines",
    "additions": <int>,
    "deletions": <int>
  }},
  "tests": {{
    "filename": "e.g. tests/login.spec.ts",
    "items": [
      {{"name": "...", "expected": "...", "duration_ms": <int>}}
    ],
    "coverage_pct": <int 0-100>
  }},
  "docs": {{
    "filename": "e.g. docs/Login_Perf_v1.md",
    "content": "Full markdown content as a single string with \\n newlines",
    "word_count": <int>
  }},
  "summary": "1-2 sentence summary of what was accomplished",
  "impact": {{
    "metric_name": "...",
    "before": "...",
    "after": "...",
    "delta_pct": <int signed>
  }}
}}

Be specific. Reference subtask titles. Make the code realistic for the domain."""


@app.post("/api/projects/{project_id}/plans/{plan_id}/execute")
def execute_plan(project_id: str, plan_id: str, body: PlanExecuteRequest):
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    plan = None
    for p in plans_db:
        if p["id"] == plan_id and p["project_id"] == project_id:
            plan = p
            break
    if not plan:
        raise HTTPException(404, "Plan not found")

    if plan["status"] not in ("approved", "executing"):
        raise HTTPException(400, f"Plan must be approved before execution (current: {plan['status']})")

    # Mark all subtasks in progress while we execute
    plan["status"] = "executing"
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()

    subtasks_text = "\n".join(
        f"  {i+1}. [{s['priority']}] {s['title']} ({s['estimated_time']})"
        for i, s in enumerate(plan["subtasks"])
    )
    prompt = EXECUTION_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"] or "(no background)",
        plan_title=plan["title"],
        plan_summary=plan.get("summary") or "",
        subtasks_text=subtasks_text,
    )
    add_ledger_entry(
        project_id, body.requested_by or "ai", "execution_started",
        f"Execution started for plan '{plan['title']}' ({len(plan['subtasks'])} subtasks)",
        plan_id=plan_id,
    )

    raw = call_ai(prompt)
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        result = json.loads(raw[start:end])
    except Exception as e:
        plan["status"] = "approved"  # roll back so user can retry
        raise HTTPException(500, f"Failed to parse AI execution output: {e}")

    # Save artifacts (in-memory only for now)
    now = datetime.now(timezone.utc).isoformat()
    saved = []
    for kind in ("code", "tests", "docs"):
        if kind in result:
            payload = dict(result[kind])
            # For tests, serialize items into readable content
            if kind == "tests" and payload.get("content") is None:
                lines = []
                for i, t in enumerate(payload.get("items", []), 1):
                    lines.append(f"// Test {i}: {t.get('name','')}")
                    if t.get("expected"):
                        lines.append(f"//   expected: {t['expected']}")
                    lines.append("")
                if payload.get("coverage_pct") is not None:
                    lines.append(f"// Coverage: {payload['coverage_pct']}%")
                payload["content"] = "\n".join(lines)
            artifact = {
                "id": str(uuid.uuid4()),
                "plan_id": plan_id,
                "project_id": project_id,
                "kind": kind,
                "payload": payload,
                "created_at": now,
            }
            artifacts_db.append(artifact)
            saved.append(artifact)

    # Mark subtasks done (visual completion)
    for s in plan["subtasks"]:
        s["status"] = "done"

    plan["status"] = "awaiting_review"
    plan["execution_summary"] = result.get("summary", "")
    plan["execution_impact"] = result.get("impact", {})
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()

    add_ledger_entry(
        project_id, "ai", "execution_completed",
        f"Execution completed · code + tests + docs generated. {result.get('summary', '')}",
        plan_id=plan_id,
        meta={"impact": result.get("impact", {})},
    )

    return {"plan": plan, "artifacts": saved}


@app.get("/api/projects/{project_id}/plans/{plan_id}/artifacts")
def list_artifacts(project_id: str, plan_id: str):
    items = [a for a in artifacts_db if a["plan_id"] == plan_id and a["project_id"] == project_id]
    return {"artifacts": items}





@app.post("/api/projects/{project_id}/plans/{plan_id}/review")
def submit_review(project_id: str, plan_id: str, body: PlanReviewRequest):
    plan = None
    for p in plans_db:
        if p["id"] == plan_id and p["project_id"] == project_id:
            plan = p
            break
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan["status"] != "awaiting_review":
        raise HTTPException(400, f"Plan must be awaiting review (current: {plan['status']})")

    decision = body.decision.lower()
    if decision == "approve":
        plan["status"] = "shipped"
        plan["shipped_at"] = datetime.now(timezone.utc).isoformat()
        plan["reviewer"] = body.requested_by
        plan["review_notes"] = body.notes or ""
        plan["updated_at"] = plan["shipped_at"]
        add_ledger_entry(
            project_id, body.requested_by or "engineer", "review_approved",
            f"Approved & shipped · {plan['title']}",
            plan_id=plan_id,
            meta={"notes": body.notes or ""},
        )
    elif decision == "request_changes":
        plan["status"] = "approved"   # back to approved → engineer can re-execute
        plan["review_notes"] = body.notes or ""
        plan["updated_at"] = datetime.now(timezone.utc).isoformat()
        add_ledger_entry(
            project_id, body.requested_by or "engineer", "review_rejected",
            f"Requested changes · {body.notes or '(no notes)'}",
            plan_id=plan_id,
        )
    else:
        raise HTTPException(400, "decision must be 'approve' or 'request_changes'")
    return plan


# ── API: Cross-project Dashboard ─────────────────────────────────────

@app.get("/api/dashboard")
def get_dashboard():
    """Cross-project rollup for Command Seat."""
    today_str = datetime.now(timezone.utc).date().isoformat()

    by_project = {p["id"]: p for p in projects_db}

    in_flight = []        # status == executing
    awaiting_review = []  # status == awaiting_review
    shipped_today = []    # status == shipped + shipped_at today
    shipped_all = []
    drafts = []

    for p in plans_db:
        project = by_project.get(p["project_id"])
        if not project:
            continue
        entry = {
            "plan_id": p["id"],
            "project_id": p["project_id"],
            "project_name": project["name"],
            "title": p["title"],
            "status": p["status"],
            "version": p["version"],
            "subtask_count": len(p["subtasks"]),
            "updated_at": p["updated_at"],
            "created_at": p["created_at"],
            "shipped_at": p.get("shipped_at"),
            "reviewer": p.get("reviewer"),
            "execution_summary": p.get("execution_summary", ""),
            "execution_impact": p.get("execution_impact", {}),
        }
        if p["status"] == "executing":
            in_flight.append(entry)
        elif p["status"] == "awaiting_review":
            awaiting_review.append(entry)
        elif p["status"] == "shipped":
            shipped_all.append(entry)
            if p.get("shipped_at", "").startswith(today_str):
                shipped_today.append(entry)
        elif p["status"] == "draft":
            drafts.append(entry)

    # Sort each list by recency
    for lst in (in_flight, awaiting_review, shipped_today, shipped_all, drafts):
        lst.sort(key=lambda e: e["updated_at"], reverse=True)

    # Stats from ledger
    review_events = [e for e in ledger_db if e["action"] == "review_approved"]
    avg_confidence = 0  # we don't actually track confidence per plan yet
    # Try to estimate from execution_impact if delta_pct exists (proxy)
    confidence_samples = []
    for p in plans_db:
        impact = p.get("execution_impact") or {}
        # use abs(delta_pct) capped at 100 as a rough "confidence" proxy
        if impact.get("delta_pct") is not None:
            confidence_samples.append(min(100, abs(impact["delta_pct"])))
    if confidence_samples:
        avg_confidence = int(sum(confidence_samples) / len(confidence_samples))

    return {
        "projects_count": len(projects_db),
        "in_flight": in_flight,
        "awaiting_review": awaiting_review,
        "shipped_today": shipped_today,
        "shipped_all": shipped_all,
        "drafts": drafts,
        "stats": {
            "in_flight_count": len(in_flight),
            "awaiting_review_count": len(awaiting_review),
            "shipped_today_count": len(shipped_today),
            "shipped_all_count": len(shipped_all),
            "avg_confidence": avg_confidence,
            "total_plans": len(plans_db),
        },
    }


# ── API: Decision Ledger ─────────────────────────────────────────────

@app.get("/api/projects/{project_id}/ledger")
def get_ledger(project_id: str):
    entries = [e for e in ledger_db if e["project_id"] == project_id]
    return {"entries": entries}


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
    user_msg = make_message(project_id, body.role, body.content, username=body.username)
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
        ai_text, suggestions = run_chat(project, reqs, proj_messages, body.role, body.content)
        ai_msg = make_message(project_id, "ai", ai_text, suggested_requirements=suggestions)

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


# ── API: Scan Codebase → AI summary + suggested requirements ─────────

SCAN_PROMPT = """You are joining a project team chat. A codebase has just been shared with you for the project "{project_name}".

## Project Background:
{project_background}

## Codebase Files (truncated):
{code_snapshot}

## Your Task:
Introduce yourself as the AI teammate and give a concise report to the PM and Engineer:
1. **What's built**: Summarize the current state of the codebase (2-3 sentences, concrete)
2. **Tech stack**: Languages/frameworks detected
3. **What's likely missing**: Based on the project background, point out obvious gaps
4. **Proposed requirements**: Suggest 4-8 concrete next-step requirements to guide the team

Format: Natural conversational message. Then append the structured requirements as a JSON block:

```suggested_requirements
[
  {{"title": "Short title", "description": "What to build + acceptance criteria", "priority": "high|medium|low"}}
]
```

Be specific — reference actual filenames/functions you saw. Don't be generic."""


# Source file extensions we care about
_SOURCE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
                ".cpp", ".c", ".h", ".html", ".css", ".vue", ".svelte",
                ".md", ".yml", ".yaml", ".toml", ".json"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist",
              "build", ".next", "target", ".idea", ".vscode"}


def read_repo_snapshot(repo_path: str, max_chars: int = 15000) -> str:
    """Walk a repo dir and return concatenated source files (truncated)."""
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"Not a valid directory: {repo}")

    parts = []
    total = 0
    for path in sorted(repo.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in _SOURCE_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = path.relative_to(repo)
        header = f"\n\n═══ {rel} ═══\n"
        chunk = header + text
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > len(header) + 100:
                parts.append(header + text[: remaining - len(header)] + "\n... [truncated]")
            break
        parts.append(chunk)
        total += len(chunk)

    return "".join(parts) if parts else "(no source files found)"


def build_snapshot_from_uploads(files: list, max_chars: int = 15000) -> tuple[str, str]:
    """Build snapshot from uploaded files. Returns (snapshot, root_folder_name)."""
    parts = []
    total = 0
    root_name = ""

    for f in files:
        # Browser passes webkitRelativePath as filename (e.g., "todo-api/app.py")
        rel = f["path"]
        if not root_name and "/" in rel:
            root_name = rel.split("/")[0]

        # Skip if in ignored dir
        path_parts = rel.split("/")
        if any(p in _SKIP_DIRS for p in path_parts):
            continue

        # Extension filter
        ext = Path(rel).suffix.lower()
        if ext not in _SOURCE_EXTS:
            continue

        try:
            text = f["content"].decode("utf-8", errors="ignore")
        except Exception:
            continue

        header = f"\n\n═══ {rel} ═══\n"
        chunk = header + text
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > len(header) + 100:
                parts.append(header + text[: remaining - len(header)] + "\n... [truncated]")
            break
        parts.append(chunk)
        total += len(chunk)

    return "".join(parts) if parts else "(no source files found)", root_name


@app.post("/api/projects/{project_id}/scan-upload")
async def scan_upload(project_id: str, files: list[UploadFile] = File(...)):
    """Scan an uploaded folder (via <input webkitdirectory>) and have AI summarize."""
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    # Read all files into memory
    items = []
    for f in files:
        content = await f.read()
        # f.filename is the webkitRelativePath like "todo-api/app.py"
        items.append({"path": f.filename, "content": content})

    snapshot, root_name = build_snapshot_from_uploads(items)
    if snapshot == "(no source files found)":
        raise HTTPException(400, "No recognizable source files in the folder")

    prompt = SCAN_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"] or "(no background set)",
        code_snapshot=snapshot,
    )
    raw = call_ai(prompt)

    # Extract suggested_requirements block
    suggestions = []
    marker_start = raw.find("```suggested_requirements")
    if marker_start != -1:
        after = raw[marker_start + len("```suggested_requirements"):]
        marker_end = after.find("```")
        if marker_end != -1:
            json_block = after[:marker_end].strip()
            try:
                suggestions = json.loads(json_block)
                if not isinstance(suggestions, list):
                    suggestions = []
            except json.JSONDecodeError:
                suggestions = []
            raw = (raw[:marker_start] + raw[marker_start + len("```suggested_requirements") + marker_end + 3:]).strip()

    ai_msg = make_message(
        project_id, "ai",
        f"📂 Scanned folder `{root_name or '(uploaded)'}` ({len(files)} files)\n\n" + raw,
        suggested_requirements=suggestions,
    )
    messages_db.append(ai_msg)

    return {"message": ai_msg, "project": project}


@app.post("/api/projects/{project_id}/scan-repo")
def scan_repo(project_id: str, body: ScanRepoRequest):
    """Scan a local codebase by path (legacy), let AI summarize + propose requirements."""
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    try:
        snapshot = read_repo_snapshot(body.repo_path)
    except ValueError as e:
        raise HTTPException(400, str(e))

    project["repo_path"] = body.repo_path

    prompt = SCAN_PROMPT.format(
        project_name=project["name"],
        project_background=project["background"] or "(no background set)",
        code_snapshot=snapshot,
    )
    raw = call_ai(prompt)

    # Extract suggested_requirements block
    suggestions = []
    marker_start = raw.find("```suggested_requirements")
    if marker_start != -1:
        after = raw[marker_start + len("```suggested_requirements"):]
        marker_end = after.find("```")
        if marker_end != -1:
            json_block = after[:marker_end].strip()
            try:
                suggestions = json.loads(json_block)
                if not isinstance(suggestions, list):
                    suggestions = []
            except json.JSONDecodeError:
                suggestions = []
            raw = (raw[:marker_start] + raw[marker_start + len("```suggested_requirements") + marker_end + 3:]).strip()

    # Post as AI system message in chat
    ai_msg = make_message(
        project_id, "ai",
        f"📂 Scanned `{body.repo_path}`\n\n" + raw,
        suggested_requirements=suggestions,
    )
    messages_db.append(ai_msg)

    return {"message": ai_msg, "project": project}


# ── API: PDF Upload → Extract Requirements ──────────────────────────

PDF_EXTRACT_PROMPT = """You are analyzing a product document (PRD, spec, or requirements doc).

## Document Content:
{content}

## Your Task:
Extract the project background and a list of concrete, actionable requirements from this document.

Respond in this exact JSON format:
{{
  "background": "A 2-4 sentence summary of the project: what problem it solves, goals, context",
  "requirements": [
    {{
      "title": "Short requirement title (under 60 chars)",
      "description": "Detailed description of what needs to be built, including acceptance criteria",
      "priority": "high|medium|low"
    }}
  ]
}}

Rules:
- Extract 3-10 requirements (not too granular, not too broad)
- Each requirement should be independently testable
- Infer priority from the document's language (must/critical = high, should = medium, nice to have = low)
- If the document has no clear requirements, return an empty list with background only
- Be specific and preserve domain terminology from the document
"""


@app.post("/api/projects/{project_id}/upload-pdf")
async def upload_pdf(project_id: str, file: UploadFile = File(...)):
    """Upload a PDF, extract requirements via AI, auto-add them to the project."""
    project = None
    for p in projects_db:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(404, "Project not found")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "File must be a PDF")

    # Extract text from PDF
    try:
        content = await file.read()
        reader = PdfReader(io.BytesIO(content))
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        text = text.strip()
        if not text:
            raise HTTPException(400, "Could not extract text from PDF (may be scanned/image-only)")
    except Exception as e:
        raise HTTPException(400, f"Failed to parse PDF: {e}")

    # Truncate if too long (keep token usage reasonable)
    text = text[:20000]

    # Call AI to extract structured requirements
    prompt = PDF_EXTRACT_PROMPT.format(content=text)
    try:
        ai_response = call_ai(prompt)
        start = ai_response.index("{")
        end = ai_response.rindex("}") + 1
        parsed = json.loads(ai_response[start:end])
    except Exception as e:
        raise HTTPException(500, f"AI extraction failed: {e}")

    # Update project background (append to existing or replace if user wants)
    extracted_background = parsed.get("background", "").strip()
    if extracted_background:
        if project["background"].strip():
            project["background"] = project["background"] + "\n\n[From uploaded PDF]: " + extracted_background
        else:
            project["background"] = extracted_background

    # Add extracted requirements to the project
    added_reqs = []
    for req_data in parsed.get("requirements", []):
        req = {
            "id": next_requirement_id(project_id),
            "project_id": project_id,
            "title": req_data.get("title", "Untitled"),
            "description": req_data.get("description", ""),
            "priority": req_data.get("priority", "medium"),
            "status": "todo",
        }
        requirements_db.append(req)
        added_reqs.append(req)

    return {
        "project": project,
        "added_requirements": added_reqs,
        "count": len(added_reqs),
        "filename": file.filename,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
