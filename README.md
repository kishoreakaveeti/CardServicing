# Card Servicing

A chat-first banking simulation that lets customers register, sign in, create accounts and issue simulated cards. Customers can describe what they need in chat or select an action directly; both paths use the same backend services and authorization rules.

The AI interprets intent and opens the appropriate workflow. Trusted services handle authentication, ownership checks and database changes. Customers review and confirm banking writes before they execute. This is a learning/demo application: all cards use synthetic `SIM-` identifiers.

## Main functionality

- **Customer access:** username/password registration, login, logout and customer-specific sessions.
- **Accounts:** create savings or transaction accounts and view accounts saved across sessions.
- **Cards:** optionally issue a card with an account, view existing cards and explicitly request additional cards.
- **Duplicate protection:** ordinary repeat requests return the existing card; same-operation retries preserve the original result.
- **Conversational workflows:** use hosted Claude for intent interpretation, or start with a credential-free stub that handles a few common intents.
- **Operational visibility:** correlated service diagnostics and model/tool usage metrics, with MongoDB or console logging.

## Technology stack

| Layer | Technology / responsibility |
| --- | --- |
| User interface | React, TypeScript and Vite; conversational UI, action buttons and private forms |
| HTTP services | Python and FastAPI; separate Experience/BFF, Agent, MCP and Cards services |
| Agent and AI | Provider-neutral agent orchestration, Anthropic Messages, catalogue-driven tools |
| Tool execution | Official MCP SDK over Streamable HTTP, OpenAPI contracts and HTTPX |
| Authentication | Keycloak service identity, signed customer delegation, secure browser sessions and scrypt password hashing |
| Business persistence | SQLite for customers, accounts, cards, workflows, sessions and retry snapshots |
| Diagnostics | MongoDB/PyMongo, or console/disabled telemetry |
| Development | Docker Compose, VS Code, debugpy and pytest |

The request path is **React -> Experience -> Agent -> HTTP MCP -> Cards -> SQLite**. MongoDB stores diagnostics, not banking records. See the [architecture and diagrams](docs/architecture.md) for the detailed boundaries.

## Run the application

The supplied scripts and VS Code profiles target **Windows and PowerShell**. The browser UI is **http://localhost:8080** in both modes.

### First-time setup

Install Python (3.13 matches the container runtime) and clone/open this repository. From its root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,chat,infra]' debugpy
.\.venv\Scripts\python.exe scripts/setup_stack.py
```

Setup generates local credentials and signing keys. The default stub needs no AI API key. Never commit generated `.env.stack`, `.env`, signing keys or local databases.

**SQLite needs no separate installation**: the application uses Python's `sqlite3` support and creates its databases automatically. **MongoDB Community Server is not a mandatory host installation**: use the provided Docker container or native console logging. Check the [full prerequisites and configuration](docs/getting-started.md#shared-first-time-setup) before your first launch.

### Option 1: Docker containers

Install/start Docker Desktop with Linux containers. Host Node/npm, MongoDB and Keycloak installations are unnecessary for this mode.

```powershell
.\scripts\stack.ps1 -Build
```

Open **http://localhost:8080** after startup succeeds. Compose supplies the application, Identity and MongoDB services.

To debug, install the VS Code Python/Python Debugger extensions and Chrome, select **Debug Full Stack (Docker)** and press **F5**. This profile builds/starts the debug containers, attaches the Python debuggers and opens the browser.

[Complete Docker instructions: prerequisites, attach ports, rebuild and shutdown](docs/getting-started.md#1-run-and-debug-using-local-docker-containers).

### Option 2: Native local execution

Install Node.js 22/npm on Windows and restart VS Code. Install frontend packages:

```powershell
Push-Location frontend
npm.cmd ci
Pop-Location
```

With Docker Desktop running for Identity/MongoDB dependencies, select **Debug Full Stack (Local)** and press **F5**. It prepares missing dependencies, starts Vite and launches the four Python services natively.

For execution without a debugger, or without Docker/MongoDB, follow the [complete native instructions](docs/getting-started.md#2-run-and-debug-natively-on-your-computer). Console logging uses `TELEMETRY_SINK=stdout`; Identity is still required.

**Switching modes:** stop the other application stack first. In native mode, stop the **Start Local Frontend** task as well as the Python debug sessions to release port 8080. Docker and native execution use separate SQLite databases. [Switching and shutdown steps](docs/getting-started.md#switch-between-modes).

## Try a customer journey

At `http://localhost:8080`, select **Register / Sign up**, create an account, optionally issue a card, then view your accounts/cards. Review and confirm writes. Sign out and back in to verify persistence. To intentionally issue another card, select the additional-card option in a fresh workflow.

Use synthetic details and keep credentials/private identifiers in secure forms. See the [complete customer journey](docs/customer-banking.md).

## Documentation

| Guide | What it covers |
| --- | --- |
| [Getting started](docs/getting-started.md) | Complete prerequisites, configuration, Docker/native run/debug steps, Mongo alternatives and troubleshooting |
| [Architecture](docs/architecture.md) | Service boundaries, technology choices, security and persistence |
| [Customer banking](docs/customer-banking.md) | Registration, accounts, cards and shared workflows |
| [Card issuance](docs/card-issuance.md) | Existing-card checks, explicit additional cards and idempotent retries |
| [End-to-end trace](docs/chat-end-to-end.md) | How intent, private forms, review and execution pass through services |
| [MongoDB logging](docs/mongodb-logging.md) | Diagnostics configuration, redaction and verification |
| [Model privacy](docs/model-privacy.md) | What enters model context and what stays private |
| [Tool catalogue](docs/tool-catalogue.md) | OpenAPI-derived capabilities and tool configuration |

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See [all verification commands and troubleshooting](docs/getting-started.md#customer-journey-and-verification) for frontend builds, schema/diagram checks and live-stack diagnostics. Older API-only infrastructure examples are historical; use the two current launch paths above.
