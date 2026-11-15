# Banking chat: end-to-end request trace

For installation and run/debug steps use [Setup guide: Docker](getting-started.md#1-run-and-debug-using-local-docker-containers) or [Setup guide: native](getting-started.md#2-run-and-debug-natively-on-your-computer). Both expose the React UI at `http://localhost:8080`. The native BFF port 8020 and container API port 8000 are not the UI.

## 1. Independent processes and startup

React talks to Experience. Experience calls Agent. Agent discovers/invokes a separate MCP Streamable HTTP server. MCP uses the shared HTTPX client to call Cards. Agent alone calls the hosted language provider. Identity secures service hops; Mongo receives structured diagnostics. There is no stdio child lifecycle.

Agent startup creates its provider/agent and enters `gateway.connect`. Gateway and MCP compile the same catalogue/OpenAPI. Gateway opens an HTTP MCP session, initializes `ClientSession`, obtains `list_tools`, and verifies names, descriptions and schemas against its compiled catalogue. A fresh destination token and customer assertion are used for execution calls. Adding a supported operation is catalogue-driven rather than a new Python MCP handler per endpoint.

## 2. Registration and browser session

Pre-login chat can propose only registration/login form openers. Direct buttons open those forms without a model call. Credentials are posted to the BFF and then the Cards credential service; they do not become chat turns or tool arguments. Successful registration/login establishes a rotated BFF session and CSRF token. Origin/CSRF checks protect browser writes; logout deletes the session. The optional original OIDC demo sign-in remains available.

Before forwarding a registered customer's agent request, Experience refreshes their owned accounts from the Cards database and issues signed customer delegation. The backend still verifies ownership at execution time. See [customer banking](customer-banking.md).

## 3. Conversation and intent

Experience proxies `POST /api/conversations` to Agent. Agent creates a caller-owned journal record and returns a greeting without a model call. The browser retains the conversation ID and current reply/review.

A chat message is accepted through the existing synchronous or asynchronous operation contract. Private pastes are filtered before durable acceptance/model projection. Agent loads only the caller's conversation, invalidates stale review state and builds recent sanitized model context. Eligible catalogue tools are permission filtered. Exact account selections/private values are represented by conversation/owner/tool/field-bound references.

`CardServicingAgent.interpret_turn` asks the provider for either `CustomerReply` or `ProposedToolCall`. It rejects unavailable tools or invalid turn shapes. Missing structured details return `Reply.input_tool`, opening the matching private form. Requesting a card without an eligible account opens account creation first. The model cannot authenticate the customer, decide ownership or execute arbitrary database operations.

## 4. Direct selection and private forms

Prominent direct action buttons start a fresh workflow and use the same catalogue-driven input/confirmation endpoints. Reads can execute immediately. Structured fields are validated against the original API schema; protected account/username values come from trusted bindings or verified identity. Forms, review and confirmation make no model calls.

The additional-card choice is a private strict boolean supplied through the form. The model cannot enable it by inventing a raw boolean/reference. Invalid corrections invalidate the old review; earlier/cross-conversation selections cannot authorize writes. See [model privacy](model-privacy.md).

## 5. Review, confirmation and execution

A complete write proposal is stored with an unpredictable review token. Confirmation validates that token and executes the reviewed snapshot directly. The Agent persists exact execution arguments and the retry key before the first attempt. MCP validates arguments, forwards the idempotency header and calls the trusted compiled HTTP method/path. Neither confirmation nor retry reinterprets the action through the model.

Cards verifies service/customer authorization and database ownership. SQLite reserves the writer before replay/existence checks. Ordinary issuance on an account with a card returns HTTP 200 and `already_exists`; it does not create another card. Explicit additional issuance returns 201 and `additional_created`. A matching key replay returns its original card/status/outcome; changed details return 409. MCP forwards the trusted issuance outcome to the Agent, which reports that a card already exists rather than falsely claiming another creation. See [card issuance](card-issuance.md).

Optional issuance during account creation uses the same card policy and a deterministic key. The account can be committed before optional issuance completes; retrying the original account request completes interrupted issuance without another account/card.

## 6. Responses, durable state and telemetry

The BFF validates typed `Reply`, `InputOptions` and `Operation` responses. Account/card read lists appear in `Reply.data` for React and do not enter model history. Only actual validated tool results are rendered as operation outcomes. A fixed submission supports safe same-key retries; use a new workflow for another action.

| State | Location | Restart behavior |
| --- | --- | --- |
| Visible messages, conversation ID, current controls | React state | Browser refresh resets the visible session UI |
| Sessions, claims, CSRF data | Experience SQLite | Sessions persist until expiry/logout |
| Private bindings, drafts, review, submission, accepted jobs | Agent SQLite journal | Caller-bound state and supported retries survive restart |
| Customers/accounts/cards and retry snapshots | Cards SQLite | Persist through API restarts/migrations |
| Structured operational events/traces | Mongo or selected console/none sink | Best effort; not a business audit ledger or model context |

Mongo exports HTTP route templates, status/timing/correlation, structured workflow/token usage and MCP outcomes. Arbitrary log text, request bodies, credentials and raw tool arguments are excluded. Local/Docker stores differ as described in README.

## Debugging and diagrams

Use **Debug Full Stack (Docker)** to attach to container processes; use **Debug Full Stack (Local)** to launch native ones. Set breakpoints in React `submit`/`supply`/`confirm`, Experience `forward`, Agent `interpret_turn`/`structured`/`execute`, MCP `call_tool`/`execute`, and Cards/SQLite issuance. Startup alone does not hit request handlers. Long pauses may trigger timeouts; continue and retry the same reviewed submission.

The [diagram viewer](diagrams/index.html) includes startup/discovery, context, confirmation, retries, adapters, HTTP identity/jobs, private model context and customer banking. Definitions are in `docs/diagrams/card-chat-flows.json`; regenerate with `python scripts/render_chat_diagrams.py`. The issuance transaction also has a [Mermaid diagram](card-issuance.md#transaction-and-migration).
