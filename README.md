# Card Servicing

Ordinary card requests now return an existing card instead of creating another. Additional cards require an explicit choice and confirmation. See [card issuance and retry policy](docs/card-issuance.md).

MongoDB now starts with the full Docker stack and receives service diagnostics by default. See [MongoDB logging and duplicate-card policy](docs/mongodb-logging.md).

The chat-first interface now supports customer registration/login, owned accounts, optional and additional simulated cards, and direct action buttons. See [Customer banking: complete journey and API](docs/customer-banking.md). Existing OIDC service authentication, Agent/MCP orchestration and SQLite persistence remain in place.

React ? Experience API ? Agent API ? Streamable HTTP MCP ? Cards API.
Claude interprets ordinary text; private details use a masked picker and a secure
form. Every write requires a review and confirmation. All cards are simulations.

## Current VS Code profiles

| Profile | Runs where | What F5 does |
| --- | --- | --- |
| **Debug Full Stack** | Docker | Compatibility name for the Docker compound below |
| **Debug Full Stack (Docker)** | Docker | One shared task builds/starts the stack, then attaches four Python debuggers and launches Chrome |
| **Debug Frontend** | Browser, Docker-served frontend | Launches Chrome at localhost:8080; start the Docker stack first |
| **Attach to Experience API** | Docker | Attaches to loopback port 5681 |
| **Attach to Agent API** | Docker | Attaches to loopback port 5682 |
| **Attach to MCP Server** | Docker | Attaches to loopback port 5683 |
| **Attach to Cards API** | Docker | Attaches to loopback port 5684 |
| **Debug Full Stack (Local)** | Native Windows | Checks prerequisites, starts local Vite, launches four local Python processes and Chrome |
| **Debug Local Experience API** | Native Windows | Launches Experience on 8020 |
| **Debug Local Agent API** | Native Windows | Launches Agent on 8021 |
| **Debug Local MCP Server** | Native Windows | Launches MCP on 8030 |
| **Debug Local Cards API** | Native Windows | Launches Cards on 8010 |
| **Debug Local Frontend** | Browser, local Vite | Launches Chrome; run local Vite first |

Docker Python profiles **attach** to existing processes with `/app/src` source
mappings. Local Python profiles **launch** `.venv/Scripts/python.exe` directly;
there are no remote source mappings, published debugger ports or application
images in local mode. Both modes use one process per service without reload.

The former **Debug Chat + API**, **Debug Chat**, **Debug API**, and **Attach to
Docker API** profiles are superseded. Their old walkthrough is archived in
[legacy-host-setup.md](docs/legacy-host-setup.md), not the current runbook.

## One-time setup

Open this repository root in VS Code. Install the Python Debugger extension;
Chrome debugging uses VS Code's built-in JavaScript debugger. From PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,chat,infra]' debugpy
.\scripts\setup-stack.ps1
```

Setup preserves existing `.env.stack` credentials and signing keys. Never commit
`.env`, `.env.stack` or `data/identity`. For natural-language intent, configure:

```text
# .env.stack
STACK_MODEL_PROVIDER=anthropic
# .env
MODEL_ID=your-supported-Claude-model
ANTHROPIC_API_KEY=your-key
```

`STACK_MODEL_PROVIDER=stub` provides a credential-free test adapter, with manual
selection; it does not understand natural language. Prompt caching defaults off.

## Docker: build, deploy, then attach

Install/start Docker Desktop with Linux containers. Host Node.js is unnecessary
for this mode: Docker builds the React app and Python images.

### 1. Build and start without debuggers

```powershell
.\scripts\stack.ps1 -Build
```

This builds the runtime image stages, creates the containers and persistent
volumes, and waits for health. It publishes only frontend 8080 and Keycloak 8081
on loopback. The four backend APIs use Compose DNS names inside the network.
Open http://localhost:8080. Use `localhost` consistently for OIDC sign-in.

### 2. Start debug images and listeners

Stop any existing VS Code debug session, then:

```powershell
.\scripts\stack.ps1 -DebugComponent All -Build
```

The override selects debugpy image stages, runs Vite with source maps, and maps
Experience/Agent/MCP/Cards listeners to 5681/5682/5683/5684 respectively. The
script waits for HTTP health and listener readiness. It does not wait for
attachment before starting HTTP, so the shared pre-launch task cannot deadlock.

### 3. Attach

In **Run and Debug**, select **Debug Full Stack (Docker)** and press **F5**.
Its shared task brings up or reuses the debug stack once, then all four attach
profiles connect and Chrome opens. If you already ran step 2, the task reuses
healthy services/listeners. **Debug Full Stack** remains an alias for this mode.
Set a breakpoint and perform a browser request; startup alone does not execute
message or card-write handlers.

For only one component:

```powershell
.\scripts\stack.ps1 -DebugComponent Agent -Build
# Select Attach to Agent API, then F5.
```

Substitute `Experience`, `MCP`, or `Cards` and select its corresponding attach
profile. `-DebugComponent Frontend` starts Vite with Python listeners disabled;
select **Debug Frontend**. Other application services continue running normally.

### 4. Rebuild after edits and stop

Images contain source snapshots. After changing Python or frontend source,
disconnect, rerun the relevant startup command with `-Build`, then attach again.
Debugger disconnect leaves containers running. The startup script reopens a
listener by restarting its service when a previous detach closed that listener.

```powershell
.\scripts\stack.ps1 -Build   # Return to normal runtime images, no debug listeners.
.\scripts\stack.ps1 -Stop    # Remove containers/network; preserve named volumes.
```

VS Code also provides **Tasks: Run Task ? Stop Debug Stack**. To inspect logs
when Docker is on PATH:

```powershell
docker compose --env-file .env --env-file .env.stack ps
docker compose --env-file .env --env-file .env.stack logs --tail 50 agent mcp
```

For catalogue/discovery startup breakpoints, start a healthy stack first, then:

```powershell
.\scripts\stack.ps1 -DebugComponent MCP -Build -WaitForDebugger
# Attach to MCP Server; continue to start HTTP.
```

Use `Agent` similarly for discovery. This special mode starts only the selected
service with `--no-deps` and waits for its listener, not HTTP readiness.

## Local: launch the application without building Docker images

Install **Node.js 22 LTS with npm on Windows**, then restart VS Code so PATH is
updated. Local mode needs the Python environment above and frontend packages:

```powershell
Push-Location frontend
npm ci
Pop-Location
```

### 1. Stop conflicting application processes

Docker and local modes share browser/identity ports 8080/8081. Stop existing
VS Code debug sessions before switching. To keep Docker-hosted Keycloak and MongoDB:

```powershell
docker compose --env-file .env --env-file .env.stack stop frontend experience agent mcp cards
docker compose --env-file .env --env-file .env.stack up -d --wait identity mongodb
```

This is a convenient hybrid: **all five application components run natively**;
the identity provider and MongoDB remain in Docker. No application image is built or
deployed. Docker Desktop's CLI may need adding to PATH; the startup script's
Docker detection paths are documented in `scripts/stack.ps1`.

### 2. Identity option with no Docker at all

Authentication remains enabled locally. To avoid Docker entirely, run a native
Keycloak installation rather than the identity container. Use the repository's
Keycloak 26.4.7 version and its supported JDK (OpenJDK 21), following the vendor
[Windows ZIP setup](https://www.keycloak.org/getting-started/getting-started-zip)
and [realm import instructions](https://www.keycloak.org/server/importExport).
Stop the Docker identity first if it is running; both cannot bind port 8081.

In a separate PowerShell terminal, with the paths adjusted to your installation:

```powershell
$repoRoot = (Get-Location).Path
$keycloakDir = 'C:\Tools\keycloak-26.4.7'
New-Item -ItemType Directory -Force "$keycloakDir\data\import" | Out-Null
Copy-Item -LiteralPath "$repoRoot\data\identity\realm.json" -Destination "$keycloakDir\data\import\cards-realm.json"
& "$keycloakDir\bin\kc.bat" start-dev --http-host=127.0.0.1 --http-port=8081 --hostname=http://localhost:8081 --import-realm
```

The imported realm contains the same synthetic users and client credentials as
`.env.stack`. Imports do not replace an existing realm: use a fresh native
installation/data directory initially, or deliberately reconcile its clients.
Keep that terminal running. Native and Docker identity stores are separate;
existing browser sessions may require signing in again after switching.

Local wrappers default to issuer `http://localhost:8081/realms/cards`. For another
Keycloak realm, set `LOCAL_OIDC_ISSUER` before opening VS Code and register the
same clients/audiences/permissions plus callback `http://localhost:8080/auth/callback`.
Do not disable authentication to get local debugging working.

### 3. Launch the local full stack

Select **Debug Full Stack (Local)** and press **F5**. The compound:

1. Checks npm, installed frontend dependencies, identity discovery and free ports.
2. Starts Vite locally, proxying `/api` and `/auth` to Experience on 8020.
3. Launches four Python debug sessions from the workspace venv.
4. Waits inside each service launcher for its downstream dependency before import/startup.
5. Waits for all six HTTP endpoints (including Identity and Vite) alongside the Python launches, then opens Chrome on localhost:8080.

The frontend browser profile is hidden from the normal launch picker because it
only opens Chrome; it does not start application services. Use **Debug Full Stack
(Local)** for F5 startup. The **Wait for Local HTTP** task now reports each missing
service and signals readiness to VS Code with a background task matcher. If it
fails, inspect its terminal and the Python terminals. **Debug Anyway** skips the
readiness check and cannot fix a missing server. The UI uses port **8080**; port
8000 is the internal Docker service port. A missing `npm` command requires
installing Node.js with npm and restarting VS Code; an unavailable port 8081
requires starting the external Identity service described above.

Dependency order is Cards ? MCP ? Agent ? Experience. Launchers wait up to three
minutes; errors appear in their individual terminals. If a startup breakpoint
pauses one service, continue it before expecting its upstream services to become
ready. Unlike Docker attach mode, local startup breakpoints bind before module
import. Save and restart the affected debug session after code changes; no image
build is involved.

Local storage uses `data/local/cards.db`, `agent.db` and `sessions.db`, separate
from Docker volumes. `scripts/local_service.py` translates service DNS names,
key paths and service identities into native settings without printing secrets.
Only Agent receives the Anthropic key in its process environment.

For an individual local component, keep its dependencies running and choose
**Debug Local Agent API**, **Debug Local MCP Server**, etc. These launch profiles
start a new process; stop an existing process on that port first. They do not
start dependencies themselves. The same launchers work without VS Code, using
separate terminals in this order:

```powershell
.\.venv\Scripts\python.exe scripts/local_service.py cards
.\.venv\Scripts\python.exe scripts/local_service.py mcp
.\.venv\Scripts\python.exe scripts/local_service.py agent
.\.venv\Scripts\python.exe scripts/local_service.py experience
```

Start frontend in a fifth terminal:

```powershell
$env:EXPERIENCE_PROXY_TARGET = 'http://127.0.0.1:8020'
Set-Location frontend
npm run dev:local
```

Select **Debug Local Frontend** to debug that already running local Vite server.
The local compound owns the frontend background task, not its process lifetime:
when stopping local mode, stop Python/Chrome sessions with the red Stop button,
then **Tasks: Terminate Task ? Start Local Frontend** (or Ctrl+C in its terminal).
Stop native Keycloak with Ctrl+C separately. Docker shutdown commands do not stop
native Python/Node processes. To return to Docker, release local ports first,
then run `scripts/stack.ps1 -Build` or choose the Docker compound.

## Sign in and follow a request through the stack

Use username `alex.synthetic` and `DEMO_PASSWORD` from `.env.stack` (not your
Windows/OpenAI password). Start a conversation and type **I would like a new
card**. Claude selects the registered action. Complete its private form with
synthetic given name `Alex` and the masked account ending `5678`, review, confirm.

Useful breakpoints, in request order:

1. React `submit` in `frontend/src/main.tsx`.
2. Experience `message` / `forward` in `src/card_chat/experience.py`.
3. Agent `interpret_turn` in `agent.py`, then `message` in `main.py`.
4. React `supply` and Agent `structured` for private details (no model call).
5. React `confirm`, then Agent `confirm` / `execute` for stored approval.
6. MCP `call_tool` / `execute` in `mcp_server.py`.
7. Cards `create_card` in `src/card_servicing/cards.py` before `create_idempotent`.

Cards writes only after confirmation. Long debugger pauses can trigger the
10-second downstream or 120-second outer HTTP timeout. Continue the paused
request and use the supported retry; the saved execution key prevents another
card. Do not start a new card request to recover an uncertain outcome.

## Checks and troubleshooting

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/export_cards_openapi.py --check
.\.venv\Scripts\python.exe scripts/render_chat_diagrams.py --check
# With the chosen stack running on browser port 8080:
.\.venv\Scripts\python.exe scripts/smoke_stack.py
```

- **Port in use:** stop the other deployment mode and any old local Vite task.
- **Local npm not found:** install Node on Windows and restart VS Code. Docker's Node installation does not supply host npm.
- **Identity unavailable:** start the selected Keycloak instance and verify `/realms/cards/.well-known/openid-configuration` on 8081.
- **Local Agent waits:** check Cards/MCP terminals and continue startup breakpoints. No MCP child process or stdio is used.
- **Unbound Docker breakpoint:** rebuild the image; maps are workspace `src` ? `/app/src`.
- **Unbound local breakpoint:** use the workspace venv and native launch profile, not a Docker attach profile.
- **No natural-language interpretation:** check `STACK_MODEL_PROVIDER=anthropic` and key/model settings. Stub mode uses the manual fallback.

Verified: 147 automated tests passed; four native Windows Python services started
and served health checks, and authenticated native Agent ? remote HTTP MCP ?
Cards creation succeeded with zero model calls, using the existing Keycloak
container. Only smoke-test child processes were stopped afterward; the Docker
application was left unchanged. Interactive VS Code compound/browser checks and a fully native
Keycloak installation remain manual verification steps; host Node availability
is checked explicitly before attempting local launch.

Further detail: [HTTP/security architecture](docs/http-stack.md),
[model privacy/caching/usage](docs/model-privacy.md),
[verification history](docs/http-stack-verification.md),
[OpenAPI contract](docs/cards.openapi.json). Redis, RabbitMQ and MongoDB are
optional legacy infrastructure, not required by the current HTTP application.
