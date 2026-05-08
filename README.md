# TechBridge AI

> **An AI teammate that turns a one-line PM request into shipped code.**
> One human. Many agents. Many times the output.

TechBridge AI is a PM-Engineer collaboration platform where AI is the third
team member — not just a chat bot. PM delegates work in a group chat, the AI
drafts a structured implementation plan, executes it (writing real code,
tests, and documentation), and waits for engineer sign-off before "shipping."
Every step is captured in a decision ledger.

DSCI 560 final project · USC Viterbi · Spring 2026

---

## End-to-end flow

```
        ┌─ chat rail (every screen) ─┐
PM @AI │ "@AI optimize login speed" │
        └────────────────────────────┘
                      │
                      ▼  AI calls DeepSeek
   ┌─ 01 Mission Control ──────────────────────┐
   │  AI drafts a real Plan v1 with subtasks  │
   │  (P0/P1/P2 priorities + time estimates)  │
   │  → PM clicks "Approve Plan"               │
   └───────────────────────────────────────────┘
                      │
                      ▼  auto-jump
   ┌─ 02 Live Execution ───────────────────────┐
   │  AI generates real artifacts in 3 phases  │
   │   • Implementation — code (.ts / .py)     │
   │   • Testing        — 5-8 test cases       │
   │   • Documentation  — 200-350 word .md     │
   │  Files written to generated/<proj>/<plan>/ │
   └───────────────────────────────────────────┘
                      │
                      ▼  auto-jump
   ┌─ 03 Approval Gate ────────────────────────┐
   │  Engineer reviews the diff, impact        │
   │  metrics, doc, and test results.          │
   │  Open the real files in Finder or VS Code │
   │  → Approve & Ship  •  Request Changes     │
   └───────────────────────────────────────────┘
                      │
                      ▼
   04 Decision Ledger — audit trail of every event
   05 Command Seat    — cross-project rollup
```

---

## Quick start

Requires Python 3.10+, a DeepSeek API key, and macOS / Linux.

```bash
git clone https://github.com/dsci560-spr26/code-requirement-checker.git
cd code-requirement-checker
echo "DEEPSEEK_API_KEY=sk-..." > backend/.env
./run.sh
```

The script installs Python deps, starts FastAPI on `0.0.0.0:8000`, and
serves the static frontend on `0.0.0.0:3000`. It also prints the LAN IP so
teammates on the same WiFi can connect to **the same** project / chat:

```
   Local access:
     Frontend: http://localhost:3000
   LAN access (share with teammates on same WiFi):
     Frontend: http://10.25.32.106:3000
```

Open the URL, pick a project (one is seeded), type your name into the
top-right field, and chat as `PM` to begin.

---

## The five screens

| # | Screen | What it does |
|---|--------|--------------|
| 01 | **Mission Control** | Empty until you @AI in chat. Then AI drafts a real Plan with subtasks. Approve / Edit / Reject. |
| 02 | **Live Execution** | Three animated phases (Implementation → Testing → Documentation). Real DeepSeek output, written to disk. |
| 03 | **Approval Gate** | Review card with diff, impact metrics, generated docs and test results. Open artifacts in Finder or VS Code. |
| 04 | **Decision Ledger** | Chronological audit trail of every action. Filter by actor (PM / Engineer / AI) and action type. |
| 05 | **Command Seat** | Cross-project dashboard: in-flight, awaiting review, shipped today. Click a review item to jump straight to its Approval Gate. |

A persistent chat rail sits on the left of every screen — it never resets
when you switch tabs.

---

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET/POST` | `/api/projects[/{id}]` | Project CRUD |
| `GET/POST/PUT/DELETE` | `/api/projects/{pid}/requirements[/{rid}]` | Requirement CRUD |
| `GET/POST` | `/api/projects/{pid}/chat` | Group chat with AI |
| `POST` | `/api/projects/{pid}/plans` | AI drafts a Plan |
| `POST` | `/api/projects/{pid}/plans/{id}/iterate` | Revise Plan from feedback |
| `POST` | `/api/projects/{pid}/plans/{id}/approve` | Approve Plan |
| `POST` | `/api/projects/{pid}/plans/{id}/execute` | AI generates code / tests / docs |
| `GET` | `/api/projects/{pid}/plans/{id}/artifacts` | List generated artifacts |
| `POST` | `/api/projects/{pid}/plans/{id}/review` | Sign off or request changes |
| `POST` | `/api/projects/{pid}/plans/{id}/open` | Reveal in Finder / open in VS Code |
| `GET` | `/api/projects/{pid}/ledger` | Audit trail |
| `GET` | `/api/dashboard` | Cross-project Command Seat rollup |
| `POST` | `/api/projects/{pid}/upload-pdf` | Extract requirements from PRD |
| `POST` | `/api/projects/{pid}/scan-upload` | Scan an uploaded folder |
| `POST` | `/api/projects/{pid}/analyze/git` | Analyze a real git commit |

Auto-generated docs: **http://localhost:8000/docs**

---

## Tech stack

| Layer | Tools |
|-------|-------|
| Frontend | React 18 (via CDN + Babel standalone) · vanilla CSS · Inter / JetBrains Mono / Instrument Serif |
| Backend | FastAPI · Uvicorn · Pydantic |
| AI | DeepSeek `deepseek-chat` (via OpenAI SDK) |
| Persistence | In-memory (`plans_db`, `artifacts_db`, `ledger_db`) + generated artifacts on disk |
| Multi-user | HTTP polling (3s for messages, 5s for ledger + dashboard) |
| Demo deploy | `run.sh` prints LAN IP; servers bind `0.0.0.0` |

No build tools, no npm. Two files (`backend/main.py` + `frontend/index.html`)
are most of the project.

---

## Generated artifacts on disk

After execution, real files appear at:

```
generated/
└── PROJ-001/
    └── <plan-uuid>/
        ├── manifest.json
        ├── src/auth/password-reset.ts
        ├── tests/password-reset.integration.spec.ts
        └── docs/password-reset-flow-v1.md
```

Filenames come from the AI; paths are sanitized so nothing escapes
`generated/`. From the Approval Gate, two buttons launch your local tools:

- **📂 Reveal in Finder** → `open -R <path>` on macOS
- **🖥 Open in VS Code** → `code <path>` (falls back to `open -a "Visual Studio Code"`)

`generated/` is gitignored — the AI's output is reproducible from the plan,
so we keep it out of the repo.

---

## Project structure

```
code-requirement-checker/
├── backend/
│   ├── main.py           # single-file FastAPI server (~1600 lines)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── index.html        # single-file React app (~2500 lines)
├── run.sh                # one-shot launcher with LAN IP printing
├── generated/            # AI-written artifacts (gitignored)
└── README.md
```

---

## Demo flow (5–7 minutes)

1. **Mission Control** — Type `@AI optimize our login speed — users complain LCP is over 3s. Aim under 1s.` as PM. Watch the right pane go *Awaiting plan* → *drafting* → real plan card.
2. **Approve Plan v1** — Auto-jumps to Live Execution.
3. **Live Execution** — Watch 3 phases play: code typing in a dark editor, tests passing one by one, markdown documentation streaming. Click **→ Open Approval Gate**.
4. **Approval Gate** — Click **🖥 Open in VS Code** to load the real generated `.ts` file in your editor. Then **✓ Approve & Ship**.
5. **Decision Ledger** — Switch tabs to show the full audit trail (5+ events for this plan).
6. **Command Seat** — Final shot of the cross-project dashboard with one shipped plan + one awaiting review.

End on: *"From doer to commander. One human. Many agents. Many times the output."*

---

## Limits and trade-offs

This is a final-project demo, not a production tool.

- **In-memory storage** — server restart drops projects / plans / chat (`generated/` files survive).
- **Single tenant** — no auth; the role toggle (PM / Engineer) is local UI state, not identity.
- **One AI provider** — DeepSeek is hardcoded via the OpenAI SDK. Swapping in Anthropic / OpenAI is a one-line change.
- **No CI / no tests** — fast iteration over correctness for the demo.

---

## License

For coursework purposes (DSCI 560, USC Spring 2026).
