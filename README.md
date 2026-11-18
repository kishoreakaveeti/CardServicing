# Card Servicing: Agentic Banking & FinCrime Platform

**Azure deployment and operations:** [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md)
is the guide for one-command create, stop, start, redeploy and verified teardown.

A production-grade, chat-first banking simulation demonstrating **LangGraph + LangChain multi-agent orchestration**, **privacy-preserving LLM interactions via Microsoft Presidio**, **catalogue-driven Model Context Protocol (MCP)** integration, and **independent financial crime risk management (FinCrime Agent)**. Customers can seamlessly interact through conversational AI or direct-action UI workflows—both paths strictly enforce identical authorization, verification, and persistent business logic.

---

## Key Features

Live policy RAG setup, ingestion and integration verification are documented in
[Live policy runtime](docs/live-policy-runtime.md).

- **LangGraph Multi-Agent Architecture:**
  - **`SupervisorAgent` (LangGraph)**: LLM-powered supervisor dynamically routes intents, constructs minimal-context handoff contracts, schedules specialist consultations, and orchestrates compound multi-step workflows.
  - **`CardServicingAgent` (LangGraph Specialist)**: Autonomous reasoning agent managing account creation, balance/account viewing, and new card issuance.
  - **`FinCrimeAgent` (LangGraph Specialist)**: Independent risk management agent handling card blocking, unblocking, and fraud mitigation, retaining its separately hosted microservice boundary.
  - **Compound Request Orchestration & Resumption**: Handles complex compound requests (e.g. *"Unblock my card and issue a new card"* or *"Block my card and show my accounts"*), resuming specialist execution after receiving intermediate handoff results.
  - **Structured Minimal-Context Handoffs**: Exchanges explicit `AgentHandoff` and `HandoffResult` contracts containing only necessary domain facts rather than wholesale unstructured chat histories.
  - **Bounded Reasoning Loops & Safety Limits**: Enforces iteration caps, retry backoffs, and deterministic policy enforcement.
- **Privacy-Preserving LLM Interactions & Token Vault:**
  - **Centralized Presidio PII Gateway**: Microsoft Presidio analyzer and anonymizer engine with custom recognizers for Australian financial identifiers:
    - **Australian BSB Numbers** (`AU_BSB`: formatted `XXX-XXX` or 6 digits)
    - **Australian Bank Account Numbers** (`AU_ACCOUNT_NUMBER`: 6-10 digits)
    - **Australian Tax File Numbers** (`AU_TFN`: 8-9 digits verified with official ATO modulo-11 checksum)
    - **Australian Medicare Numbers** (`AU_MEDICARE`: 10 digits with validation)
    - **Australian Phone Numbers** (`AU_PHONE_NUMBER`: mobile and landline formats)
    - **Payment Cards** (`CREDIT_CARD`: 13-19 digits validated with Luhn algorithm)
  - **Reversible Token Vault**: Permitted banking values are substituted with opaque reversible tokens (`selected_account_...`) before entering model context. Original values are resolved strictly inside the trusted execution layer after customer authorization.
  - **Zero-Credential LLM Exposure & Fail-Closed Checks**: Authentication secrets, PINs, CVVs, passwords, and API keys are completely stripped and never exposed to LLMs. Outbound verification fails closed if unmasked sensitive PII or credentials are detected.
  - **Dependency-Injected `TokenVault` Interface**:
    - `InMemoryTokenVault` (Default): Session and workflow-scoped with TTL, automatic expiry cleanup, and customer isolation (`owner`/`sub`).
    - `RedisTokenVault`: Configurable future provider for multi-replica cluster persistence.
    - *Storage Note:* In-memory storage is scoped to the local process lifetime; process restarts require active sessions to re-authenticate or re-select.
- **Card Blocking & Unblocking:**
  - Securely block compromised, lost, or stolen cards conversationally (*"Block my card ending in 1234"*) or via direct forms.
  - Unblock previously blocked cards with explicit confirmation prompt warnings.
  - Restricted selection: only active cards can be blocked; only blocked cards can be unblocked.
- **Cross-Agent Pre-Issuance Security Rule:** 
  - If any card associated with an account is blocked, new card issuance for that account is rejected across backend services and orchestrator until the card is resolved or unblocked.
- **MCP Server Agent-Tool Access Policy:**
  - Fine-grained declarative configuration in `tools.json` and `MCP_AGENT_TOOLS`.
  - `cards_agent` is restricted exclusively to card servicing APIs.
  - `fincrime_agent` is restricted exclusively to FinCrime APIs (`block_card`, `unblock_card`).
  - Calls across unauthorized agent boundaries are strictly rejected with HTTP 403.
- **Independent FinCrime Backend & Database:**
  - Dedicated `fincrime_servicing` service and SQLite store (`fincrime.db`) maintaining immutable audit logs, block records, and idempotent replays.
  - Synchronizes card lifecycle status directly with the authoritative Cards database (`cards.db`).
- **Extended Observability:**
  - OpenTelemetry telemetry tracks supervisor routing decisions, specialist handoffs, MCP tool calls, and workflow transitions with all sensitive identifiers sanitized.

---

## Technology Stack

| Component | Technology | Container Name / Role | Entry Point |
| --- | --- | --- | --- |
| **Frontend UI** | React 19, TypeScript, Vite | `card-servicing-frontend` | Conversational chat, direct banking actions, masked forms, review & confirmation (`frontend/src/main.tsx`) |
| **Experience (BFF)** | FastAPI, Python 3.13 | `card-servicing-experience` | HttpOnly cookie session management, CSRF validation, EdDSA customer delegation (`src/card_chat/experience.py`) |
| **Cards Agent (Orchestrator)** | FastAPI, Python 3.13 | `card-servicing-agent` | Multi-agent orchestrator, intent routing, durable operations journal (`src/card_chat/main.py`) |
| **FinCrime Agent** | FastAPI, Python 3.13 | `fincrime-agent` | Dedicated financial crime agent microservice (`src/card_chat/fincrime_agent.py`) |
| **MCP Server** | Official Python MCP SDK | `card-servicing-mcp` | Streamable HTTP catalogue-driven tool discovery and execution (`src/card_chat/mcp_server.py`) |
| **Cards API** | FastAPI, Python 3.13 | `cards-api` | Core banking service, account ownership, card issuance (`src/card_servicing/main.py`) |
| **FinCrime API** | FastAPI, Python 3.13 | `fincrime-api` | Independent risk service, card blocking/unblocking, audit logging (`src/fincrime_servicing/main.py`) |
| **Databases** | SQLite (built-in Python) | Independent SQLite files | `cards.db`, `fincrime.db`, `agent.db`, `sessions.db` in named volumes / local data |
| **Diagnostics** | MongoDB / PyMongo | `card-servicing-mongodb` | Redacted operational traces and metrics (supports MongoDB or console stdout) |
| **Service Identity** | Keycloak (OIDC) | `card-servicing-identity` | Containerized OIDC identity provider for inter-service token exchange |

---

## Prerequisites

Before running or debugging the project, ensure your environment meets the following prerequisites:

1. **Operating System:** Windows (PowerShell 5.1 or 7+), macOS, or Linux.
2. **Python:** Python 3.11 to 3.13 (Python 3.13 recommended to match container builds).
   - *Note:* Python includes `sqlite3` natively. **No separate database installation (PostgreSQL/MySQL/SQLite CLI) is required.**
3. **Node.js:** Node.js 20+ with `npm` (required for native frontend build; optional when using Docker containers).
4. **Docker Desktop:** Required for containerized execution; optional if running in native mode with console telemetry.
5. **Claude API Key (`ANTHROPIC_API_KEY`):**
   - For live conversational reasoning, get an API key from [Anthropic Console](https://console.anthropic.com/).
   - Supported models include `claude-3-5-sonnet-20241022`, `claude-3-7-sonnet-20250219`, or `claude-3-5-haiku-20241022`.
   - *Offline / Stub Mode:* If you do not have an Anthropic API key, the application includes a deterministic credential-free stub model (`MODEL_PROVIDER=stub`) that handles common banking and FinCrime intents out of the box with zero external dependencies!

---

## First-Time Setup

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/kishoreakaveeti/CardServicing.git
   cd CardServicing
   ```

2. **Create and activate the Python virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev,chat,infra]" debugpy
   ```

3. **Run the initial stack setup script:**
   ```powershell
   .\.venv\Scripts\python.exe scripts/setup_stack.py
   ```
   *What this does:* Generates internal service credentials, EdDSA signing keys in `data/identity/`, and an ignored `.env.stack` configuration.

4. **Configure your Claude API Key (Optional for live LLM):**
   Open the root `.env` file (or copy `.env.example` to `.env`):
   ```dotenv
   MODEL_PROVIDER=anthropic
   MODEL_ID=claude-3-5-sonnet-20241022
   ANTHROPIC_API_KEY=sk-ant-api03-YOUR_ACTUAL_KEY_HERE
   ```
   *To run without an API key, simply keep:*
   ```dotenv
   MODEL_PROVIDER=stub
   ```

---

## Option 1: Run and Debug Locally WITHOUT Containers (Native Mode)

Native mode executes all Python services and the React frontend directly on your computer.

### Step 1: Install and build the frontend
```powershell
cd frontend
npm ci
npm run build
cd ..
```

### Step 2: Configure for local standalone execution
Ensure your `.env` contains:
```dotenv
ALLOW_INSECURE_LOCAL=1
TELEMETRY_SINK=stdout
```
*(Setting `ALLOW_INSECURE_LOCAL=1` allows local microservices to communicate without requiring a separate Keycloak instance; `TELEMETRY_SINK=stdout` logs diagnostics directly to the terminal without requiring MongoDB).*

### Step 3: Run the services

#### Approach A: Run manually in separate terminals
Open PowerShell terminals for each service:

- **Terminal 1 — Cards Banking API (Port 8010):**
  ```powershell
  .\.venv\Scripts\python.exe scripts/local_service.py cards
  ```
- **Terminal 2 — FinCrime API (Port 8025):**
  ```powershell
  .\.venv\Scripts\python.exe scripts/local_service.py fincrime
  ```
- **Terminal 3 — MCP Tool Server (Port 8030):**
  ```powershell
  .\.venv\Scripts\python.exe scripts/local_service.py mcp
  ```
- **Terminal 4 — FinCrime Agent (Port 8022):**
  ```powershell
  .\.venv\Scripts\python.exe scripts/local_service.py fincrime_agent
  ```
- **Terminal 5 — Cards Agent / Orchestrator (Port 8021):**
  ```powershell
  .\.venv\Scripts\python.exe scripts/local_service.py agent
  ```
- **Terminal 6 — Experience BFF (Port 8020):**
  ```powershell
  .\.venv\Scripts\python.exe scripts/local_service.py experience
  ```
- **Terminal 7 — Frontend Dev Server (Port 8080):**
  ```powershell
  $env:EXPERIENCE_PROXY_TARGET = "http://127.0.0.1:8020"
  npm --prefix frontend run dev:local
  ```

#### Approach B: One-click launch in VS Code
Open the project root in VS Code:
1. Go to the **Run & Debug** pane (`Ctrl+Shift+D`).
2. Select **Debug Full Stack (Local)**.
3. Press **F5**.
VS Code automatically starts dependencies, boots all 6 Python microservices (`Cards API`, `FinCrime API`, `MCP Server`, `FinCrime Agent`, `Cards Agent`, `Experience BFF`), launches the Vite dev server, and attaches debuggers.

### Step 4: Debugging native code
- **Breakpoints:** Set breakpoints anywhere in `src/card_chat/`, `src/card_servicing/`, or `src/fincrime_servicing/`. Execution will pause immediately when a user triggers an action or turn.
- **Frontend Hot-Reload:** Run `npm --prefix frontend run dev` to make real-time changes to React components.

---

## Option 2: Run and Debug WITH Containers (Docker Mode)

Docker mode packages all services into dedicated Linux containers with automated Keycloak identity and MongoDB logging. The container topology is composed of:
- **`card-servicing-frontend`**: React 19 UI (Nginx reverse proxy on port 8080)
- **`card-servicing-experience`**: Experience BFF (`src/card_chat/experience.py`)
- **`card-servicing-agent`**: Cards Agent & Orchestrator (`Dockerfile.agent`)
- **`fincrime-agent`**: FinCrime Agent microservice (`Dockerfile.fincrime-agent`)
- **`card-servicing-mcp`**: Model Context Protocol tool server (`Dockerfile.mcp`)
- **`cards-api`**: Core Cards Banking service (`Dockerfile.cards`)
- **`fincrime-api`**: Financial Crime risk & blocking service (`Dockerfile.fincrime-api`)
- **`card-servicing-mongodb`**: Operational diagnostics store (Mongo 8.0 on port 27018)
- **`card-servicing-identity`**: OIDC Identity provider (Keycloak on port 8081)

### Step 1: Ensure Docker Desktop is running
Make sure Docker Desktop is started with Linux containers enabled.

### Step 2: Forward Claude Key to the Docker stack
If using Anthropic Claude, edit `.env.stack`:
```dotenv
STACK_MODEL_PROVIDER=anthropic
```
And ensure your `ANTHROPIC_API_KEY` is present in `.env`.

### Step 3: Build and start the containers
```powershell
.\scripts\stack.ps1 -Build
```
*(Or via direct Compose: `docker compose -f compose.yaml up --build -d`)*

Once started, open **http://localhost:8080** in your browser.

### Step 4: Debugging containerized services
- Each Python container starts with `debugpy` listening on dedicated debug ports:
  - **Experience (BFF):** Port `5681` (`card-servicing-experience`)
  - **Cards Agent:** Port `5682` (`card-servicing-agent`)
  - **MCP Server:** Port `5683` (`card-servicing-mcp`)
  - **Cards API:** Port `5684` (`cards-api`)
  - **FinCrime API:** Port `5685` (`fincrime-api`)
  - **FinCrime Agent:** Port `5686` (`fincrime-agent`)
- **In VS Code:** Open the Run & Debug panel, select **Debug Full Stack (Docker)**, and press **F5**. VS Code will attach to all 6 containerized Python processes simultaneously!
- **View Container Logs:**
  ```powershell
  docker compose logs -f cards-agent
  docker compose logs -f fincrime-agent
  docker compose logs -f fincrime-api
  docker compose logs -f cards-api
  ```
- **Stop Containers:**
  ```powershell
  .\scripts\stack.ps1 -Stop
  ```

---

## Customer Journey Walkthrough

1. **Register or Sign In:**
   - Open `http://localhost:8080`.
   - Click **Register / Sign up** (or type *"I want to register"*).
   - Enter a synthetic username (e.g. `alex.smith`) and password (10+ characters).
2. **Open an Account:**
   - Click **Create account** (or type *"Open a savings account"*).
   - Check the option to issue a card with the account, review the masked details, and confirm.
3. **View Cards:**
   - Click **View my cards** (or type *"Show my cards"*).
   - You will see your simulated card (e.g. `SIM-... [CREATED]`).
4. **Block a Card:**
   - Click **Block card** (or type *"Block my card ending in 1234"*).
   - Select the active card and specify a reason (e.g. *"Suspected fraud"*).
   - Confirm the action. The card status transitions to `[BLOCKED]`.
5. **Verify Cross-Agent Blocking Rule:**
   - Click **Issue new card** (or type *"Issue another card for my account"*).
   - Select the account that holds the blocked card.
   - The Orchestrator intercepts the request:
     > *"Cannot issue a new card for this account because an existing card is blocked. Under banking security rules, the blocked card must be resolved before a new card can be issued."*
6. **Unblock the Card:**
   - Click **Unblock card** (or type *"Unblock my card ending in 1234"*).
   - Only blocked cards appear in the selection.
   - The confirmation review displays an attention warning:
     > *"ATTENTION: Unblocking this card will restore its active status and re-enable new card issuance on this account. Confirm with the button below."*
   - Confirm unblocking. Card status reverts to `[CREATED]`.
7. **Re-attempt Card Issuance:**
   - Click **Issue new card** on the same account. Issuance now succeeds!

---

## Testing & Verification Suite

Run all automated unit, integration, and contract tests with a single command:

```powershell
# Run all Python backend tests (202+ tests including LangGraph and Presidio PII)
.\.venv\Scripts\python.exe -m pytest -q

# Run frontend tests
npm --prefix frontend test

# Verify production frontend build
npm --prefix frontend run build

# Validate OpenAPI contract consistency
.\.venv\Scripts\python.exe scripts/export_cards_openapi.py --check
```

---

## Documentation Index

- [End-to-End Debugging Journeys](docs/debugging-account-and-card.md) — Major banking, policy/RAG, identity and recovery flows with diagrams, ordered walkthroughs and a method-based generated breakpoint index. Refresh with `python scripts/generate_debugging_guide.py`; validate with `--check`.
- [Architecture & Sequence Diagrams](docs/architecture.md) — Comprehensive multi-agent sequence diagrams, boundaries, and database layouts.
- [Tool Catalogue & MCP](docs/tool-catalogue.md) — Catalogue compilation, tool definitions, and agent access policies.
- [Customer Banking Experience](docs/customer-banking.md) — Step-by-step UI flows, session security, and API endpoints.
- [Card Issuance & Idempotency](docs/card-issuance.md) — Concurrency control, retry policies, and existing-card handling.
- [Model Privacy & Data Boundaries](docs/model-privacy.md) — Zero-trust model context isolation and private argument binding.
- [Getting Started & Operations Guide](docs/getting-started.md) — In-depth setup, ports, troubleshooting, and container debugging reference.

Azure VM deployment preparation: see [Azure Compose](docs/azure-compose.md) for the
standalone `compose.azure.yaml`, Blob telemetry, HTTPS/OIDC settings and scaling
limitations. The existing local Compose commands are unchanged.
