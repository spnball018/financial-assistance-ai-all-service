# Financial Assistance AI — All Services

A personal expense tracker. It reads bank and card emails from Gmail, turns them into a ledger of
transactions, categorizes them with Merchant Memory and an LLM, and lets you review and confirm them
in a web UI before they count in reports.

This repository wires the services together with Docker Compose on a shared `financial-ai` network.

## Services

| Service | Where | Host port | What it does |
|---|---|---|---|
| `web` | `web/` (React + Vite, served by nginx) | 3000 | Review queue, manual entry, monthly summary, balances, settings |
| `expense-api` | `expense-api/` (FastAPI, Alembic) | 8002 | Owns the `ledger` schema: sync, parsing, categorization, receipts, reports |
| `gmail-service` | `gmail-service/` (git submodule, FastAPI) | 8000 | Generic Gmail reader: list by label, message bodies, attachments, move to Trash |
| `credit-card-statement-service` | `credit-card-statement-service/` (FastAPI) | 8003 | Parses statement files (CSV, KTC e-Statement PDF) |
| `postgres` | postgres:16-alpine | 5432 | Database `financial`, schema `ledger` |
| `caddy` | caddy:2-alpine (profile `public`) | 80, 443 | HTTPS for a public server; proxies to `web` |
| `langgraph` / `chatbot` | `langgraph/` (git submodule) | — | Older LangGraph assistant (profile-gated, not part of the tracker yet) |

All ports are bound to `127.0.0.1` except Caddy's. The web UI and expense-api require Google sign-in
(or an API token). gmail-service and credit-card-statement-service have no auth, so never publish their ports.

## Setup

1. Clone with submodules:
   ```bash
   git clone --recurse-submodules <repo-url>
   # or, if already cloned:
   git submodule update --init --recursive
   ```
2. Google OAuth for Gmail: create an OAuth client in Google Cloud, download `credentials.json` and put
   it in the repo root (next to `docker-compose.yml`). Then run the one-time consent flow, which writes
   `gmail-service/token.json`:
   ```bash
   docker compose --profile manual up --build gmail-auth
   ```
   The scope is `gmail.modify` (read and move to Trash; it cannot delete permanently).
3. Secrets:
   ```bash
   cp .env.example .env                           # POSTGRES_PASSWORD
   cp expense-api/.env.example expense-api/.env   # SECRET_KEY, GEMINI_API_KEY, ...
   ```
   `GEMINI_API_KEY` (Google AI Studio) enables AI categorization and receipt reading; without it the
   tracker still works with Merchant Memory only.
4. Start everything and open http://localhost:3000:
   ```bash
   docker compose up -d --build
   ```
   `expense-api` runs the database migrations on start. Press **Refresh** in the web UI to pull the
   last three months of email.

## Sign-in and users

1. Google Cloud > APIs & Services > Credentials > **Create OAuth client ID** > *Web application*
   (a different client from gmail-service's `credentials.json`). Authorized redirect URI:
   `<PUBLIC_BASE_URL>/api/auth/callback`, e.g. `http://localhost:3000/api/auth/callback` or
   `https://money.example.com/api/auth/callback`.
2. In `expense-api/.env`: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `PUBLIC_BASE_URL`,
   and `ADMIN_EMAILS` (your Google address; always an admin). `SECRET_KEY` must be set too.
3. Sign in. Add family members in **ตั้งค่า > ผู้ใช้** by their Google address. Each person will see
   only their own data. (Per-user data separation and Gmail per person are being built: Phase 7 in the plan.)
4. For scripts and Bruno: **ตั้งค่า > API token**, then send `Authorization: Bearer fat_...`.

For local development without Google, set `AUTH_MODE=off` in `expense-api/.env`. It acts as user 1 and
only answers requests for `localhost`/`127.0.0.1`.

## Public server

Google sign-in does not accept a bare IP address, so point a domain name at the server (a free
DuckDNS name works). Then:

```bash
echo "DOMAIN=money.example.com" >> .env
# expense-api/.env: PUBLIC_BASE_URL=https://money.example.com
#                   ALLOWED_HOSTS=localhost,127.0.0.1,expense-api,money.example.com
docker compose --profile public up -d --build
```

Caddy gets a Let's Encrypt certificate automatically (ports 80 and 443 must be open in the firewall).
Everything else stays on `127.0.0.1`. Change `POSTGRES_PASSWORD` before the first start.

## Local development

Postgres in Docker, everything else from local virtualenvs / Node (commands in [CLAUDE.md](CLAUDE.md)):

```bash
docker compose up -d postgres
(cd gmail-service && .venv/bin/uvicorn main:app --port 8000)
(cd expense-api && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial \
   GMAIL_SERVICE_URL=http://localhost:8000 .venv/bin/uvicorn app.main:app --port 8002 --env-file .env)
(cd web && npm install && npm run dev)     # http://localhost:3000, proxies /api to :8002
```

Tests: `cd expense-api && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial_test .venv/bin/python -m pytest`
(the database name must contain `test`; the suite truncates ledger tables). `cd web && npm run build` type-checks the UI.

## Documentation

| Document | Contents |
|---|---|
| [docs/handoff.md](docs/handoff.md) | Current state, files not in git, setting up a new machine, next tasks |
| [docs/expense-tracker-plan.md](docs/expense-tracker-plan.md) | Plan, decisions and task status |
| [docs/api-spec.md](docs/api-spec.md) | HTTP API of every service |
| [docs/er-diagram.md](docs/er-diagram.md) | `ledger` schema (Mermaid + PNG) |
| [docs/sync-engine.md](docs/sync-engine.md) | How email becomes transactions |
| [docs/ktc-track.md](docs/ktc-track.md) | KTC credit-card PDF work (separate track) |

Each FastAPI service also serves Swagger UI at `/docs`.

## Troubleshooting

- **gmail-service returns 500 `Permission denied: 'token.json'`**: the Docker OAuth helper wrote the
  token as root. `sudo chown $USER gmail-service/token.json`.
- **First sync is slow**: gmail-service fetches messages one by one (about 2 minutes for 150 emails).
- **Gmail 500/503 during large syncs**: rate limiting; the sync retries with backoff and the next run
  picks up anything that failed.
