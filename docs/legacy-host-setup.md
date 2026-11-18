> Historical reference only. Use the [current README](../README.md) for supported profiles and commands.

# Card Servicing

The current application is the [independent HTTP service stack](http-stack.md):
React → Experience API → Agent API → Streamable HTTP MCP → Cards API, with local
Keycloak sign-in. The guide includes Windows setup, single-component debugging,
**Debug Full Stack**, security boundaries, durable operations, and verification.

```powershell
.\scripts\setup-stack.ps1
.\scripts\stack.ps1 -Build
# Open http://localhost:8080; use alex.synthetic and DEMO_PASSWORD from .env.stack.
# With STACK_MODEL_PROVIDER=anthropic in .env.stack, start a conversation
# and type "I would like a new card". Complete the matching private form,
# review the details, then confirm. Stub mode has a manual-action fallback.
.\scripts\stack.ps1 -Stop
```

The earlier host/API and infrastructure walkthrough below is retained, including
existing local documentation edits. For that older infrastructure, use
`compose.infra.yaml`; `compose.yaml` and the VS Code launches now target the HTTP
stack. Follow the linked guide for current run/debug commands.

The current [private-input and model-context flow](model-privacy.md) keeps
raw identifiers out of Claude, uses a shared allowlist builder, and defaults
prompt caching off. Structured review, confirmation and safe replay need zero
model calls. Free text is for intent and safe corrections; private pastes are
rejected before reaching the model.

A local learning project for creating simulated cards through a browser chatbot
or REST API. The chat backend uses hosted Claude to understand customer messages
and propose a configured MCP tool. The application validates the proposal, shows
a masked review, and calls the separate Cards API only after confirmation.

```text
Browser :8020 -> Chat workflow / CardServicingAgent -> hosted Claude
                          |
                  validated review + confirmation
                          |
                    local MCP child (stdio)
                          |
                  Cards REST API :8010 -> SQLite
```

The default catalogue exposes **create_card**. Cards are simulation identifiers
(`SIM-...`), not payment network PANs. There is no physical issuance, PIN/CVV,
account lookup, authentication or production card processing. Use synthetic data.

## Quick start: clone and debug Chat + API together

These instructions use **Windows, PowerShell and VS Code**. The simplest mode
runs both Python applications locally with structured stdout telemetry: **Docker
and MongoDB are not required**. Hosted chat requires your own Anthropic API key,
network access and a Messages model available to your account; usage is billed
by Anthropic. Automated tests and the stub demo need no cloud key.

Prerequisites:

- Python 3.10+ installed and available as `python` (CI uses 3.12).
- Git, or download/extract this repository's ZIP from GitHub.
- VS Code with the **Python** and **Python Debugger** extensions.
- Docker Desktop only if you choose the optional container/infrastructure modes.

### 1. Get the repository and install dependencies

```powershell
git clone https://github.com/kishoreakaveeti/CardServicing.git
cd CardServicing
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,infra,chat]"
```

If you downloaded a ZIP, open the extracted repository folder and run the commands
starting at `python -m venv .venv`. If you already have `.venv`, activate and reuse
it rather than recreating it. If PowerShell blocks activation, you can run commands
with `.\.venv\Scripts\python.exe` directly.

### 2. Configure your local .env

For a fresh checkout, copy the example only if `.env` does not already exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit the root **`.env`**, preserving any other existing settings:

```dotenv
CARD_STORE=sqlite
SQLITE_PATH=data/cards.db
TELEMETRY_SINK=stdout
MODEL_PROVIDER=anthropic
MODEL_ID=YOUR_CLAUDE_MODEL_ID
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY
CARD_API_BASE_URL=http://127.0.0.1:8010
```

Replace the key/model placeholders locally. See [Anthropic's model IDs](https://platform.claude.com/docs/en/models/overview)
for an ID available to your account. The application has no default model ID.
`stdout` skips Mongo configuration and credentials. Environment variables override
`.env`; restart the applications after changing configuration.

`.env` and `data/` are ignored by Git. Commit only placeholder values in
`.env.example`; never place real credentials in source, documentation, browser
code, `launch.json` or logs. Before committing, `git ls-files -- .env` should print
nothing. Do not force-add `.env`.

### 3. Start both debuggers with one selection

Open this folder in VS Code, then:

1. Run **Python: Select Interpreter** and choose `.venv/Scripts/python.exe`.
2. Open **Run and Debug** (`Ctrl+Shift+D`).
3. Select **Debug Chat + API** and press **F5**.
4. Wait for **both** terminals to report `Application startup complete`:
   Cards API on **8010**, Chat on **8020**. Chat automatically starts the MCP child.
5. Open **http://127.0.0.1:8020/**. You can also run:

   ```powershell
   Start-Process 'http://127.0.0.1:8020'
   ```

`Start-Process` only opens a browser; it does not start the servers. Do not also
launch manual Uvicorn instances on these ports while the debuggers are running.

### 4. Create a simulated card

Enter `I want a new card`, then answer the questions with synthetic details:

```text
My given name is Alex, my user name is alex.synthetic,
and my existing account number is 0012345678.
```

Claude can collect several values in one message or ask for missing information.
Question wording/order can vary. Review the masked account, then click **Create
card**. Success displays the actual `SIM-...` identifier and UUID returned by the
API. Clicking the same confirmation again returns the original saved result.
Use **New conversation** for another operation. Blocking/replacement are not
production capabilities in the default catalogue.

### 5. Follow the breakpoints

| Put a breakpoint inside | What to inspect |
| --- | --- |
| `src/card_chat/main.py` -> `message`, at `c = get(cid)` | Incoming text and conversation ID; every customer message enters here |
| `src/card_chat/agent.py` -> `interpret_turn` | History, collected details and allowed tools |
| `src/card_chat/provider.py` -> `turn`, after Messages returns | Claude's text/question or proposed `tool_use` |
| `src/card_chat/main.py` -> `spec.validate` and review creation | Validated tool/arguments and masked review |
| `src/card_chat/main.py` -> `execute`, at `gateway.execute(...)` | Confirmed snapshot; no extra model call |
| `src/card_servicing/cards.py` -> `store.create_idempotent(...)` | HTTP creation and durable replay, reached after confirmation |

F10 steps over; F5 continues to the next breakpoint. Both sessions appear under
**CALL STACK**. Stepping does not automatically cross HTTP or process boundaries;
continue to the breakpoint in the receiving process. Long pauses inside REST can
exceed the ten-second client timeout; resume and retry the same confirmed
create-card submission. Do not restart chat to recover an uncertain write: its
submission key is in memory.

Debug configurations are custom names in [.vscode/launch.json](../.vscode/launch.json):

| Configuration | Behavior |
| --- | --- |
| **Debug Chat + API** | Starts Debug API and Debug Chat together; stopping one stops both |
| **Debug API** | Starts the host Cards API on 8010 |
| **Debug Chat** | Starts host Chat on 8020 and manages its MCP child |
| **Attach to Docker API** | Attaches to an already-running debug API container on 5679 |

MCP automatic child attachment is disabled (`subProcess:false`) to avoid the
previous stdio startup problem. Chat/agent/model and API breakpoints still work;
inspect the MCP boundary in `gateway.execute` or attach to its child separately.

## Addresses: which page should I open?

| Purpose | Host-run API | Docker-run API |
| --- | --- | --- |
| Chat window | http://127.0.0.1:8020/ | http://127.0.0.1:8020/ (chat still runs on host) |
| Cards Swagger | http://127.0.0.1:8010/docs | http://127.0.0.1:8011/docs |
| API readiness | http://127.0.0.1:8010/health/ready | http://127.0.0.1:8011/health/ready |
| MCP destination setting | `CARD_API_BASE_URL=http://127.0.0.1:8010` | `CARD_API_BASE_URL=http://127.0.0.1:8011` |

The API has no page at `/`: opening port 8010 or 8011 without `/docs` can correctly
return **Not Found**. The chat page is on 8020. MCP uses stdio, not an HTTP port.

## Run without VS Code debugging

Use two terminals, activate the same environment in each, and keep `.env` as above.
Do not run these in parallel with the compound debugger.

Terminal 1:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn card_servicing.main:app --host 127.0.0.1 --port 8010
```

Terminal 2:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn card_chat.main:app --host 127.0.0.1 --port 8020
```

Wait for both startup messages, then open 8020. Stop each terminal server with
**Ctrl+C**. For VS Code sessions use the red Stop button; the compound stops both.
To switch from terminal runs to debugging, stop the terminal servers first.

## Run without an Anthropic key

Tests require neither Docker nor cloud credentials:

```powershell
python -m pytest
python scripts/export_cards_openapi.py --check
```

For a browser demonstration with a scripted model and isolated HTTP/MCP processes:

```powershell
python scripts/chat_stub_smoke.py
```

Open 8020. In order, enter `freeze card`, `new card`, `Alex / alex.synthetic`,
`0012345678`, then click Create card twice. Replies are scripted in that order;
restart the harness to repeat. It uses an isolated in-memory API, not your card
database. Stop any existing chat server before starting this harness.

To verify a separately running API through chat and real MCP, using a stub model:

```powershell
python scripts/smoke_chat.py --api-url http://127.0.0.1:8010 --lose-first-response
```

Add `--live` for the configured Anthropic model. The smoke deliberately loses the
first creation result, then checks that retry returns the original card. It uses
synthetic data and live mode incurs cloud usage.

## Optional Docker API and MongoDB

The host **Debug Chat + API** mode above needs no containers when using stdout.
To run the API in Docker instead, stop the host API and use Docker Desktop:

```powershell
$env:TELEMETRY_SINK = 'stdout'
.\scripts\start-docker-api.ps1 -Standalone -DebugApi -Build
```

Set `CARD_API_BASE_URL=http://127.0.0.1:8011` in `.env`, then start **Attach to Docker
API** and **Debug Chat** as separate sessions. Do not use **Debug Chat + API** in
this mode: that compound starts a second host API. The container's SQLite mount
uses the same `data/cards.db`; use one API execution mode per database at a time.

For the original Mongo/Redis/RabbitMQ development infrastructure, use
`scripts/setup-infra.ps1`. If `.env` is absent it generates local infrastructure
credentials from the example. If `.env` already exists it preserves its values:
replace any `GENERATE_ME` password/hash placeholders before starting those services.
Do not overwrite a configured `.env` to change modes. Set `TELEMETRY_SINK=mongo`
for Mongo telemetry, then use the normal startup script (without `-Standalone`).
See [infrastructure setup](local-infrastructure.md),
[Docker debugging](docker-debugging.md), and
[the same-build Mongo/stdout switch](infrastructure-adapters.md) for full steps.

| Setting | Default / supported values |
| --- | --- |
| `CARD_STORE` | `sqlite` only |
| `SQLITE_PATH` | `data/cards.db` |
| `TELEMETRY_SINK` | `mongo` (default), `stdout`, `none` |
| `MODEL_PROVIDER` | `anthropic` |
| `MODEL_ID`, `ANTHROPIC_API_KEY` | Required for live chat; server-side only |
| `CARD_API_BASE_URL` | `http://127.0.0.1:8010`; optional gateway prefix supported |
| `TOOL_CATALOGUE_PATH` | Optional; defaults to `src/card_chat/tools.json` |

## REST contract and persistence

`POST /cards` requires `Idempotency-Key`, for example `card-demo-001`:

```json
{"user_name":"alex.synthetic","given_name":"Alex","account_number":"0012345678"}
```

Returns **201**, a UUID `id`, `SIM-...` card number, created status and timestamp.
Account identifiers stay strings, including leading zeros. Invalid requests return
422 before writes. The same key and normalized body replay the original 201;
a changed body with that key returns 409. Storage failures return sanitized 503.
Responses include `Idempotency-Replayed`, `X-Trace-ID`, and `Cache-Control:no-store`.

SQLite stores cards and durable replay snapshots in `data/cards.db`. MongoDB, when
selected, stores diagnostics rather than cards or chat context. Conversation
history, proposals and submission keys are in memory; use a single chat worker
without reload. Redis/RabbitMQ are provisioned for future work, outside creation.
The `/items` routes remain simple API learning examples.

## Catalogue, documentation and CI

The versioned [tool catalogue](../src/card_chat/tools.json) references stable OpenAPI
operation IDs. Claude selects from discovered tools, asks questions, and proposes
one action. The workflow validates and requires confirmation before a write.
For supported JSON-body/path-parameter operations, adding an entry and restarting
chat requires no server/agent/UI branch. See [catalogue authoring](tool-catalogue.md).

Read the diagrams directly in [architecture.md](architecture.md); maintain
that document and its diagrams manually.

The [walkthrough](chat-end-to-end.md) traces every boundary and idempotency.
To update the API export after changing its source:

```powershell
python scripts/export_cards_openapi.py
```

[GitHub Actions CI](../.github/workflows/ci.yml) runs on pushes and pull requests:
install dependencies, check debugging-guide freshness, run pytest, check OpenAPI
freshness, and test/build the frontend. Architecture Markdown and its embedded
diagrams are maintained manually; CI does not generate or update them.

## Troubleshooting and further reading

| Symptom | Check |
| --- | --- |
| Site cannot be reached | Both servers must finish startup; opening the URL alone starts nothing |
| Not Found on 8010/8011 | Use `/docs` for the API, or 8020 for chat |
| Address already in use | Stop the prior terminal/debug instance before launching another |
| Chat replies but no breakpoints | Check CALL STACK includes Debug Chat and the browser reaches that process |
| API breakpoint does not hit during questions | Expected: the API is called after confirmation |
| Chat fails during startup | Run `python -m card_chat.catalogue`; inspect terminal errors before any MCP connection-closed message |
| Language-service error | Check local key, accessible model ID, network access and environment overrides without logging secrets |

More detail: [chat guide](chat-demo.md), [architecture](architecture.md),
[adapter configuration](infrastructure-adapters.md),
[persistence](card-persistence.md), [health checks](health-checks.md),
[infrastructure viewers](local-infrastructure.md), and
[Docker debugging](docker-debugging.md).
