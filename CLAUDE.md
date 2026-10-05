# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

The top-level orchestration repo for the Financial Assistance AI project. It holds almost no application code. Its job is to wire separate services together in `docker-compose.yml` on a shared `financial-ai` bridge network:

- **`gmail-service/`** (git submodule, FastAPI, host port 8000): queries Gmail by label, date and text. Needs Google OAuth (`credentials.json` + `token.json`).
- **`credit-card-statement-service/`** (lives in this repo, not a submodule; FastAPI, host port **8001** → container port 8000): `POST /upload-statement/` parses an uploaded CSV with `Date`, `Description` and `Amount` columns into transactions.
- **`langgraph/`** (git submodule): the LangGraph AI assistant. It calls the tool services over HTTP using container-name URLs (`GMAIL_SERVICE_URL`, `CREDIT_CARD_STATEMENT_SERVICE_URL`) and persists to Postgres (`DATABASE_URL`). It has two entry points: `chatbot.py` (interactive) and `main.py` (batch). Both read secrets from `langgraph/.env`.
- **`postgres`** (postgres:16-alpine, port 5432, user/password `postgres`/`postgres`, database `financial`).

Code changes to `gmail-service` or `langgraph` must be committed inside that submodule's own repo. Then commit the updated submodule pointer here. If those directories are empty, run `git submodule update --init --recursive`.

## Commands

```bash
# One-time Gmail OAuth (writes gmail-service/token.json; opens auth server on :8080)
docker compose --profile manual up --build gmail-auth

# Start the always-on services (postgres, gmail-service, credit-card-statement-service)
docker compose up -d

# Run the LangGraph assistant (profile-gated; waits for the services above to be healthy)
docker compose --profile chatbot run --rm chatbot    # interactive (stdin/tty)
docker compose --profile run up langgraph            # batch

# Rebuild one service after code changes / tail logs
docker compose up -d --build <service>
docker compose logs -f <service>
```

`credentials.json` must be in the repo root, next to `docker-compose.yml`. It and `token.json` are gitignored. Each FastAPI service serves Swagger at `/docs`, and the healthchecks call that same URL.

This repo has no test suite, linter or build step. You verify changes by running the containers.

## Known README inaccuracies

`README.md` predates the current layout. Trust `docker-compose.yml` over it:
- The README says `--profile auth`. The actual profile for `gmail-auth` is `manual`.
- The README refers to the path `financial-assistance-ai-gmail-service/`. The submodule is actually at `gmail-service/`.
- The README's service table lists only Gmail.

## Adding a new tool service

Add it as a submodule, or as a directory like `credit-card-statement-service/`. Then add a `docker-compose.yml` entry on the `financial-ai` network with a `/docs` healthcheck. Give it a unique host port. Pass its URL to both `chatbot` and `langgraph` as an env var of the form `http://<service-name>:8000`, and add it to their `depends_on`.
