# Private inputs and minimal model work

Structured model usage and tool metrics now go to MongoDB when enabled. Prompts, arbitrary log text, credentials and raw tool arguments remain excluded; see [MongoDB logging](mongodb-logging.md).

Customer registration/login credentials travel only through secure BFF forms to the Cards credential service. They never become conversation drafts, model arguments or tool results. Pre-login intent calls expose only registration/login form openers. `Reply.data` account/card lists are shown by React and excluded from model history. See [Customer banking](customer-banking.md) for session and ownership boundaries.

The HTTP services, durable jobs, remote MCP catalogue and Cards contract remain
separate. The model boundary now has one shared `build_context` projection,
used by the agent and defensively repeated in the Anthropic adapter. The browser
does not send private identifiers through the model conversation.

## Create a simulated card

Start the stack with `scripts/stack.ps1 -Build`, open http://localhost:8080 and
sign in with the existing synthetic credentials. With Claude enabled, start a
conversation and type **"I would like a new card"**. The model identifies the
action and the UI opens its matching private form automatically. Enter `Alex`,
select the masked account ending `5678`, click
**Review action**, then **Create card**. The username comes from verified
identity. The picker contains only accounts from the signed customer assertion.
No full account number is returned to React.

Chat is the primary entry point for intent, questions and safe corrections.
The secondary **Choose an action manually** panel retains a structured-only flow
that makes **zero model calls**, even with the Anthropic adapter configured.
After completing the form, "Create the card using these details" can produce a
model proposal using opaque references. Confirmation and eligible replay still
make zero additional calls. The result displays the actual `SIM-...` card and
reference returned by Cards, using catalogue labels and the same renderer for
immediate replies and polled jobs.

## State and trust boundaries

| Data | Location and model policy |
| --- | --- |
| Actual account, username and structured private fields | Private conversation bindings and exact execution state in Agent SQLite; no model projection |
| Account options | Masked label and random `selected_account_...` reference returned to the authenticated caller |
| Reference binding | Bound to owner, conversation, tool and field; checked on structured input and model-proposed use |
| Verified draft facts | Revalidated bindings and authorizations projected as references; only explicit `context_fields` may carry safe scalar values |
| Messages | Sanitised before durable acceptance and again before model calls; at most eight recent chat/model turns, latest message up to the existing 4,000-character API limit and older turns capped at 2,000 |
| Reviews, tool results, credentials and submission state | Never serialised wholesale to the model; workflow history uses neutral markers |
| Provider metrics | Counts only; no prompts, request bodies, customer IDs, account values or credentials in metric logs |

References are random letters with a scoped prefix, not encoded or hashed account
numbers. A reference from another conversation is rejected even when the same
user owns both conversations. Cross-user conversation access returns 404.
Every execution rechecks permissions, selected account membership and username,
then executes the exact stored proposal. Cards independently verifies the signed
delegation and enforces its existing resource authorization.

References are resolved into an exact snapshot when the review is created.
Confirmation reauthorizes that saved snapshot; later form changes cannot
silently retarget an already reviewed payload or its retry key.

The Agent keeps all permitted tools available for an open-ended turn, including
when a different tool is already drafted. `permissions` in catalogue entries are
checked against signed customer permissions; `MODEL_ALLOWED_TOOLS` is an
optional server-side allowlist for explicit workflow restrictions. The default
demo grants `tools:execute` to authenticated customers when OIDC supplies no
more specific `permissions` claim; a supplied empty list grants nothing. A trusted
OIDC permissions claim or server policy can narrow this. MCP checks the signed
permissions again at invocation. Browser fields cannot grant permissions.

New messages and accepted structured corrections invalidate older reviews,
including when replacement structured details fail validation. Corrections to
private values use the form; safe fields such as the block fixture's `reason`
can be proposed by the model. Ambiguous "use the first one" references receive a
deterministic clarification, rather than guessing or making a retrieval/model
call. Legacy unmarked conversation turns are omitted from new model requests;
existing exact proposals and downstream idempotency keys remain server-side.

The fallback detector rejects likely numeric identifiers, formatted card
numbers, CVV/PIN/credential labels, common token formats, emails and labelled
customer details. Known private values are removed using current bindings and
verified identity. Rejected private pastes become a canonical safe notice before
job persistence and cause zero model calls. Validation errors are generic and
never echo input. This is conservative and can reject benign numbers; it is not
a comprehensive DLP classifier for arbitrarily encoded or unlabelled secrets.
The structured path and deny-by-default model field projection are the primary
controls. Use synthetic data only; exact values needed for execution remain
unencrypted in the existing local SQLite journal and Cards store.

## Catalogue-driven forms and model schemas

The existing OpenAPI operation mapping still constructs MCP's downstream call.
The catalogue adds optional policy metadata:

```json
{
  "trusted_fields": {"account_number": "account", "user_name": "username"},
  "context_fields": [],
  "permissions": ["tools:execute"]
}
```

`trusted_fields` declares account picker or verified-username sources. All other
private fields use structured input. `context_fields` is an explicit allowlist
for non-sensitive scalar facts; it defaults empty and rejects identifier,
credential, sensitive and trusted fields. The fixture `block_card` allows only
`reason`; its card ID remains private. The model receives reference-string
schemas for private fields, while MCP retains the original execution schemas.
Model-facing proposal arguments are optional so Claude can identify intent
before private values exist. Original required-field validation still applies
before any review or execution. `Reply.input_tool` is the typed cue used by
React to display the matching form in both immediate and polled responses.
Examples/defaults and arbitrary schema annotations never enter the model prefix.

React renders the returned scalar form fields and masked options generically.
Adding a supported operation/catalogue entry still requires no Agent routing
branch or UI component. The dedicated API is:

- `GET /api/conversations/{id}/inputs`: masked form options, no raw account IDs.
- `POST /api/conversations/{id}/inputs`: `{tool, fields, selections}`; trusted
  fields cannot be supplied as raw values. Return a review, not a write.

Agent exposes the same paths without `/api`. Structured submissions support the
existing `Idempotency-Key`, request-response and `Prefer: respond-async` contracts.
Polling stays authenticated and caller-bound through Experience. Network retries
reuse the submission key; after an explicitly uncertain execution reply, the
confirmation button creates a new HTTP operation while preserving the stored
downstream Cards key and payload.

## Optional Anthropic caching

Caching defaults **off**. `ANTHROPIC_PROMPT_CACHE=true` enables the installed SDK's
explicit system-block `cache_control={"type":"ephemeral"}` mechanism. The
breakpoint follows stable instructions and sorted eligible tool schemas; dynamic
messages, history, facts and references follow it and have no cache marker.
Top-level automatic conversation caching is deliberately not enabled.

The configured `claude-haiku-5-5` currently requires a 512-token cacheable prefix;
other model thresholds vary. Prefixes must match exactly and meet the model's
minimum; short prefixes may run without caching. The default ephemeral lifetime
is five minutes. Check the provider's [current eligibility table and usage
definitions](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
when changing `MODEL_ID`. Do not pad prompts or make extra calls merely to warm
a cache. Changing the eligible tool set intentionally changes the prefix.

To opt into a measured Claude experiment in PowerShell, with an existing valid
key/model in ignored `.env`:

```powershell
$env:STACK_MODEL_PROVIDER = 'anthropic'
$env:ANTHROPIC_PROMPT_CACHE = 'true'
.\scripts\stack.ps1 -Build
# Use ordinary safe intent turns, then inspect Reply.usage / model_usage logs.
# Reset to the credential-free default after the experiment:
$env:STACK_MODEL_PROVIDER = 'stub'
$env:ANTHROPIC_PROMPT_CACHE = 'false'
.\scripts\stack.ps1
```

The flag is forwarded only to Agent. It is also documented in `.env.example`.
The SDK credential remains transport authentication, never prompt or cached text.

## Measurements and verification

Each `Reply.usage` contains cumulative conversation `model_calls`, input/output
tokens, cache read/write input tokens and `completed_actions`. Calls count
adapter invocation attempts. Anthropic `model_usage` logs contain reported
numeric usage only; failures log no exception/prompt body and mark tokens as
unreported. `workflow_usage` records the tool name and cumulative counters once
per first completed action; duplicates do not increment completions. Unreported
tokens are not estimated as billable usage.

For a completed one-card conversation, compare model calls and reported tokens
per completion. Total processed input is ordinary input plus cache reads plus
cache writes. A cache hit must be demonstrated by nonzero reported reads; a
configured flag or a mocked counter is not evidence of a real hit.

Baseline before this change: **105 tests passed**, one existing deprecation
warning, and diagrams were current. The current suite passes **143 tests**.
Automated outgoing-payload tests now use an
actual Anthropic SDK with a mocked HTTP transport, not just a mocked adapter
method. They inject private values into text, history, drafts, tool results and
schema defaults/examples, and assert absence in the serialized request and
cached prefix. Both modes execute through real remote MCP and Cards with the
correct private account, lose the first committed response, and replay the same
payload/key without another model call.

Measured automated call counts: structured-only card flow **0**; optional
model-assisted proposal **1**; confirmation, eligible retry and duplicate
confirmation **0 additional** in both modes. Mock token/cache counters test
instrumentation only. **No live Claude token measurements or real cache hits
were collected during that privacy verification.** A subsequent chat-first
smoke test verified one live Claude intent request (1,062 input tokens, 97 output
tokens, zero cache reads/writes), without confirmation or a Cards write. This
is not a completed-card cost benchmark. The current suite passes 143 tests.
See [verification results](http-stack-verification.md).

The earlier scripted, all-details real-MCP retry fixture asserted one model
invocation per completed card. Its structured-input replacement asserts zero.
This is an automated call-count comparison for that fixture, not a measured
percentage reduction in live Claude tokens, latency or cost.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\stack.ps1 -Build
.\.venv\Scripts\python.exe scripts/smoke_stack.py
```

The [Markdown architecture diagrams](architecture.md) describe the privacy boundary. The old inline Python chat UI script now points to this
React flow, so it no longer encourages private values in chat. The isolated
`smoke_chat.py` harness uses structured inputs and requires a separately running
test MCP endpoint; authenticated Compose verification uses `smoke_stack.py`.
