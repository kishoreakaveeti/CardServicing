# Independent HTTP stack

Card issuance outcomes now distinguish created, already-existing and explicitly additional cards across both HTTP paths. See [card issuance](card-issuance.md).

The stack also supports local customer registration/password login alongside the existing OIDC demo login. See [Customer banking](customer-banking.md) for the complete journey, service-token policy for BFF banking calls, new routes and database ownership checks. Banking direct-selection buttons bypass the LLM and reuse the existing reviewed tool workflow.

The current path is React/TypeScript → Experience FastAPI → Agent FastAPI →
remote MCP Streamable HTTP → Cards FastAPI. Agent alone calls Anthropic over
HTTPS. Five application containers and a sixth Keycloak container have separate
entry points, health checks and Compose service names. Only frontend 8080 and
Keycloak 8081 are published in normal mode, both on loopback. No MCP child
process or stdio transport exists in the application flow.

The [HTTP identity and jobs diagram](diagrams/06-http-auth-and-jobs.svg) shows
the complete stack; the other generated diagrams follow individual workflow steps.

## Windows quick start

Prerequisites: Docker Desktop using Linux containers, PowerShell, Python 3.10+
and the VS Code Python Debugger and JavaScript Debugger extensions. Host Node is
optional; frontend builds and type checks run in Docker with Node 22.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,chat,infra]'
.\scripts\setup-stack.ps1
.\scripts\stack.ps1 -Build
```

Setup is idempotent: it preserves existing credentials and signing keys. It
generates ignored `.env.stack` and `data/identity/{realm.json,delegation-*.pem}`.
The realm imports at first Keycloak startup; subsequent imports do not overwrite
an existing realm. `scripts/sync_identity_scopes.py` reconciles demo service
scopes if upgrading an earlier local realm. Keep `.env.stack`, the key pair and
the Keycloak volume together. Existing root `.env` is preserved and read for
model configuration. No secret is printed by setup or copied into images.

Open **http://localhost:8080** (use `localhost`, not `127.0.0.1` for OIDC).
Sign in with `alex.synthetic` or `sam.synthetic` and the `DEMO_PASSWORD` value
in `.env.stack`. With `STACK_MODEL_PROVIDER=anthropic`, click **Start conversation**
and type **"I would like a new card"**. Claude identifies the permitted action
and the UI opens its matching private-details form. Enter the synthetic given
name `Alex`, choose the masked account ending `5678`, then
click **Review action** and **Create card**. The username is supplied from verified
identity; the actual account value stays server-side. Do not paste private values into
chat. Sam's masked account ends `5432`; a signed-in Sam cannot use Alex's scoped
reference. Intent interpretation uses a model call; completing the private form,
review, confirmation and safe retry make no additional model calls. The secondary
**Choose an action manually** panel retains the complete zero-model path.
Default `STACK_MODEL_PROVIDER=stub` is an
explicit test adapter; it does not provide natural-language understanding.
For Claude, set `STACK_MODEL_PROVIDER=anthropic` in `.env.stack` and set
`ANTHROPIC_API_KEY` and `MODEL_ID` in ignored `.env`, then rerun startup. Only
Agent receives the Anthropic key. Model selection remains behind `Model.turn`.
See [private inputs, model context, caching and usage](model-privacy.md).

```powershell
# Stop and remove this stack's containers/network; keep all persistent volumes.
.\scripts\stack.ps1 -Stop
# Rebuild/recreate just one component; other components keep running.
docker compose --env-file .env --env-file .env.stack up -d --build --no-deps agent
# The script finds Docker Desktop even when docker is absent from PATH.
```

`compose.infra.yaml` retains the earlier Mongo/Redis/RabbitMQ/API stack under its
original project name. `compose.api.yaml` retains the standalone learning API.
Those legacy modes are separate from the authenticated HTTP stack.

## Contract and durable workflow

Agent exposes `POST /conversations`, `POST /conversations/{id}/messages`,
`POST /conversations/{id}/confirm`, and `GET /operations/{id}`. Experience exposes
the same paths prefixed by `/api`. Wire models live in
`src/card_chat/contracts.py`; Experience validates replies and rewrites polling
URLs. React depends only on that contract. It never calls Agent, MCP, Cards or
Anthropic directly. nginx routes `/api` and `/auth` to Experience on the same
origin; debug Vite uses equivalent proxies.

`GET /conversations/{id}/inputs` returns catalogue-driven form metadata and
masked, scoped selections. `POST /conversations/{id}/inputs` accepts structured
fields and selection references and returns a review without a model call.
Both are proxied through Experience under `/api`.

Messages, structured inputs and confirmations accept an `Idempotency-Key` submission header.
The default request-response mode waits for the operation result. With
`Prefer: respond-async`, the service durably inserts the job and returns
**202**, an operation ID, a `Location` header and `status_url`. Authenticated
polling returns `pending`, `running`, `succeeded` or `failed`, with the same
`Reply` result as synchronous mode. React's checkbox exercises polling through
Experience. Reusing a submission key and identical body returns the original
operation; changing a valid body or operation kind returns 409. Rejected private
pastes share a canonical safe notice rather than persisting raw text or a hash
of a low-entropy secret. Failed jobs remain
failed under that key; use a new key to try the workflow again.

Agent's SQLite journal stores caller ownership, history, draft, review nonce,
exact proposal, scoped private bindings, usage counters, submission snapshot/key/result,
and accepted jobs. Only sanitised chat and deterministic workflow markers enter
new history; model context excludes raw tool results and legacy unmarked turns. It commits
write intent **before** MCP execution and commits the resulting conversation
and job result atomically. On restart, running jobs become pending and are
resumed. Confirmation never invokes the model. A new message invalidates an old
review. A completed duplicate returns the saved result. An uncertain supported
write reuses its saved Cards idempotency key. An attempted non-idempotent write
is never automatically retried. Pending confirmations remain reviewable after a
restart, and already accepted confirmed writes recover through the job journal.

This is intentionally **single Agent process, single worker, one replica**.
An in-process worker lock serializes jobs and state changes. Do not mount its
SQLite file into multiple Agent instances. Jobs and conversation data have no
automatic retention cleanup; use synthetic data and protect/back up volumes.
The journal contains raw customer details and short-lived signed assertions;
it is not encrypted at rest. A job whose one-hour delegation expired fails
closed; sign in again and confirm the same stored review with a new submission
key. Any previously persisted downstream write key is preserved. There is no
exactly-once claim for endpoints that lack idempotency support.

## Identity and authorization

Browser login is OIDC authorization code + PKCE, state, nonce, fixed callback,
and an opaque random server-side session. Sessions and transient sign-in state
are persisted in Experience SQLite; only hashes of cookie identifiers are
stored. The browser receives no service access tokens. Session cookies are
HttpOnly and SameSite=Lax. All browser writes require the session's CSRF token
and an exact `Origin` match. Local logout invalidates the Experience session;
it does not terminate the Keycloak SSO session.

| Hop | OAuth client | Receiving audience | Required scope |
| --- | --- | --- | --- |
| Experience → Agent | experience-service | agent | agent:invoke |
| Agent → MCP | agent-service | mcp | tools:execute |
| MCP → Cards | mcp-service | cards | cards:write |

Each receiver verifies the Keycloak RS256 signature, fixed issuer, audience,
expiry, required claims, scope and calling client (`azp`). Each caller obtains
a fresh client-credentials access token for its own downstream hop; access
tokens are never forwarded to a different resource. MCP uses Bearer HTTP
authorization, exposes protected-resource metadata, and checks permission for
discovery and every invocation. Execution additionally requires customer
delegation. The configured MCP destination/method/authentication/execution
headers belong to the handler, never the model-facing tool schema.

Experience signs a separate one-hour Ed25519 customer assertion after verified
OIDC login. It binds the subject, username, allowed synthetic accounts, issuer,
expiry and intended internal audiences. Agent, MCP and Cards have only the
public verification key. A browser-supplied ID or bare forwarded identity header
has no authority. MCP checks the assertion at execution, and Cards verifies it
again before enforcing account membership and username equality. The private
key exists only in Experience. This is a small explicitly trusted delegation
scheme, not general OAuth token exchange. Trust in Experience includes its
authority to attest customer accounts.

The simulation assumes the account exists. It does not implement a bank account
registry, real card ownership lookup, account entitlements/revocation propagation,
KYC, settlement, financial transactions, or production audit controls. All tools
currently share `tools:execute`; add finer scopes when distinct entitlements are
introduced. Existing supported catalogue operations still need appropriate
downstream resource authorization at Cards.

Local HTTP and Keycloak `start-dev` are for loopback development only. Browsers
can send Secure Keycloak cookies on trustworthy `localhost`; the HTTP smoke
harness explicitly emulates this CookieJar behavior. Deployments require HTTPS,
`COOKIE_SECURE=true`, HTTPS issuer/public origin/callbacks, managed secrets and
keys, session/delegation revocation and retention policies, and an operational
identity deployment. Do not publish internal ports. Debug ports are loopback
only. Isolated test factories allow auth-free in-memory fixtures; Compose always
supplies identity configuration. The legacy API examples are not secured by the
new stack unless explicitly configured.

Correlation IDs originate at Experience and pass through Agent, MCP and Cards.
Only 32-character hexadecimal correlation headers are accepted. Access logs
are disabled in the Python entry point and nginx so OIDC callback query strings
are not recorded; the Vite error logger redacts OIDC query credentials. Tool
diagnostics contain the configured tool name, trace ID, duration and outcome,
not credentials or arguments. Cards can use its
existing redacted telemetry sinks; Compose now starts MongoDB and selects `mongo`
by default across the Python services. See [MongoDB logging](mongodb-logging.md).

References used for implementation: [MCP HTTP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
[Python SDK authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/),
[Keycloak OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers).
Installed SDK 1.30 APIs were inspected directly:
`streamable_http_client(url, http_client=...)` and
`StreamableHTTPSessionManager(server, stateless=True, json_response=True)`.

## Debug one component or all components

The normal Python build target excludes debugpy. The override uses the debug
target and enables listeners only for selected components. It runs one worker,
without reload or startup wait. Frontend debug mode runs Vite with TypeScript
source maps. Source is an image snapshot: rebuild after editing; no Windows
venv or secret is mounted as source.

```powershell
.\scripts\stack.ps1 -DebugComponent Frontend -Build
# Select Debug Frontend and press F5.
.\scripts\stack.ps1 -DebugComponent Experience -Build
# Select Attach to Experience API and press F5.
.\scripts\stack.ps1 -DebugComponent Agent -Build
# Select Attach to Agent API and press F5.
.\scripts\stack.ps1 -DebugComponent MCP -Build
# Select Attach to MCP Server and press F5.
.\scripts\stack.ps1 -DebugComponent Cards -Build
# Select Attach to Cards API and press F5.
```

Every command brings up/reuses the entire stack but enables only that Python
listener (or no Python listeners for Frontend). The remaining Python services
run normally. Debug override maps reserved loopback ports for all four services;
disabled listeners do not accept connections.

| Configuration | Host loopback port | Source mapping |
| --- | --- | --- |
| Attach to Experience API | 5681 | local src → /app/src |
| Attach to Agent API | 5682 | local src → /app/src |
| Attach to MCP Server | 5683 | local src → /app/src |
| Attach to Cards API | 5684 | local src → /app/src |
| Debug Frontend | browser HTTP 8080 | webRoot frontend |

For all components, select **Debug Full Stack** and press F5. Its compound has
**one shared pre-launch task** that calls `stack.ps1 -DebugComponent All -Build`,
waits for HTTP health and the four TCP listeners, then launches the browser and
attaches all Python debuggers. Individual attach configurations have no startup
task, so Compose is not started five times. To prepare the same stack manually:

```powershell
.\scripts\stack.ps1 -DebugComponent All -Build
```

Stop/disconnect in VS Code leaves the containers running. The compound's
`stopAll` disconnects its sibling debug sessions; it does not stop Compose.
Some debugpy sessions close their listener on detach. Rerunning the startup
task detects this and restarts the affected service to reopen the listener;
durable Agent operations recover normally. A still-listening debug service is
reused. Listener readiness reads the container's TCP LISTEN table without
opening an incomplete DAP connection through Docker Desktop.
Run **Tasks: Run Task → Stop Debug Stack** or:

```powershell
.\scripts\stack.ps1 -Stop
# Return to normal images/listeners:
.\scripts\stack.ps1 -Build
```

For startup breakpoints (catalogue loading or Agent discovery), first start the
normal healthy stack, then recreate **only** the chosen Python service waiting
for attach. This command waits only for that debug listener, never HTTP:

```powershell
.\scripts\stack.ps1 -DebugComponent MCP -Build -WaitForDebugger
# Set breakpoint at create_server/load_catalogue; Attach to MCP Server; F5.
# After continuing, do the same for Agent discovery:
.\scripts\stack.ps1 -DebugComponent Agent -Build -WaitForDebugger
# Attach to Agent API; breakpoint inside gateway.connect; F5.
```

The wait mode requires existing healthy dependencies and uses `--no-deps`.
Do not use it as the compound pre-launch task: waiting for HTTP before attach
would deadlock startup. Afterward rerun the regular debug setup to remove wait.
Long paused requests can hit the Cards client's 10-second timeout or the outer
120-second HTTP timeout; continue and use
**Retry same submission**, preserving its submission key. A confirmation retry
uses the previously persisted downstream execution key.

## One confirmed-request breakpoint walkthrough

1. Start **Debug Full Stack**, sign in and start a new conversation. Set React
   breakpoints in `frontend/src/main.tsx` at `supply`, `submit` and `confirm`.
2. Break in `experience.py:message`/`forward`: inspect the typed request and URL;
   avoid expanding credentials when sharing screenshots.
3. Complete the secure form and break in `main.py:structured`: it resolves scoped
   references and returns a masked review with zero model calls. Optionally send
   a safe intent message, such as "Create the card using these details", to hit
   `agent.py:interpret_turn` and `main.py:message`; inspect sanitised context and
   opaque facts, not raw customer values. Cards must not be called yet.
4. Click the confirmation button. Break in `main.py:confirm`, then `execute`:
   inspect the stored proposal, review and saved submission key. No model call
   occurs here.
5. Break in `mcp_server.py:call_tool`, then `execute`: the handler constructs the
   registered HTTP destination, delegation and idempotency headers.
6. Break in `card_servicing/cards.py:create_card` before
   `store.create_idempotent`. Inspect the authorized synthetic account. Continue
   and verify the UI renders the configured result labels. Confirm again and
   verify no second Cards write occurs. Detach and check the app still works.

## Catalogue extension

The existing version-1 catalogue compiler supports flat scalar JSON bodies,
required string path parameters, optional body fields and configured scalar
result labels; unsupported shapes fail closed at load. It validates local
OpenAPI 3.1 references and idempotency/confirmation policy. Secrets and execution
keys are absent from tool input schemas. Changes to the registered destination
or schema require restarting MCP and Agent so discovery stays consistent.

`tests/fixtures/tool-catalogue.json` adds `block_card` to the existing create
operation against `tests/catalogue_api.py` and its OpenAPI contract. The existing
integration tests expose and execute it over real remote MCP with no Agent or UI
branch. They also prove a non-idempotent uncertain block cannot be retried. This
is a test fixture; the simulated production Cards API still exposes creation
only. Once an authorized Cards endpoint and compatible OpenAPI exist, adding its
catalogue entry is sufficient for discovery, review, dispatch and rendering.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/export_cards_openapi.py --check
.\.venv\Scripts\python.exe scripts/render_chat_diagrams.py --check
.\scripts\stack.ps1 -Build
.\.venv\Scripts\python.exe scripts/smoke_stack.py
.\.venv\Scripts\python.exe scripts/verify_identity_import.py
Get-Content -Raw scripts/verify_internal.py | docker compose --env-file .env --env-file .env.stack exec -T experience python -
docker compose --env-file .env --env-file .env.stack config --quiet
docker compose --env-file .env --env-file .env.stack -f compose.yaml -f compose.debug.yaml config --quiet
# Type checking and production compilation were also run in the Docker build.
docker build --target build --tag card-servicing-ui-check ./frontend
docker run --rm card-servicing-ui-check npm run typecheck
# Direct DAP check, with no VS Code debugger currently attached:
.\scripts\stack.ps1 -DebugComponent All -Build
.\.venv\Scripts\python.exe scripts/verify_debug.py
```

See [verification results](http-stack-verification.md) for what was actually
executed, limitations, and remaining interactive checks. No live Claude smoke
test is implied by stub results.
