# Local card chat demo

For the current registration, login, account and card journey, use [Customer banking](customer-banking.md). Structured credentials never enter chat or the LLM. Both action buttons and natural language use the same banking workflows. The earlier examples below describe the original card-only demo.

The current runnable demo uses [React and the independent HTTP services](http-stack.md).
Private details now use the secure form and masked picker, with
[minimal model context](model-privacy.md); the earlier free-text collection examples
below describe the superseded flow.
Agent connects to MCP over Streamable HTTP; it no longer launches an MCP child.
The detailed earlier workflow explanation below is retained, but use the linked
guide for current authentication, startup and debugging commands.

For the detailed code trace and downloadable vector diagrams, see
[the end-to-end walkthrough](chat-end-to-end.md) and
[offline diagram viewer](diagrams/index.html).

The browser talks to FastAPI chat on 8020. Chat launches an official MCP SDK
stdio child, discovers every configured tool, and sends Claude their names,
descriptions and OpenAPI-derived input schemas with automatic tool choice.
`CardServicingAgent` returns either a customer reply/question or a proposed call.
The workflow validates complete proposals and renders a generic masked review.
An explicit button click executes that exact snapshot through MCP and the shared
HTTP client, with no additional Claude call. The Cards API stays separate on
8010 (host) or 8011 (Docker).

See [tool catalogue configuration](tool-catalogue.md) for the supported operation
shapes, how to add an entry, policies, tests and detailed VS Code breakpoints.
Only create-card is exposed in the default production catalogue. The second
blocking operation exists only in the integration-test fixture.

The single concrete `CardServicingAgent` owns no conversation or execution state.
The workflow passes history, previously collected details and all eligible tool
definitions to `interpret_turn`. Anthropic remains the implemented model adapter.
Session state, schema validation, review tokens, confirmation, idempotency and
MCP execution stay in `main.py`. There is no fixed intent or question mapping.

## Install and run in PowerShell

For a fresh clone, follow [the README quick start](../README.md)
first: create the virtual environment, install the chat extra, and configure
ignored `.env`. The primary debugging path is **Debug Chat + API**, which starts
both host applications together. The terminal commands below are an alternative;
do not run them alongside that compound on the same ports. Use
`TELEMETRY_SINK=stdout` to run locally without Docker or MongoDB.

Use the existing virtual environment, from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,infra,chat]"
python -m pytest -q
```

Terminal 1, start the host API (uses the selected `.env` telemetry sink and SQLite):

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn card_servicing.main:app --host 127.0.0.1 --port 8010
```

Terminal 2, set server-side configuration and start chat:

```powershell
.\.venv\Scripts\Activate.ps1
$env:MODEL_PROVIDER = 'anthropic'
$env:MODEL_ID = 'YOUR_CLAUDE_MODEL_ID'
# Enter your key without placing it in command history or terminal output.
$chatSecret = Read-Host 'Anthropic API key' -AsSecureString
$env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new('', $chatSecret).Password
$env:CARD_API_BASE_URL = 'http://127.0.0.1:8010'
python -m uvicorn card_chat.main:app --host 127.0.0.1 --port 8020
```

Replace the model placeholder with a Messages model ID available to your Anthropic
account. No model ID is silently selected. Alternatively put configuration in
the ignored root `.env`, using `.env.example` as the placeholder reference.
Environment values override `.env`. Never commit a real key. Do not enable SDK
HTTP debug logging or log request bodies. The browser never receives the key.

Terminal 3:

```powershell
Start-Process 'http://127.0.0.1:8020'
```

If using the existing Docker API instead, start it with the README instructions,
set `CARD_API_BASE_URL=http://127.0.0.1:8011`, and restart chat. No separate manual
MCP startup is needed. Use a single chat worker without reload for this demo.

## Live Anthropic smoke steps

Automated tests use a stub model, not cloud credentials. Separate live Claude
checks have exercised multi-turn questions, review, creation and replay; see the
[catalogue verification record](tool-catalogue.md#verification). To repeat the
browser flow after startup, use the steps below. Wording and question order are
model-generated and can vary.

1. Confirm the exact greeting: “Hi! What would you like help with?”
2. Enter “Freeze my card”. Expect an explanation that creation is supported.
3. Enter “card servicing”. Expect a question about creating a card for an existing account.
4. Enter “Yes, create a new card”. Expect a question for missing customer details.
5. Enter “My given name is Alex and my user name is alex.synthetic”. Expect the account question.
6. Enter “0012345678”. Expect a masked account review with a Create card button.
7. Enter “Change my given name to Sam”. Expect a fresh review. No REST write has occurred.
8. Click **Create card**. Expect the API's `SIM-...` identifier and UUID only after creation succeeds.
9. Click **Create card** again. Expect the same card; the completed result is returned from memory.
10. Use New conversation for another request. To check API unavailability, stop
    the API before confirming, then restart it and click Create card again.
    The frozen body and submission key are reused.

## Credential-free checks

```powershell
python -m pytest tests/test_chat.py -q
python scripts/chat_stub_smoke.py
# In another terminal:
Start-Process 'http://127.0.0.1:8020'
```

The tests first launch an isolated real REST process, discover the stdio MCP
tool, create a synthetic card and replay its key. They then verify the full
chat/MCP/HTTP path, including a lost response after commit: retry returns the
identical card. Stub-model tests cover unsupported and generic requests,
multi-turn fields, multiple fields per turn, leading zeros, invalid values,
no tool before confirmation, stale reviews, mismatched model arguments and
duplicate clicks. Tests use no hosted model or credentials. CI installs the
chat extra so these tests run there too.

The browser harness also uses real MCP/HTTP with a scripted model and an isolated
in-memory API subprocess; it does not touch your card database. In order, enter
“freeze card”, “new card”, “Alex / alex.synthetic”, “0012345678”, then click Create
card twice. Its model replies are scripted in that order, so restart the harness
to repeat it. Stop with Ctrl+C to close chat, MCP and the isolated API.
Automated HTTP and MCP checks passed during implementation. Visual browser
verification could not complete: browser automation host unavailable, followed
by native app access approval timing out. Run this harness to finish that check.

## VS Code debugging

1. Select `.venv/Scripts/python.exe` with **Python: Select Interpreter**. Put live
   model settings in the root ignored `.env`; do not put keys in `launch.json`.
2. Stop old host/container APIs sharing the database and any manual chat server.
   Choose **Debug Chat + API** in Run and Debug and press **F5**. This starts host
   **Debug API** (8010) and **Debug Chat** (8020) together. Wait for both startup
   messages, then open `http://127.0.0.1:8020` and start a fresh conversation.
3. The labels and compound are defined in [launch.json](../.vscode/launch.json).
   Both sessions appear under CALL STACK. The compound's `stopAll:true` stops both
   sessions when either is stopped. Terminal-only runs need Ctrl+C in each terminal.
   For Docker instead, set `CARD_API_BASE_URL=http://127.0.0.1:8011`, start the
   debug container, then start **Attach to Docker API** and **Debug Chat** separately.
   Do not select the host compound for that mode. Variables set only in an
   integrated terminal are not guaranteed to configure a launch started by VS Code;
   use `.env` or explicit launch configuration for debugger sessions.
4. Set breakpoints in `main.py` at `message`, `confirm`, and
   `gateway.execute`. `Debug Chat` uses `subProcess: false` to keep automatic
   debugger attachment out of the MCP stdio child startup. The compound
   `Debug Chat + API` still debugs both applications as independent sessions.
   To see Claude's proposal, break in `provider.py` on
   `return ProposedToolCall(calls[0].name, calls[0].input)` inside `AnthropicModel.turn`.
   To see the workflow execute it, break in `main.py` on
   `result = await app.state.gateway.execute(submission['tool'], submission['arguments'], submission['key'])`.
   Between these points, the workflow validates the proposal, shows a review, and
   waits for confirmation. Confirmation executes it without a second model call. Inspect `c.proposal` and
   `submission` locally; do not log their values.
5. Set a breakpoint in `card_servicing/cards.py` at the POST route. The API
   breakpoint belongs to its separate host/container debugger session. With
   automatic child attachment disabled, breakpoints in `mcp_server.execute`
   and `api_client/client.py` inside that child do not fire; inspect the outgoing
   MCP arguments and returned result in `gateway.execute`. Debugging the child
   itself requires a separate attachment after it has started.
6. Open 8020, supply synthetic details, inspect the masked review, then click
   Create card. Trace the frozen body/key into MCP and the `Idempotency-Key`
   header into REST. Avoid copying watch values or sensitive payloads into logs.

MCP reserves stdout for protocol frames. Diagnostics go to stderr with tool name,
duration and outcome; chat text, account values and keys are not logged. Tool errors
are sanitized. A debugger breakpoint longer than the REST client's ten-second
timeout can produce a retry message; resume, then retry the same submission.

## State, policies and request tracing

Conversations, collected proposal details, reviews and submissions are in memory.
Every new message invalidates the previous review. A valid confirmation binds the
exact tool and arguments; duplicate successful clicks return a cached result.
`create_card` keeps the same body/key for retries after a lost response. Writes
without a safe replay contract cannot be retried after an uncertain attempt.
GET entries can opt out of confirmation; every write requires it. API validation
and authorization remain at the API boundary. Account existence remains assumed.

Restarting chat loses its keys and context. Do not create a new submission to
retry an uncertain write. Bind locally and use synthetic details. The hosted
model receives supplied details and history, but no execution key or credential.
Tool-result success comes from the actual API, not model-generated claims.

For the complete trace, see [the walkthrough](chat-end-to-end.md). Start with
`index.html` -> `main.message` -> `CardServicingAgent.interpret_turn` ->
`AnthropicModel.turn` -> validated review -> `main.confirm`/`execute` ->
`Gateway.execute` -> `mcp_server.execute` -> `ApiClient.execute_operation` ->
`cards.create_card` -> `SQLiteCardStore.create_idempotent`.
