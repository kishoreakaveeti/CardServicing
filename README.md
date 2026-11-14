# Card Servicing

API-first card origination learning project built with Python/FastAPI, HTTPX,
SQLite, MongoDB, Redis, RabbitMQ, Docker and VS Code debugging.
Local configuration includes project-specific data, credentials, volumes and ports.

Startup configuration selects `CARD_STORE=sqlite` and
`TELEMETRY_SINK=mongo|stdout|none` (default `mongo`). See
[infrastructure adapters](docs/infrastructure-adapters.md) to run the same build
with Mongo, then stdout with Mongo stopped, and to verify safe retries.

## Setup and run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,infra]"
.\scripts\setup-infra.ps1
.\scripts\start-docker-api.ps1 -DebugApi -Build
```

Open Docker Swagger at http://127.0.0.1:8011/docs. Select **Attach to Docker API**
in VS Code to debug. Alternatively select **Debug API** or run
`python -m uvicorn card_servicing.main:app --reload --port 8010` for host Swagger
at http://127.0.0.1:8010/docs. Use one execution mode per database at a time.

## Originate a card

`POST /cards` with required header `Idempotency-Key: card-demo-001`:

```json
{"user_name":"alex.smith","given_name":"Alex","account_number":"0012345678"}
```

Returns **201** with those fields, a generated UUID `id`, `card_number` such as
`SIM-529b8ee2175b48f0ae803f70527a4cb9`, `status: "created"`, and UTC `created_at`.
The account must be supplied as a string to preserve leading zeroes. We assume it
already exists; no account lookup or account creation occurs. User names, given
names and account identifiers are trimmed, nonblank and length bounded. Extra
fields and invalid requests return 422 before storage changes.

Same key and normalized body replay the original 201 response and card number;
different body with the same key returns 409. Keys do not expire. Responses carry
`Idempotency-Replayed`, `X-Trace-ID` and `Cache-Control: no-store`. SQLite failures
return sanitized 503 errors; retry with the same key and body.

Numbers are simulation identifiers, with no issuer/network integration, PAN, PIN,
CVV, activation or physical fulfilment. Use synthetic names/accounts locally.
SQLite stores readable development data and original response snapshots. Diagnostics
omit names/card numbers and use keyed account hashes when configured. Authentication
and production card-data handling are future work.

## Client, tests and contract

```powershell
python -m api_client.demo
python -m pytest
python scripts/export_cards_openapi.py
python scripts/check_infra.py
```

The client defaults to host port 8010; set `API_BASE_URL=http://127.0.0.1:8011`
for Docker. Exported contract: [cards.openapi.json](docs/cards.openapi.json).
GitHub Actions runs tests on pushes and pull requests. The `/items` endpoints
provide in-memory CRUD examples for learning and implementation reference.

See [architecture](docs/architecture.md), [persistence](docs/card-persistence.md),
[infrastructure](docs/local-infrastructure.md), [health checks](docs/health-checks.md),
and [Docker debugging](docs/docker-debugging.md). Redis caching and RabbitMQ business
events are provisioned for future increments.

## Conversational card demo

Read the [end-to-end code walkthrough](docs/chat-end-to-end.md) or open the
[zoomable offline SVG viewer](docs/diagrams/index.html). Diagram source and
generated SVGs are committed together and checked in CI.

The optional [chat demo guide](docs/chat-demo.md) includes PowerShell setup,
VS Code debugging, credential-free verification, and a request walkthrough.
It serves a plain browser page on port 8020 and launches a local stdio MCP child;
the existing REST API stays in its own process on port 8010 (or Docker 8011).
Claude receives the catalogue-discovered tools with automatic selection, asks
for missing values and proposes an action for review. The backend validates and
executes the stored proposal only after explicit confirmation, without another
model call. See [adding tools through configuration](docs/tool-catalogue.md).
