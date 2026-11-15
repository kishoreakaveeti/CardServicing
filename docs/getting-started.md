# Setup, running and debugging

This is the complete Windows/PowerShell setup and operations guide. Start with the [application overview](../README.md) for features and the technology stack.

- [First-time setup and prerequisites](#shared-first-time-setup)
- [Run and debug in Docker](#1-run-and-debug-using-local-docker-containers)
- [Run and debug natively](#2-run-and-debug-natively-on-your-computer)
- [Switch between modes](#switch-between-modes)
- [Customer journey and verification](#customer-journey-and-verification)
- [Troubleshooting](#troubleshooting)

## Shared first-time setup

### Installation prerequisites

| Software | Docker application mode | Native application mode |
| --- | --- | --- |
| Git | Clone the repository, or download its ZIP instead | Same |
| Python with `venv`, `pip`, `sqlite3` | Host Python is required by this repository's setup scripts; Python 3.13 matches its container image | Required for the four Python services; use Python 3.13 for alignment. The package declares Python 3.10+ |
| PowerShell | Required by the supplied Windows setup/debug scripts | Same |
| Docker Desktop with Linux containers and Compose | Required and running | Recommended for Identity/Mongo dependencies only; optional when dependencies run natively/externally |
| Node.js 22 with npm | **Not required on the host**; the frontend image provides it | **Required on Windows** for Vite; restart VS Code after installation to refresh PATH |
| VS Code, Microsoft Python and Python Debugger extensions | Required for the documented attach workflow; JavaScript debugging is built in | Required for the documented launch workflow; not required for terminal-only execution |
| Google Chrome | Required by shipped browser debug profiles; normal runs can use another browser | Same |
| Separate SQLite server/CLI/viewer | **Not required** | **Not required** |
| MongoDB Community Server on Windows | **Not required**; Compose runs `mongo:8.0` | Only needed if you choose native MongoDB logging; Docker Mongo or console logging need no host Mongo installation |
| MongoDB Compass | Optional viewer, not the database server | Same |
| Native Keycloak and OpenJDK | **Not required**; supplied by the Identity container | Only needed if you choose to run Identity without Docker |
| Anthropic API key | Optional; start with the credential-free stub | Same |

**SQLite does not need a separate installation.** The application uses Python's `sqlite3` module and automatically creates/migrates its database files. SQLite requires no server process. Standard Python installers normally include the module; verify it below. If your Python distribution omits it, install a distribution that provides it. Installing a database viewer alone does not add Python SQLite support. See [Python's SQLite documentation](https://docs.python.org/3/library/sqlite3.html).

Official installation references: [Python for Windows](https://www.python.org/downloads/windows/), [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/), [Node.js](https://nodejs.org/en/download), and [VS Code Python debugging](https://code.visualstudio.com/docs/python/debugging). Enable Python/Node PATH options when installing, then fully restart VS Code.

### Clone, install dependencies and generate credentials

Run in PowerShell. If you downloaded a ZIP, open its extracted repository folder and skip the clone command.

```powershell
git clone https://github.com/kishoreakaveeti/CardServicing.git
Set-Location CardServicing
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,chat,infra]' debugpy
.\.venv\Scripts\python.exe -c "import sqlite3; print('SQLite:', sqlite3.sqlite_version)"
if (-not (Test-Path -LiteralPath '.env')) {
    '# Optional settings; see .env.example.' | Set-Content -LiteralPath '.env' -Encoding ascii
}
.\scripts\setup-stack.ps1
```

If PowerShell blocks `setup-stack.ps1`, run `.\.venv\Scripts\python.exe scripts/setup_stack.py` instead; the result is the same and no execution-policy change is required.

If only the Windows `py` launcher is available, create the environment with `py -3.13 -m venv .venv`, then use the explicit `.venv` interpreter above. No environment activation is required. Open the **repository root** in VS Code; use **Python: Select Interpreter** to select `.venv/Scripts/python.exe`.

Setup generates ignored `.env.stack` with credentials, plus `data/identity/` with the imported realm and signing keys. It preserves existing credentials and keys. Do not commit these files, private keys, `.env`, or local databases to the public repo. Keep the generated credentials consistent with existing Identity/Mongo volumes. `.env.example` is a reference: its `GENERATE_ME` values are not ready-to-use credentials.

### Configuration shared by both modes

| Setting | Default / location | Purpose |
| --- | --- | --- |
| `STACK_MODEL_PROVIDER` | `stub`; optionally set in `.env.stack` | Stub supports direct actions and a small deterministic set of common intents; `anthropic` enables hosted LLM interpretation |
| `MODEL_ID`, `ANTHROPIC_API_KEY` | Set in ignored `.env` when using Anthropic | Use a model available to your account. Only Agent calls the provider; Docker forwards the key only to Agent |
| `TELEMETRY_SINK` | `mongo`; optionally set in `.env.stack` | `mongo`, `stdout` for console diagnostics, or `none` |
| `MONGO_USER`, `MONGO_PASSWORD`, `ACCOUNT_HASH_KEY` | Generated in `.env.stack` | Mongo authentication and optional account hashing; setup preserves original `.env` credentials when available |
| `MONGODB_DATABASE` | `card_servicing` | Diagnostics database; business data remains in SQLite |
| `MONGODB_HOST_PORT` | `27018`; optional `.env.stack` override | Published Docker Mongo port and native application's Mongo destination port |
| `LOCAL_MONGODB_HOST` | `127.0.0.1`; native mode only | Native/external Mongo host |
| `LOCAL_OIDC_ISSUER` | `http://localhost:8081/realms/cards`; native mode only | Override only for a compatible external Identity deployment |
| `ANTHROPIC_PROMPT_CACHE` | `false`; optional `.env` setting | Optional static-prefix caching |

A first run needs no model key: leave `stub` selected. It is not a general conversational model. To enable Claude, edit these files and replace the placeholders:

```dotenv
# .env.stack
STACK_MODEL_PROVIDER=anthropic
```

```dotenv
# .env
MODEL_ID=your-supported-Claude-model
ANTHROPIC_API_KEY=your-key
```

For the supplied launchers, shell environment variables override file settings, and `.env.stack` overrides the same keys in `.env`. Remove stale shell overrides when diagnosing configuration. Compose already sets Docker DNS names, internal URLs, key mounts and SQLite paths. `local_service.py` sets native equivalents; do not copy Docker hostnames such as `cards` or `mongodb` into native destinations.

## 1. Run and debug using local Docker containers

### Prerequisites and configuration

Complete the shared setup. Install/start Docker Desktop with Linux containers. Verify `docker version` and `docker compose version` in a new terminal. `stack.ps1` also checks standard Docker Desktop paths if the CLI is not on PATH.

**No Windows MongoDB Community Server, SQLite server, Node/npm, native Keycloak or JDK installation is required for this mode.** Docker supplies the runtimes and database/Identity services. Host Python is still used for the shared setup scripts. VS Code debugging requires the extensions and Chrome listed above.

Before switching from native debugging: press **Shift+F5**, then stop the **Start Local Frontend** task using its Terminal trash-can button or **Tasks: Terminate Task**. This Vite task can outlive the debug sessions and occupy 8080. Resolve any other process using 8080, 8081 or the selected Mongo host port.

### Run without debugging

From the repository root:

```powershell
.\scripts\stack.ps1 -Build
```

Wait for success, then open **http://localhost:8080**. Use `localhost` consistently for browser access and sign-in. Compose starts frontend, Experience, Agent, MCP, Cards, Identity and MongoDB, with persistent volumes. Runtime images do not enable debugger listeners.

**8080 is the UI port.** Port 8000 is internal to the API containers. Backend HTTP ports are not published in normal mode; services communicate through Compose DNS. Identity uses host 8081 and Mongo uses loopback 27018 by default.

### Debug the full stack and attach VS Code

1. Stop conflicting native processes and the native Vite task as described above.
2. Ensure Docker Desktop is running; open the repository root in VS Code.
3. Open **Run and Debug** (**Ctrl+Shift+D**), select **Debug Full Stack (Docker)** and press **F5**.
4. Wait for **Start Full Debug Stack** to build/start debug images and check health/listeners. The first build can take several minutes. Do not bypass a failed task with **Debug Anyway**.
5. VS Code attaches to six containerized Python processes and opens Chrome at `http://localhost:8080`.
6. Set breakpoints in your existing local source files and trigger a browser action. Local `src` maps to `/app/src` inside the containers.

**You do not need to start the stack separately before F5.** That profile's shared pre-launch task runs the equivalent of:

```powershell
.\scripts\stack.ps1 -DebugComponent All -Build
```

`Debug Full Stack` is a compatibility alias for the Docker profile. Debug images include `debugpy` and a Vite frontend with source maps. HTTP starts without waiting for debugger attachment.

| Attach profile | Host debugger port | Container listener | Container name |
| --- | --- | --- | --- |
| Attach to Experience API | 5681 | 5678 | `card-servicing-experience` |
| Attach to Agent API | 5682 | 5678 | `card-servicing-agent` |
| Attach to MCP Server | 5683 | 5678 | `card-servicing-mcp` |
| Attach to Cards API | 5684 | 5678 | `cards-api` |
| Attach to FinCrime API | 5685 | 5678 | `fincrime-api` |
| Attach to FinCrime Agent | 5686 | 5678 | `fincrime-agent` |

### Attach to one container

For example:

```powershell
.\scripts\stack.ps1 -DebugComponent Agent -Build
```

Select **Attach to Agent API**, then F5. Substitute `Experience`, `MCP` or `Cards` and choose the matching attach profile. Individual attach profiles connect to an existing listener; they do not start containers. **Debug Frontend** only opens Chrome against an already running stack.

For Agent/MCP startup/discovery breakpoints, first start a healthy debug stack, then:

```powershell
.\scripts\stack.ps1 -DebugComponent MCP -Build -WaitForDebugger
```

Select **Attach to MCP Server**, press F5 and continue. This special command restarts only that service with `--no-deps` and waits for its debugger rather than HTTP. Use `Agent` similarly.

### If MongoDB is not installed on Windows

Normal Docker startup already handles this: it downloads `mongo:8.0`, starts it and preserves diagnostics in the stack's `mongodb_data` volume. Compass is optional. If another Mongo server occupies 27018, stop that server or change `MONGODB_HOST_PORT` in `.env.stack`.

To send diagnostics to the console, set this in `.env.stack`, then restart/recreate application services:

```dotenv
TELEMETRY_SINK=stdout
```

Use `none` to discard telemetry. **The full Compose configuration still starts Mongo with either override**, because Mongo remains a configured service and Cards has a startup dependency on it. The override changes the logging destination, not the container list. To run with no Mongo server/container at all, use native application mode with `stdout`/`none` below.

Default collections: `card_servicing.application_logs` and `card_servicing.request_traces`. Optional Compass connection: `127.0.0.1:27018`, authentication database `admin`, credentials from `.env.stack`. See [Mongo logging](mongodb-logging.md).

### Stop, rebuild and inspect

**Shift+F5** disconnects debuggers but leaves containers running. Images contain source snapshots: after code changes, disconnect and launch **Debug Full Stack (Docker)** again to rebuild, or run the debug command with `-Build` and reattach. Restarting an unchanged image does not include edited code.

```powershell
# Return to runtime images without debugger listeners.
.\scripts\stack.ps1 -Build
# Remove containers/network; preserve named volumes.
.\scripts\stack.ps1 -Stop
# Status/logs; direct docker commands require Docker on PATH.
docker compose --env-file .env --env-file .env.stack -f compose.yaml ps
docker compose --env-file .env --env-file .env.stack -f compose.yaml logs --tail 50 agent mcp cards
```

Docker SQLite files live in named volumes, separate from native `data/local/` files. Customers registered natively do not automatically appear in Docker: register again in the chosen environment. Do not use `down -v` unless you intend to erase persistent data.

## 2. Run and debug natively on your computer

### Prerequisites and configuration

Complete shared setup. Install **Node.js 22 with npm on Windows**, fully restart VS Code, then verify/install:

```powershell
node --version
npm.cmd --version
.\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.sqlite_version)"
Push-Location frontend
npm.cmd ci
Pop-Location
```

Use `npm.cmd` if PowerShell blocks `npm.ps1`; no execution-policy change is needed for npm. **No separate SQLite installation is required.** Vite and the four Python services run natively, with no application image builds or remote Python attach ports.

Choose an infrastructure option:

| Option | Identity | Logging | Host installations |
| --- | --- | --- | --- |
| Recommended native development | Docker Keycloak | Docker MongoDB | Python, Node/npm, Docker Desktop; no native Mongo/JDK needed |
| No MongoDB at all | Docker or native Keycloak | `TELEMETRY_SINK=stdout` or `none` | No MongoDB Community Server/Compass needed |
| No Docker at all | Native Keycloak 26.4.7 | Native Mongo or console/disabled logging | OpenJDK 21 and native Keycloak; MongoDB Community Server only if logging to native Mongo |

**Identity remains required with simple customer registration/login** because it secures the existing service-to-service architecture. Disabling Mongo logging does not remove authentication.

### Prepare Identity and MongoDB

With Docker Desktop running, the local F5 preparation task starts only missing local Identity/Mongo dependencies and reuses healthy ones. It does not start Docker application services. For terminal-only execution, prepare dependencies explicitly:

```powershell
.\.venv\Scripts\python.exe scripts/local_infrastructure.py
```

Or use Compose directly:

```powershell
docker compose --env-file .env --env-file .env.stack -f compose.yaml up -d --wait identity mongodb
```

If switching from Docker application mode, disconnect its debuggers and stop only its application containers, retaining dependencies:

```powershell
docker compose --env-file .env --env-file .env.stack -f compose.yaml stop frontend experience agent mcp cards
```

#### If you do not install or run MongoDB

Before starting local F5 or terminals, set this in `.env.stack`:

```dotenv
TELEMETRY_SINK=stdout
```

The local infrastructure task then prepares Identity only and does not start/require Mongo. `stdout` keeps diagnostics in terminals; `none` discards them. An already running Mongo container is not automatically stopped by this setting. SQLite still stores all business data.

#### If you choose native MongoDB Community Server

1. Install MongoDB Community Server **8.0** to match Compose, following [MongoDB Community Server for Windows](https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/). Compass alone is a viewer, not the server; administrative commands may also require `mongosh`.
2. Start its Windows service/server with loopback binding. The repository defaults to port **27018**; a native installation commonly uses **27017**, so configure the actual port below.
3. Follow MongoDB's [access-control instructions](https://www.mongodb.com/docs/manual/tutorial/enable-authentication/). Create an `admin`-authenticated application user with read/write and index-creation permissions for `card_servicing`. The application's Mongo client uses `authSource=admin`. Generated Docker credentials do not automatically create a native Mongo user.
4. Configure `.env.stack` to match that server/user:

```dotenv
TELEMETRY_SINK=mongo
LOCAL_MONGODB_HOST=127.0.0.1
MONGODB_HOST_PORT=27017
MONGODB_DATABASE=card_servicing
MONGO_USER=your-existing-Mongo-user
MONGO_PASSWORD=your-existing-Mongo-password
```

Start native Mongo before F5; local preparation reuses the listening service. Do not also publish a Docker Mongo container on the same port. If you do not want native database setup, choose Docker Mongo or console logging.

#### If you want no Docker at all

Use console logging above, or start the configured native Mongo service. Install **OpenJDK 21** and the **Keycloak 26.4.7 ZIP**, matching this repository's pinned container version. Consult version-specific [release notes](https://www.keycloak.org/docs/26.4.7/release_notes/index.html), [ZIP startup](https://www.keycloak.org/getting-started/getting-started-zip) and [realm import](https://www.keycloak.org/server/importExport) instructions. The latest-version quickstart may specify a different JDK; use one supported by the version you install.

After shared setup generated `data/identity/realm.json`, open a separate PowerShell terminal at the repository root and adjust this installation path:

```powershell
java -version
$repoRoot = (Get-Location).Path
$keycloakDir = 'C:\Tools\keycloak-26.4.7'
New-Item -ItemType Directory -Force "$keycloakDir\data\import" | Out-Null
Copy-Item -LiteralPath "$repoRoot\data\identity\realm.json" -Destination "$keycloakDir\data\import\cards-realm.json"
& "$keycloakDir\bin\kc.bat" start-dev --http-host=127.0.0.1 --http-port=8081 --hostname=http://localhost:8081 --import-realm
```

Keep it running. Verify `http://localhost:8081/realms/cards/.well-known/openid-configuration` responds. Use a fresh Keycloak data directory for the first import; importing does not overwrite an existing realm. Imported clients/secrets must match `.env.stack`. Do not also run Docker Identity on 8081.

When Identity and any selected Mongo service are already available, local preparation needs no Docker. An unavailable custom `LOCAL_OIDC_ISSUER` is not silently replaced with a local container. A custom realm must provide the same service clients, audiences/scopes and browser callback (`http://localhost:8080/auth/callback`) as the generated realm.

### Run locally without debugging

Prepare dependencies above. From the repository root, check prerequisites:

```powershell
.\.venv\Scripts\python.exe scripts/check_local.py
```

Open **six separate PowerShell terminals at the repository root**, one command per terminal, in this order. Keep them running:

```powershell
# Terminal 1 — Cards Banking API (8010)
.\.venv\Scripts\python.exe scripts/local_service.py cards
```

```powershell
# Terminal 2 — FinCrime API (8025)
.\.venv\Scripts\python.exe scripts/local_service.py fincrime
```

```powershell
# Terminal 3 — MCP Tool Server (8030)
.\.venv\Scripts\python.exe scripts/local_service.py mcp
```

```powershell
# Terminal 4 — FinCrime Agent (8022)
.\.venv\Scripts\python.exe scripts/local_service.py fincrime_agent
```

```powershell
# Terminal 5 — Cards Agent / Orchestrator (8021)
.\.venv\Scripts\python.exe scripts/local_service.py agent
```

```powershell
# Terminal 6 — Experience BFF (8020)
.\.venv\Scripts\python.exe scripts/local_service.py experience
```

In a **seventh terminal**, start Vite:

```powershell
$env:EXPERIENCE_PROXY_TARGET = 'http://127.0.0.1:8020'
Set-Location frontend
npm.cmd run dev:local
```

Wait for startup, then open **http://localhost:8080**. No debugger is attached. Python launchers wait for Identity and their downstream dependencies. Stop these terminal-run services before launching another copy through F5.

### Debug locally from VS Code

1. Stop terminal-run Python/Vite processes and conflicting Docker application containers. Leave the selected dependencies running.
2. Open the repository root; select `.venv/Scripts/python.exe` with **Python: Select Interpreter**.
3. Select **Debug Full Stack (Local)** in **Run and Debug** and press **F5**.
4. Preparation starts missing local infrastructure, validates prerequisites/free ports and starts Vite.
5. VS Code launches six native Python debuggers in separately named service consoles (`Local Experience API`, `Local Agent API`, `Local FinCrime Agent`, `Local MCP Server`, `Local Cards API`, `Local FinCrime API`).
6. Experience startup reports its listening address to VS Code. A `serverReadyAction` then launches the browser debugger at `http://localhost:8080`.
7. Set breakpoints in local source and trigger an action. For model-provider breakpoints, select Anthropic and send a chat intent; direct buttons/forms intentionally bypass the model.

Individual **Debug Local Experience API**, **Debug Local Agent API**, **Debug Local FinCrime Agent**, **Debug Local MCP Server**, **Debug Local Cards API**, and **Debug Local FinCrime API** profiles launch only that process; dependencies must already run. The browser-only **Debug Local Frontend** profile is hidden from the normal picker because it does not start the app. Use local launch profiles, not Docker **Attach to...** profiles, for native execution.

Do not choose **Debug Anyway** after a failed prerequisite: it only skips the check and can open a browser against an unavailable server. Inspect the named task/service terminal. If an older debug session still shows **Wait for Local HTTP**, stop that session and its Vite task before starting the updated local profile. `KeyboardInterrupt` means a wait/process was interrupted; the startup message immediately before it identifies the awaited dependency. Loopback readiness requests bypass HTTP proxies.

### Ports, persistence and shutdown

| Component | Native endpoint | Purpose |
| --- | --- | --- |
| Frontend | `http://localhost:8080` | Banking UI; proxies `/api` and `/auth` to Experience |
| Identity | `http://localhost:8081/realms/cards` | OIDC issuer |
| Cards API | `http://127.0.0.1:8010/health/ready` | Banking API readiness |
| FinCrime API | `http://127.0.0.1:8025/health/ready` | Risk & card blocking/unblocking API readiness |
| MCP Server | `http://127.0.0.1:8030/health` | HTTP tool service |
| FinCrime Agent | `http://127.0.0.1:8022/health` | Financial Crime agent microservice |
| Cards Agent | `http://127.0.0.1:8021/health` | Conversation & orchestrator |
| Experience | `http://127.0.0.1:8020/health` | Session/BFF; not the banking UI |
| Mongo | `127.0.0.1:27018` by default | Diagnostics; unnecessary with `stdout`/`none` |

Native files `data/local/cards.db`, `fincrime.db`, `agent.db` and `sessions.db` are created automatically. Save and restart the affected Python debug session after edits; no image build is needed. Vite serves frontend edits during development.

**Shift+F5** stops the compound's Python/browser sessions. Then stop **Start Local Frontend** with its Terminal trash-can button or **Tasks: Terminate Task**; that background task may remain running. For terminal execution, press Ctrl+C in each application terminal. Stop native Keycloak separately. Docker shutdown commands do not stop native Python/Node processes.

## Switch between modes

| Direction | Steps |
| --- | --- |
| Native → Docker | Stop native debuggers/terminal processes **and Vite**, freeing 8080. Keep existing Docker dependencies. Stop conflicting native Identity/Mongo services or resolve published-port conflicts. Select **Debug Full Stack (Docker)** and F5, or run `stack.ps1 -Build` |
| Docker → native | Disconnect Docker debuggers; stop `frontend experience cards-agent fincrime-agent mcp-server cards-api fincrime-api` with the native-section Compose command. Keep Docker dependencies if desired. Select **Debug Full Stack (Local)** and F5, or use the seven-terminal instructions |

SQLite state is separate in the two modes. Register/log in again after switching; use the same `localhost` browser URL. A native Keycloak installation also has a separate Identity store from Docker. Preserve the corresponding realm credentials/signing keys.

## Customer journey and verification

1. Open `http://localhost:8080`. Select **Register / Sign up**, or type **I want to register**. Use synthetic details: username starts with a letter, 3-64 letters/numbers/`_`/`.`/`-`; password 10-128 characters.
2. Select **View my accounts**. An empty list offers **Create account**. Create a `savings` or `transaction` account, optionally request a card, review and confirm.
3. Select **View my cards**. An ordinary repeat returns the existing card. To intentionally create another, select the additional-card option in a fresh workflow and confirm.
4. Sign out/in and verify persistence. Register a second synthetic customer and verify isolated lists.

The optional **OIDC sign in** retains original demo identities `alex.synthetic`/`sam.synthetic` with `DEMO_PASSWORD` from `.env.stack`. They are distinct from customers registered in SQLite. Credentials/private identifiers belong in secure forms, not chat.

Useful breakpoints: React `submit`/`supply`/`confirm`; Experience `forward`; Agent `interpret_turn`, `structured`, `confirm`/`execute`; MCP `call_tool`/`execute`; Cards `issue_card`/`create_card`; SQLite `issue_idempotent`. Forms/confirmation bypass the model. Debugger pauses may exceed HTTP timeouts: continue and retry the same submission, retaining its durable operation key.

Automated checks from the repository root need no running Docker stack:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/export_cards_openapi.py --check
.\.venv\Scripts\python.exe scripts/render_chat_diagrams.py --check
Push-Location frontend
npm.cmd ci
npm.cmd test
npm.cmd run build
Pop-Location
```

Frontend checks need host Node/npm; Docker-only users can build the frontend image instead. With Mongo running, `.\.venv\Scripts\python.exe scripts/check_mongo_logging.py` verifies synthetic log/trace delivery. `scripts/smoke_stack.py` is the original OIDC smoke and needs the chosen stack running at 8080.

Verification scope: automated tests cover authentication, ownership, direct/conversational workflows, confirmation, concurrent duplicate prevention, replay, persistence and redaction. Native VS Code full-stack launch, a live registration-form render, and Mongo queue-to-container delivery have been verified. Fully native Keycloak/Mongo installation remains an optional path not end-to-end verified here. Verify the full Docker debug launch in your Docker Desktop environment after building. Automated tests do not require live hosted-Claude calls.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| UI cannot be reached | Check the selected full-stack profile, frontend and Identity health. npm alone does not start the app. Use 8080, not internal 8000 or BFF 8020 |
| Browser has not opened | The browser waits for Experience to report that it is listening. Inspect **Start Local Infrastructure**, **Check Local Prerequisites**, **Start Local Frontend** and the Python terminals; continue startup breakpoints. The old HTTP-wait pre-launch task has been removed |
| `KeyboardInterrupt` connecting | The process was stopped while waiting. Read the preceding awaited URL and start/check that dependency |
| npm missing | Install host Node/npm and fully restart VS Code. Verify `node --version`, `npm.cmd --version`; Docker's Node does not supply host npm |
| PowerShell blocks npm | Use `npm.cmd`. For repository `.ps1` scripts, follow your machine's policy; Python equivalents such as `scripts/setup_stack.py` are available |
| Port in use | Stop the other mode and leftover Vite tasks. Do not run native/Docker dependencies on the same ports |
| Docker CLI missing | Install/start Docker Desktop, restart terminal. Repository wrappers check standard paths; direct Compose commands need Docker on PATH |
| Identity unavailable | Verify discovery on 8081 and imported realm credentials. Imports do not overwrite existing realms; reconcile older demo service scopes with `scripts/sync_identity_scopes.py` when needed |
| Mongo logs missing | Check sink, credentials, host/port, `authSource=admin` and database/index permissions. Mongo stores diagnostics, not accounts/cards |
| SQLite import fails | Use a Python distribution with `sqlite3`. Installing a SQLite server/viewer cannot fix the missing Python module |
| Docker breakpoint unbound | Rebuild the debug image and use attach profiles; mapping is local `src` → `/app/src` |
| Native breakpoint unbound | Use the workspace interpreter and local launch profile, not a Docker attach profile |
| Limited chat interpretation | Stub supports a few common intents. Configure Anthropic plus a valid key/model for hosted interpretation |

## Further documentation

- [HTTP/security architecture](http-stack.md), [architecture overview](architecture.md)
- [Customer banking](customer-banking.md), [card issuance](card-issuance.md)
- [Mongo logging](mongodb-logging.md), [SQLite persistence](card-persistence.md)
- [Tool catalogue](tool-catalogue.md), [end-to-end trace](chat-end-to-end.md)
- [Docker debugging](docker-debugging.md), [diagram viewer](diagrams/index.html)

Use `compose.yaml` plus optional `compose.debug.yaml` for the current application. `compose.infra.yaml`, `compose.api.yaml` and [legacy host setup](legacy-host-setup.md) describe earlier API-only learning configurations. Redis/RabbitMQ from that earlier stack are not required by the current banking flow. Do not infer current VS Code profile names from historical guides.

Authentication forms report a clear service-unavailable error for empty/non-JSON proxy responses. `npm.cmd run dev:local` targets native Experience on 8020 by default; `EXPERIENCE_PROXY_TARGET` remains an optional override.

Each native Python profile has a unique `consoleName` to prevent simultaneous debug launches from sharing the generic Python Debug Console. Experience output is also redirected to the Debug Console so the server-ready browser trigger can observe its listening message.
