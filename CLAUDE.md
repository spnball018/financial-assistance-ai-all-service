# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

The Financial Assistance AI project: a personal expense tracker plus the services it needs, wired together in `docker-compose.yml` on a shared `financial-ai` bridge network. All host ports are bound to `127.0.0.1` (there is no auth yet).

- **`expense-api/`** (in this repo; FastAPI + Alembic, host port 8002): owns the Postgres schema `ledger` and all money logic: sync from Gmail, per-source parsers (`sources/`, `parsers/`), categorization (`categorize/`), review/confirm, balances, receipts. Layering: `app/routers` → `sync` → `sources` → `parsers`; `sync/repo.py` is the only writer during sync. Secrets in `expense-api/.env`.
- **`web/`** (in this repo; React + Vite, host port 3000; nginx in Docker): the UI. Calls `/api/*`, which the Vite dev server or nginx proxies to expense-api.
- **`gmail-service/`** (git submodule, FastAPI, host port 8000): generic Gmail reader (list by label, message bodies, attachments, move to Trash). No financial logic. Needs Google OAuth (`credentials.json` + `token.json`, scope `gmail.modify`).
- **`credit-card-statement-service/`** (in this repo; FastAPI, host port **8003** → container port 8000): parses statement files; `POST /parse-ktc-pdf` for KTC e-Statements (KTC track, `docs/ktc-track.md`).
- **`langgraph/`** (git submodule): the LangGraph AI assistant. It calls the tool services over HTTP using container-name URLs (`GMAIL_SERVICE_URL`, `CREDIT_CARD_STATEMENT_SERVICE_URL`) and persists to Postgres (`DATABASE_URL`). It has two entry points: `chatbot.py` (interactive) and `main.py` (batch). Both read secrets from `langgraph/.env`.
- **`postgres`** (postgres:16-alpine, port 5432, user `postgres`, password from `./.env` `POSTGRES_PASSWORD` (default `postgres`), database `financial`).

Code changes to `gmail-service` or `langgraph` must be committed inside that submodule's own repo. Then commit the updated submodule pointer here. If those directories are empty, run `git submodule update --init --recursive`.

## Commands

```bash
# One-time Gmail OAuth (writes gmail-service/token.json; opens auth server on :8080)
docker compose --profile manual up --build gmail-auth

# Start the always-on services (postgres, gmail-service, credit-card-statement-service, expense-api, web)
docker compose up -d

# Run the LangGraph assistant (profile-gated; waits for the services above to be healthy)
docker compose --profile chatbot run --rm chatbot    # interactive (stdin/tty)
docker compose --profile run up langgraph            # batch

# Rebuild one service after code changes / tail logs
docker compose up -d --build <service>
docker compose logs -f <service>
```

Local development of the expense tracker (Postgres in docker, the rest from local venvs / Node):

```bash
docker compose up -d postgres
(cd gmail-service && .venv/bin/uvicorn main:app --port 8000)
(cd expense-api && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial \
   GMAIL_SERVICE_URL=http://localhost:8000 .venv/bin/uvicorn app.main:app --port 8002 --env-file .env)
(cd web && npm install && npm run dev)          # http://localhost:3000, proxies /api -> :8002
# expense-api tests (DB tests need a database whose name contains "test")
(cd expense-api && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial_test .venv/bin/python -m pytest)
```

Design docs live in `docs/`: start with `handoff.md` (current state, files not in git, next tasks), then `expense-tracker-plan.md` (plan + status), `sync-engine.md`, `api-spec.md`, `er-diagram.md`, `ktc-track.md`.

`credentials.json` must be in the repo root, next to `docker-compose.yml`. It and `token.json` are gitignored. Each FastAPI service serves Swagger at `/docs`, and the healthchecks call that same URL.

`expense-api` has a pytest suite; `web` type-checks and builds with `npm run build`. The other services are verified by running them.

Migrations: add `expense-api/alembic/versions/NNNN_*.py` with a linear `down_revision`, then update `docs/er-diagram.md` (and re-render the PNG) and `docs/api-spec.md` for any API change.

## Adding a new tool service

Add it as a submodule, or as a directory like `credit-card-statement-service/`. Then add a `docker-compose.yml` entry on the `financial-ai` network with a `/docs` healthcheck. Give it a unique host port. Pass its URL to both `chatbot` and `langgraph` as an env var of the form `http://<service-name>:8000`, and add it to their `depends_on`.
