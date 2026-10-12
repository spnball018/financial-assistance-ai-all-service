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
   Also add `<PUBLIC_BASE_URL>/api/gmail/callback` as a redirect URI, enable the **Gmail API** in the
   same project, and add the scope `gmail.readonly` to the OAuth consent screen.
3. Sign in. Add family members in **ตั้งค่า > ผู้ใช้** by their Google address. Each person sees only
   their own data. Signing in asks Google once for both identity and read-only Gmail, so the first
   sign-in also connects that person's Gmail (refresh token stored encrypted with `SECRET_KEY`). A
   banner offers to (re)connect when it is missing or expired; **ตั้งค่า > Gmail ของฉัน** can connect
   another mailbox or disconnect. User 1 keeps reading `gmail-service/token.json` until connected.

   Gmail is a *restricted* scope. While the consent screen is in **Testing**, every person must be
   listed as a test user and Google expires their access after 7 days (Settings then shows
   "ต้องเชื่อมใหม่"). Publishing the app ("In production") without verification avoids the 7-day expiry
   but shows an "unverified app" warning and is limited to 100 users.
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

### Home server (Cloudflare Tunnel)

No router ports are opened and CGNAT does not matter; Cloudflare serves HTTPS. The domain's DNS must
be on Cloudflare.

1. Cloudflare Zero Trust > Networks > Tunnels > **Create a tunnel** (Cloudflared). Copy the token.
   Add a public hostname, e.g. `pfa.panusian.com`, service `HTTP`, URL `web:80`.
2. `./.env`:
   ```
   POSTGRES_PASSWORD=<long random>          # before the first start; it only applies to a new volume
   CLOUDFLARE_TUNNEL_TOKEN=<token>
   ```
3. `expense-api/.env`: a fresh `SECRET_KEY` (keep a copy away from the backups), `AUTH_MODE=google`,
   `ADMIN_EMAILS`, the Google OAuth client, and
   ```
   PUBLIC_BASE_URL=https://pfa.panusian.com
   ALLOWED_HOSTS=localhost,127.0.0.1,expense-api,pfa.panusian.com
   ```
4. Google OAuth client: add JavaScript origin `https://pfa.panusian.com` and redirect URIs
   `https://pfa.panusian.com/api/auth/callback` and `https://pfa.panusian.com/api/gmail/callback`.
5. `docker compose --profile tunnel up -d --build`

`db-backup` writes a `pg_dump -Fc` to `./data/backups` every 24 hours and keeps 14 days
(`BACKUP_KEEP_DAYS`). Copy them off the machine; restore with
`pg_restore -h localhost -U postgres -d financial --clean <file>`.

Cloudflare closes a request after 100 seconds, so a long first sync shows an error in the browser
even though it keeps running on the server. Run the first sync from `http://localhost:3000` on the
server itself, or press Refresh again after a few minutes.

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
