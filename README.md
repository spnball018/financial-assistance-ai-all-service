# Financial Assistance AI — All Services

This repository is the top-level orchestration layer for the Financial Assistance AI project. It composes all tool microservices used by the LangGraph-based AI assistant into a single Docker environment.

Each service lives in its own Git submodule and exposes a REST API that LangGraph tools call at runtime.

## Services

| Service | Submodule | Port | Description |
|---|---|---|---|
| `gmail-service` | `financial-assistance-ai-gmail-service` | `8000` | Query Gmail by label, date, and text |

## Prerequisites

1. **Docker & Docker Compose** must be installed.
2. Clone the repository with submodules:
   ```bash
   git clone --recurse-submodules <repo-url>
   # or, if already cloned:
   git submodule update --init --recursive
   ```

## Gmail Service Setup

The Gmail service requires a one-time OAuth authentication before it can run.

### Step 1: Add Google Cloud credentials

Follow the [Gmail service README](./financial-assistance-ai-gmail-service/README.md#prerequisites-get-google-cloud-credentials) to create a Google Cloud project and download `credentials.json`.

Place the file here:
```
financial-assistance-ai-gmail-service/credentials.json
```

### Step 2: Run the OAuth flow (first time only)

```bash
docker compose --profile auth up gmail-auth
```

Follow the link printed in the console, log in with your Gmail account, and grant access. A `token.json` file will be written to `financial-assistance-ai-gmail-service/token.json`.

### Step 3: Start all services

```bash
docker compose up -d
```

The Gmail API will be available at `http://localhost:8000`.

Re-run Step 2 whenever `token.json` expires or is deleted.

## Running individual services

```bash
# Start only the Gmail service
docker compose up -d gmail-service

# Rebuild after code changes
docker compose up -d --build gmail-service

# View logs
docker compose logs -f gmail-service

# Stop everything
docker compose down
```

## API Documentation

Once running, each service exposes interactive docs via FastAPI:

| Service | Swagger UI | ReDoc |
|---|---|---|
| Gmail | http://localhost:8000/docs | http://localhost:8000/redoc |

## Adding a new tool service

1. Add the new service as a Git submodule:
   ```bash
   git submodule add <repo-url> <service-name>
   ```
2. Add a new entry to `docker-compose.yml` under `services:`, referencing `./service-name` as the build context.
3. Connect it to the `financial-ai` network so services can reach each other by container name.
4. Document it in the table above.