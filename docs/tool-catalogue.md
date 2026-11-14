# Configured MCP tools and model-selected actions

The tool catalogue is [src/card_chat/tools.json](../src/card_chat/tools.json).
It is versioned JSON, compiled against a local OpenAPI 3.1.0 document at chat/MCP
startup. Adding a supported REST operation needs a catalogue entry and restart;
there is no MCP Python function, intent table, agent branch or browser branch per
operation. The only production entry today is `create_card`. Blocking exists only
in the integration-test fixture.

## Contract and registration

Each entry references a stable `operation_id`. The card router explicitly declares
`operation_id='create_card'`. Run the existing export whenever the API changes:

```powershell
python scripts/export_cards_openapi.py
python scripts/export_cards_openapi.py --check
```

CI checks the export after tests, before checking diagrams. It checks that the
committed contract matches the router; it does not modify or commit files.
The stable ID changes the OpenAPI identifier from its former generated name;
the REST URL, request/response payloads, status codes and headers are unchanged.

`catalogue.load_catalogue()` resolves local OpenAPI references and derives the
HTTP method, path, path parameters, body fields, validation constraints and success
response schema. It flattens path and body properties into one model-facing
argument object. Path/body names must not collide. Headers are not model inputs.

`mcp_server.create_server()` constructs the official SDK `Server`, registers
`list_tools` and one shared `call_tool` handler, and lists only compiled catalogue
entries. The child uses `stdio_server()`; it has no HTTP port. The gateway starts
the child, initializes `ClientSession`, calls `list_tools()`, and verifies the
discovered names/descriptions/schemas match the compiled catalogue. It no longer
requires exactly one tool.

## Add an entry

Suppose an existing API has `POST /cards/{card_id}/block`, with stable operation ID
`block_card`, one required string path parameter, and a JSON body containing the
required string `reason`. An example entry is:

```json
{
  "name": "block_card",
  "operation_id": "block_card",
  "description": "Block an existing card when the customer explicitly requests blocking. Collect its card identifier and the customer-supplied reason. Do not use for creation or replacement.",
  "confirmation": "required",
  "label": "Block card",
  "confirmation_label": "Block this card",
  "field_labels": {"card_id": "Card reference", "reason": "Reason"},
  "result_labels": {"card_id": "Card reference", "status": "Status"}
}
```

That operation is **not a production feature in this repository**. The example
is implemented only in `tests/catalogue_api.py`; its matching export and two-tool
catalogue are under `tests/fixtures/`. Integration tests launch it over real HTTP,
start the real MCP child using that test catalogue and prove dispatch after review.

To add a real, already implemented supported endpoint:

1. Give the endpoint a stable operation ID and publish its current OpenAPI export.
2. Add an entry under `tools` and point the catalogue's `openapi` to that document.
3. Choose a precise description distinguishing the tool from other capabilities.
4. Set the confirmation/replay policy to match the API's actual semantics.
5. Optionally supply display labels, result properties and sensitive input fields.
6. Validate and restart chat:

   ```powershell
   python -m card_chat.catalogue
   # Stop/restart Debug Chat, or its Uvicorn process.
   ```

The catalogue defaults to `src/card_chat/tools.json`. Override
`TOOL_CATALOGUE_PATH` in `.env` or the process environment to select another local
file. Relative OpenAPI paths resolve relative to the catalogue file. Relative
catalogue paths resolve from the process working directory; VS Code uses the repo
root. The deployed chat package needs the catalogue and referenced contract files
available on disk; the built-in reference assumes the repository checkout layout.

## Catalogue fields

| Field | Meaning |
| --- | --- |
| Top-level `version` | Exactly `1` |
| Top-level `openapi` | Local OpenAPI document path; servers from that document are not used as destinations |
| `name` | Unique MCP tool name; letters/digits/underscore/hyphen, begins with a letter, max 64 |
| `operation_id` | Exactly one operation in the referenced OpenAPI document |
| `description` | What the action does and when Claude should choose it |
| `confirmation` | `required`, or `none` for GET operations only |
| `label` | Optional action label; defaults to the operation summary/name |
| `confirmation_label` | Optional button text; defaults to the action label |
| `field_labels` | Optional mapping of argument names to review labels |
| `sensitive_fields` | Input names to mask in reviews; `x-sensitive` schema properties and account-named fields are also masked |
| `result_labels` | Mapping of scalar response properties to displayed labels; omitted fields are not displayed |
| `idempotency_header` | Optional `Idempotency-Key`; must correspond to an OpenAPI header and a documented safe-replay contract |

The header name/configuration and key are not included in Claude's tool schema.
The gateway supplies the key in MCP request `_meta`, and the MCP handler injects
the HTTP header. For `create_card`, the existing API provides durable replay.
Do not mark another write replayable simply because it accepts an arbitrary
header: its API must actually implement that guarantee.

## Supported shared HTTP patterns

The deliberately small shared adapter supports:

* OpenAPI 3.1.0 with nonrecursive local `$ref` references.
* GET, POST, PUT, PATCH and DELETE paths, with successful JSON **object** responses.
* Required **string** path parameters, URL-encoded as individual values. Numeric
  identifiers should be modelled as strings when leading zeros matter.
* Optional absence of a body, or a required `application/json` body that is a flat
  object with scalar string/integer/number/boolean properties. Individual body
  properties can be optional; missing optional values are omitted, not invented.
* Scalar descriptions, titles, examples, enums/constants, supported formats,
  string length/pattern constraints and numeric range/multiple constraints.
* One execution-owned `Idempotency-Key` header, declared by the contract/catalogue.

It rejects query/cookie/other header parameters, optional path parameters,
body/path collisions, nested/array/union input shapes, optional request bodies,
non-JSON media, recursive/external references, configured security schemes and
non-object/no-content success responses. These need an explicit shared-adapter
extension before they can be exposed. It also rejects duplicate names, missing
or ambiguous operation IDs, invalid labels, malformed schemas and unconfirmed
writes. Errors identify the entry/unsupported shape without echoing customer
arguments or credentials. Configuration failures occur before tools are offered.

All requests use `CARD_API_BASE_URL`; the model never supplies a host, method,
path template or execution header. Prefixes are retained, for example
`https://gateway.example/demo/card-service` becomes `/demo/card-service/cards`.
Redirects are not followed. API validation and authorization still belong at the
API boundary. No gateway-specific authentication has been added.

## Conversation and model boundary

`CardServicingAgent.interpret_turn(history, collected, allowed_tools)` calls the
provider-neutral `Model.turn` once for each customer message. The Anthropic
adapter sends the conversation, latest message (last history entry), previously
validated collected details, and all discovered tool definitions. It uses
`tool_choice={'type':'auto', 'disable_parallel_tool_use':True}`.

There is no fixed intent enum, intent-to-tool table, `QUESTIONS` mapping, or forced
`create_card` call. Claude uses tool descriptions and required/schema metadata to
ask one clarifying question, give a normal unsupported-action reply, or propose
one action. Multiple tool blocks become a request to choose one action. The prompt
requires explicit supplied values, preserves string identifiers, and forbids
claims that a proposal has already executed.

The workflow independently rejects unknown tools and invalid arguments. An
incomplete proposed call is not executable: valid partial arguments are saved as
collected context and a missing-field question is generated from schema/catalogue
labels. A normal clarifying reply keeps context in the accumulated history; it
does not run a separate extraction API. Every new message invalidates an earlier
review, even if the model cannot interpret the correction.

Only a complete validated proposal gets a masked review and review token. The
token binds to a server-owned tool/argument snapshot. Confirmation sends just
that token and executes the stored snapshot directly; **no second Claude call**.
GET entries explicitly configured with `confirmation:none` execute after proposal
validation without a button. All writes require confirmation.

## Retry policy

The conversation lock serializes duplicate clicks. A confirmed replayable write
gets one generated key and frozen argument snapshot. A failed/uncertain attempt
can only retry those same values/key. A completed duplicate returns the cached
result and makes no additional model, MCP or HTTP call. `create_card` still uses
the API's unchanged SQLite transaction/fingerprint/saved-response behavior.

Other writes without a safe-replay contract get at most one attempt per confirmed
submission. On error or lost result the button is disabled, and further confirm
requests do not execute again. The customer must check status rather than create
another request blindly. No operation has an automatic HTTP retry loop.

Conversation history, proposals, review tokens and submission keys remain in
memory. Do not restart chat to retry an uncertain creation. API durable replay
survives API restart, but chat recovery after losing its key is not implemented.

## Local VS Code steps

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,infra,chat]"
python scripts/export_cards_openapi.py --check
python -m card_chat.catalogue
```

Keep the key/model in ignored `.env`; select Anthropic as before. For host
debugging set `CARD_API_BASE_URL=http://127.0.0.1:8010`. Start Mongo, or set
`TELEMETRY_SINK=stdout`. Stop old processes on 8010/8020, select **Debug Chat + API**,
and wait for both applications to finish startup. Open `http://127.0.0.1:8020`.

Enter "I want a new card", then supply synthetic answers separately or together.
Expect model-generated questions, then a masked review and Create card button.
Change a value and observe a fresh review. Click Create card for the actual
reference; click again for the same cached reference. Unsupported blocking should
receive an explanation: the production catalogue has no blocking tool.

Useful breakpoints (use executable statements inside these functions):

| Where | Inspect |
| --- | --- |
| `catalogue.load_catalogue` / `compile_entry` | Entry, OpenAPI operation, flattened schema |
| `gateway.connect` after `session.list_tools()` | Every discovered definition |
| `main.message` at `c = get(cid)` | Latest text, conversation history, collected details |
| `agent.interpret_turn` | All allowed tool definitions; no fixed action routing |
| `provider.turn` after Messages returns | Text reply or Claude `tool_use`; `stop_reason` and usage |
| `main.message` at `spec.validate` | Proposed name/arguments, constraints, missing values |
| `main.message` at review token creation | Exact validated snapshot and masked labels |
| `main.execute` at `gateway.execute` | Confirmed tool/arguments and execution-owned key; no model call |
| `gateway.execute` at `session.call_tool` | Public arguments and separate execution metadata |
| `cards.create_card` / `persistence.create_idempotent` | REST validation and atomic replay |

Chat has `subProcess:false` to avoid the previous MCP debugger-startup issue;
MCP internals need a separate attach. Chat/agent/adapter and API breakpoints work
in the existing compound debugger. Pausing HTTP longer than ten seconds can
trigger a timeout; resume and retry only when the contract supports it.

## Verification

Baseline: 78 tests and diagram check passed, with a clean checkout. The updated
suite includes real HTTP/MCP creation/replay and configuration-only second-tool
selection with path parameters. It checks missing/invalid values, unknown tools,
unsupported/plain replies, stale reviews, no pre-confirmation writes, read policy,
non-idempotent uncertain outcomes and no model call on confirmation/retry.

Run:

```powershell
python -m pytest
python scripts/export_cards_openapi.py --check
python scripts/render_chat_diagrams.py --check
# Against a separately running API:
python scripts/smoke_chat.py --api-url http://127.0.0.1:8010 --lose-first-response
python scripts/smoke_chat.py --api-url http://127.0.0.1:8010 --live --lose-first-response
```

Final automated result: **95 tests passed**; OpenAPI export and diagram checks
passed. Stub tests require no cloud credential. Separate live Claude create-card
flows passed with the configured Haiku model: all-fields-at-once with response
loss/replay, and three-turn missing-field questions followed by masked review,
creation and a duplicate result. Browser automation and a live model choosing the test-only
blocking operation were not run; the two-tool proof uses a stub model. The API
adapter-switching tests remain in the suite; Docker rebuild/runtime was not
repeated for this chat-only refactor.

When behavior changes, update [the walkthrough](chat-end-to-end.md), the editable
[sequence definitions](diagrams/card-chat-flows.json), regenerate SVGs and commit
them together. CI checks freshness and code anchors, not prose semantics.
