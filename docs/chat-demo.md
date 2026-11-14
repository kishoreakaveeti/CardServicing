# Local card chat demo

The browser talks to a separate FastAPI chat process on 8020. That process owns
an official MCP SDK v1 stdio child, discovers `create_card` at startup, and calls
the existing `ApiClient.create_card()` over HTTP to the separately running API.
Anthropic Messages extracts intent/fields without tools. Only a validated review
and explicit button click make the discovered tool available to Claude. A future
provider implements the `Model` interface; only Anthropic is implemented today.

`CardServicingAgent` in `src/card_chat/agent.py` is the single concrete agent.
The workflow gives `interpret_turn(history, draft, allowed_tools)` a snapshot of
the draft and an empty tool mapping during collection. The agent uses the model
adapter to interpret intent and extract supplied fields, then returns a
`CustomerReply` with those updates and the next question. The workflow validates
updates before saving them or showing a review. After an explicit confirmation,
the workflow passes only the discovered `create_card` schema. The agent returns
a `ProposedToolCall`; the workflow checks its name and values against the frozen
submission, then executes MCP using the confirmed server-side body and key.
Session state, validation, confirmation, idempotency and MCP execution all stay
in `main.py`. The agent holds no conversation state or MCP client.

## Install and run in PowerShell

Use the existing virtual environment, from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,infra,chat]"
python -m pytest -q
```

Terminal 1, start the existing host API (uses the existing `.env` infrastructure
configuration, SQLite and best-effort Mongo telemetry):

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

A live hosted-model smoke test was not run during implementation; no cloud
credential was supplied for this task. After the commands above:

1. Confirm the exact greeting: “Hi! What would you like help with?”
2. Enter “Freeze my card”. Expect an explanation that creation is supported.
3. Enter “card servicing”. Expect a question about creating a card for an existing account.
4. Enter “Yes, create a new card”. Expect one question for the given name.
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

1. Select `.venv/Scripts/python.exe` with **Python: Select Interpreter**.
2. Start **Debug API** (8010), or start Docker and **Attach to Docker API** (5679).
3. Set the chat environment using the PowerShell commands above in the integrated
   terminal. Choose **Python Debugger: Debug using launch.json** → **Debug Chat**,
   or use **Debug Chat** directly after putting local secrets in ignored `.env`.
   The launch configuration loads `.env`; don't put the key in `launch.json`.
4. Set breakpoints in `main.py` at `message`, `confirm`, and
   `gateway.create`. `Debug Chat` uses `subProcess: false` to keep automatic
   debugger attachment out of the MCP stdio child startup. The compound
   `Debug Chat + API` still debugs both applications as independent sessions.
   To see Claude's proposal, break in `provider.py` on
   `return calls[0].name, calls[0].input` inside `AnthropicModel.propose`.
   To see the workflow execute it, break in `main.py` on
   `result = await app.state.gateway.create(submission['body'], submission['key'])`.
   Between these points, `CardServicingAgent.interpret_turn` returns the proposal
   and `confirm` checks it against the confirmed draft. Inspect `proposal` and
   `submission` locally; do not log their values.
5. Set a breakpoint in `card_servicing/cards.py` at the POST route. The API
   breakpoint belongs to its separate host/container debugger session. With
   automatic child attachment disabled, breakpoints in `mcp_server.create_card`
   and `api_client/client.py` inside that child do not fire; inspect the outgoing
   MCP arguments and returned result in `gateway.create`. Debugging the child
   itself requires a separate attachment after it has started.
6. Open 8020, supply synthetic details, inspect the masked review, then click
   Create card. Trace the frozen body/key into MCP and the `Idempotency-Key`
   header into REST. Avoid copying watch values or sensitive payloads into logs.

MCP reserves stdout for protocol frames. Diagnostics go to stderr with tool name,
duration and outcome; chat text, account values and keys are not logged. Tool errors
are sanitized. A debugger breakpoint longer than the REST client's ten-second
timeout can produce a retry message; resume, then retry the same submission.

## State and limitations

The backend serializes each conversation, validates against `CardCreate`, creates
a new review token after edits, and rejects stale confirmations. The first valid
click freezes the body and generates one UUID key. The model sees only card fields;
the backend injects the key into the MCP execution arguments. It rejects proposed
tool names or values that differ from the confirmed draft. A completed duplicate
click returns the saved result; failed attempts retain body/key for retry. Once
submitted, use New conversation for different values, avoiding ambiguity after a
timeout. API account existence remains assumed.

In-memory state is lost on chat restart; do not restart chat to retry an uncertain
submission. Durable recovery, auth, session expiry and capacity limits are outside
this local demo. Bind to loopback and use synthetic data only. Conversation URLs
contain unguessable IDs but are not a substitute for production authentication.
Names and account strings are sent to the hosted provider for extraction. The
provider adapter's returned prose never determines creation success: the backend
renders the structured MCP/API result directly.

## Trace one request

* `src/card_chat/index.html`: message list and same-origin JSON requests; explicit confirmation button.
* `src/card_chat/main.py`: per-conversation draft, validation, masked review, lock and frozen retry submission.
* `src/card_chat/agent.py`: single `CardServicingAgent`, customer replies/field updates and allowed tool proposals.
* `src/card_chat/provider.py`: provider interface and Anthropic extraction/tool-use blocks.
* `src/card_chat/gateway.py`: child lifecycle, runtime tool listing, model-schema projection and MCP call.
* `src/card_chat/mcp_server.py`: single tool, sanitized result/log, existing HTTP client call.
* `src/api_client/client.py` → `src/card_servicing/cards.py`: HTTP POST and existing durable idempotent API behavior.
* `tests/test_chat.py`: stub model plus real-process transport/retry verification.

SDK references: [official MCP Python SDK v1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x),
[Anthropic client tool handling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls).
