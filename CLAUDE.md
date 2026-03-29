# SuzyCloud — Multi-Tenant WhatsApp AI Assistant Platform

## What is this
A platform for running multiple WhatsApp AI assistants from a single backend.
Each assistant (persona) has its own identity, memory, credentials, and config.

## Architecture
- **One process** handles all personas
- **Persona = directory** under `personas/{key}/` with config.yaml, .env, soul/, memory/
- **Webhook** receives messages → looks up persona by group_id → loads soul → invokes Claude
- **Per-persona isolation**: separate soul files, memory, daily logs, IMAP credentials, Graph tokens

## Stack
- Python 3.12+ / FastAPI / Uvicorn
- Claude Code CLI (`claude -p`) for AI
- Waha for WhatsApp (shared instance)
- IMAP for email (per-persona credentials)
- Microsoft Graph for calendar/tasks (per-persona tokens)

## Key Files
- `src/main.py` — FastAPI app, webhook, batch handler
- `src/persona.py` — Persona CRUD, config loading, template generation
- `src/soul_loader.py` — Per-persona soul/memory context assembly
- `src/config.py` — Global config (shared infra)
- `src/waha.py` — WhatsApp message delivery (from OpenSuzy)
- `src/claude_runner.py` — Claude CLI subprocess (from OpenSuzy)
- `src/batcher.py` — Message batching (from OpenSuzy)
- `src/sessions.py` — Session persistence (from OpenSuzy)

## Persona Directory Layout
```
personas/{key}/
  config.yaml     — name, emoji, group_ids, claude model, feature flags
  .env            — IMAP_*, MICROSOFT_*, CLAUDE_CODE_OAUTH_TOKEN
  soul/           — IDENTITY.md, SOUL.md, USER.md, TOOLS.md, EMAIL.md, etc.
  memory/         — MEMORY.md, contacts.md, projects.md
    daily/        — conversation logs
  tasks/          — lessons.md
  data/           — runtime state
```

## Running
```bash
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
```
