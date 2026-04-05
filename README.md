# Nudge

**Nudge** is a local-first second-brain and lifestyle agent built for people who want a private, memory-aware AI companion that runs on their own machine.

It combines:
- a CLI for fast daily use
- a dashboard for chat, memory, projects, reminders, and analytics
- local memory and retrieval
- Ollama for local reasoning
- an agent-style workflow that can suggest saves, plan multi-step actions, and ask for approval before important changes

## Why Nudge

Nudge is designed to feel useful across day-to-day life, not just as a chatbot.

It can help you:
- remember notes, logs, reminders, and personal preferences
- build a cleaner picture of your habits, interests, and current focus
- answer questions using your saved context instead of generic responses
- run daily check-ins and weekly reviews
- organize projects and goals
- propose multi-step plans and wait for approval before applying them

## Local-First Guarantee

Nudge is built to stay on your machine.

- No cloud APIs
- No remote database
- No internet dependency for core use
- LLM calls go only to your local Ollama server
- Memory stays in local JSON files and local retrieval indexes

## Core Capabilities

### Memory and Recall
- Save logs, notes, reminders, and project goals locally
- Retrieve relevant memories for grounded answers
- Ask context-aware questions like:
  - `what am I focusing on today?`
  - `what do you know about me?`
  - `what did I learn about FAISS?`

### Persona Building
- Learns patterns over time from your logs and notes
- Maintains local persona state such as interests, habits, and trends
- Uses persona to improve recommendations and weekly summaries

### Daily and Weekly Workflows
- `checkin` asks a short daily reflection
- `review week` summarizes recent patterns, wins, and reminders
- `ask` helps Nudge learn more about you with persona-building questions

### Projects and Goals
- Create projects and attach goals
- Mark goals done, archive projects, edit or delete goals
- Generate starter plans from natural language requests like:
  - `help me get fitter`
  - `help me build a startup on agentic ai`

### Dashboard
- Local web UI for chat and analytics
- Search inside conversation, memory, timeline, projects, and reminders
- Project modal and goal controls
- Timeline view and recent activity
- Shared state with the CLI

### Agentic Behavior
- Routes requests through an explicit workflow
- Uses LangGraph when available
- Suggests saves when a message looks useful
- Uses approval-based flows for multi-step planning
- Falls back gracefully when the local model is unavailable

## Architecture

```text
CLI / Dashboard
      |
      v
  Agent Core
      |
      +--> Planner / routing
      +--> Memory manager
      +--> Persona builder
      +--> Retrieval
      +--> Projects / reminders / repair tools
      |
      v
 Local Ollama + Local JSON/FAISS state
```

## Project Structure

```text
nudge/
├── app/
│   ├── agent/         # core agent logic, prompts, graph, memory, planner
│   ├── models/        # dataclasses / typed structures
│   ├── persona/       # persona extraction and schema
│   ├── services/      # llm, storage, retrieval, conversations
│   ├── tools/         # insights, projects, reminders, activities, repair
│   ├── dashboard.py   # local dashboard server
│   └── main.py        # CLI entry point
├── dashboard/         # static dashboard assets
├── data/              # local memory, reminders, persona, embeddings
├── scripts/           # helper scripts
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Setup

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com)
- A local model for chat/reasoning
- Docker Desktop if you want the containerized dashboard flow

### 1. Install models in Ollama

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

### 2. Local Python setup

```bash
cd <project_dir>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Start Ollama

```bash
ollama serve
```

If you are using the Ollama desktop app and it is already running, you may not need to start it manually.

## Running Nudge
Clone from github repository.
### CLI

```bash
cd <project_dir>
source .venv/bin/activate
python -m app.main
```

### Dashboard

```bash
cd <project_dir>
source .venv/bin/activate
python -m app.dashboard
```

Then open:

```text
http://127.0.0.1:8765
```

## Docker Deployment

Nudge supports an easy local Docker flow for the dashboard while keeping Ollama on the host machine.

### Start with Docker

```bash
cd <project_dir>
docker compose up --build -d dashboard
```

Then open:

```text
http://127.0.0.1:8765
```

### Notes

- The dashboard container talks to host Ollama using `host.docker.internal`
- Your local memory persists through the mounted `data/` directory
- This is usually faster and more stable on a Mac than running Ollama inside Docker

### Helpful Docker commands

```bash
docker compose ps
docker compose logs -f dashboard
docker compose restart dashboard
docker compose down
docker compose up --build -d dashboard
```

## Usage

### Core commands

```text
log: <text>                 Save a daily log
note: <text>                Save a note
save: <text>                Alias for note
remember: <text>            Alias for note
checkin                     Run a 3-question daily check-in
review week                 Generate a weekly review
ask                         Ask a persona-building question
ask reset                   Reset question history
remind: <when> <text>       Create a reminder
reminders                   List reminders
project add <name>          Create a project
goal add <project> :: <t>   Add a goal to a project
projects                    Show project summaries
recent                      Show recent memory entries
timeline                    Show a recent cross-memory timeline
story week                  Show a short weekly narrative
activities                  Recommend activities from persona
approve                     Approve a pending save or plan
skip                        Skip a pending save or plan
autosave on|off|status      Control autosave behavior
persona                     Print persona JSON
help                        Show command help
quit                        Exit
```

### Example session

```text
nudge> note: I play badminton regularly
Saved note.

nudge> remind: tomorrow 09:00 review roadmap
Saved reminder.

nudge> help me get fitter
A plan to improve overall fitness with small, trackable steps.

Project: Fitness
Starter goals:
- Walk for 20 minutes 3 times this week
- Do 2 short strength sessions
- Track sleep timing for 5 days

Click the thumbs up icon to add this to projects, or the thumbs down icon to skip.
```

## Dashboard Features

The dashboard and CLI share the same memory and project state.

Dashboard highlights:
- local chat with Nudge
- recent memory panel
- projects and goals management
- reminders view
- timeline and weekly review
- save approval with thumbs up / thumbs down
- project modal and click-based goal management

## How Nudge Decides What To Save

Nudge supports three memory behaviors:

- Explicit save:
  - `log: ...`
  - `note: ...`
  - `save: ...`
  - `remember: ...`
- Intelligent autosave:
  - useful durable context may be saved automatically
- Ask-to-save:
  - if a statement looks useful but confidence is lower, Nudge can ask before saving

The goal is to balance helpfulness with trust.

## Current Tech Stack

- Python 3.10+
- Ollama
- Qwen for local reasoning
- `nomic-embed-text` for local embeddings
- FAISS for retrieval
- LangGraph for workflow orchestration when available
- JSON files for local persistence
- Plain HTML/CSS/JS dashboard

## Best Practices

- Keep Ollama running while using Nudge
- Use `note:` for long-term facts and ideas
- Use `log:` for daily state, events, and check-ins
- Use `review week` regularly so the system stays useful
- Use the dashboard when you want visual memory and project management

## Future Improvement Scope

Nudge is useful, but there is plenty of room to grow. Strong future directions include:

- richer planner and executor loops with stronger tool routing
- better memory confidence and source transparency
- more robust non-heuristic intent routing
- voice input/output as an optional local module
- calendar and file-system tools through a cleaner tool protocol
- stronger project coaching and progress tracking
- habit analytics and trend visualizations
- export/import and backup workflows
- multi-profile support for different users or contexts
- stronger test coverage across CLI, dashboard, and agent flows

## Contributing

Contributions that improve grounding, reliability, UX, and local privacy are especially welcome.

Feel free to open an issue or raise a PR to enhance:
- agent planning and approval flows
- dashboard UX
- memory repair tools
- retrieval quality
- project and reminder workflows
- docs, setup, and examples

If you submit a PR, please keep the project local-first and privacy-respecting.

## GitHub Safety Note

Before publishing, make sure you do **not** commit:
- `.env`
- `venv/` or `.venv/`
- personal data from `data/`
- generated caches and indexes


## License
MIT