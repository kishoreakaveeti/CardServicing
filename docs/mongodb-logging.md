# MongoDB service logging

The full `compose.yaml` stack includes `mongodb` (`mongo:8.0`) and enables `TELEMETRY_SINK=mongo` by default for Experience, Agent, MCP and Cards. Starting the stack with `.\scripts\stack.ps1 -Build` starts MongoDB automatically. An explicit `TELEMETRY_SINK=stdout` or `none` overrides the sink, but the Mongo service still starts with the full stack.

Run `.\scripts\setup-stack.ps1` once after upgrading an existing checkout. It adds Mongo credentials and an account hashing key to ignored `.env.stack`, preserving existing identity credentials and reusing original `.env` Mongo credentials when available. Changing initialization credentials does not update an already initialized Mongo volume; preserve the credentials used when that volume was created.

MongoDB listens on `mongodb:27017` inside Compose and `127.0.0.1:27018` on Windows (`MONGODB_HOST_PORT` can override the host port). Its `mongodb_data` named volume persists across restarts. Do not run the older `compose.infra.yaml` Mongo container on the same host port at the same time. The full-stack volume is separate from the earlier infrastructure project's volume; existing logs in the earlier volume are preserved there.

For native VS Code application debugging, start the Docker dependencies without building application images:

```powershell
docker compose --env-file .env --env-file .env.stack -f compose.yaml up -d --wait identity mongodb
```

Alternatively, select **Debug Full Stack (Local)** directly: its preparation task starts missing local Identity/MongoDB dependencies automatically when Docker Desktop is running. Healthy existing dependencies are reused. Native services use `127.0.0.1:27018` and honor `TELEMETRY_SINK`; they no longer force `none`. Set `LOCAL_MONGODB_HOST` or `MONGODB_HOST_PORT` if using different native destinations.

## What is collected

| Collection | Events |
| --- | --- |
| `card_servicing.application_logs` | Existing card creation/replay/failure diagnostics; completed customer registration/login, account creation and card issuance; model token usage, workflow counters and MCP tool timing/outcomes |
| `card_servicing.request_traces` | Cards, Experience, Agent and MCP HTTP method, route template, status, duration, service and correlation ID |

The existing `MongoTelemetry` adapter handles all services: bounded asynchronous queue, seven-day TTL indexes, trace lookup index, and safe console fallback when Mongo is unavailable. Cards API readiness remains based on SQLite, not Mongo connectivity. Compose waits for Mongo health during initial stack startup; a later logging outage does not block banking requests.

Only explicitly tagged structured Agent/MCP log events are exported. Arbitrary Python log text, prompts, credentials, bearer tokens, exception messages, request bodies and raw account numbers are excluded. Account identifiers are optional keyed HMAC hashes. Logs are best-effort operational diagnostics, not a durable audit ledger. SQLite remains authoritative for customers, accounts, cards and idempotency responses.

Mongo credentials are available in ignored `.env.stack`. Connect with MongoDB Compass to `127.0.0.1:27018`, authentication database `admin`, and inspect the two collections above. Perform an application action, allow the worker queue to flush, then filter by `service` or `trace_id`.

Run `.venv\Scripts\python.exe scripts/check_mongo_logging.py` to verify the queue-to-Mongo round trip with synthetic events. The smoke records expire under the same TTL policy. This check was run successfully against the Docker Mongo service during implementation.

`tests/test_service_observability.py` tests the actual queued Mongo adapter against an in-process client double, including field redaction, collection routing, TTL indexes, lifecycle cleanup and outage behavior. Existing persistence/configuration tests cover Cards diagnostics.

## Idempotency versus an existing card

The existing implementation distinguishes an operation by its idempotency key, not by natural-language wording:

| Situation | Current behavior |
| --- | --- |
| Same key and same normalized body | Return the original response without another write; the legacy `/cards` route also sets `Idempotency-Replayed: true` |
| Same key with different details | Return 409 conflict |
| Fresh workflow/new key on an account that already has a card | Return the existing card with `already_exists`; explicit `additional_card=true` is required for another card |

Channels retrying one operation must share its operation key. Identical chat text alone cannot establish that two requests represent the same operation.

The implemented business rule is **existing card by default, explicit additional card**: ordinary issuance checks for an existing card in the same database transaction as creation; if one exists, return it with an `already_exists` outcome and message. A separate explicit additional-card choice, shown in the review and confirmed by the customer, permits another card. A durable customer/account-level constraint or serialized transaction must enforce the rule across concurrent channels; an LLM check or a UI-only preflight is insufficient. Key lookup must occur before the existence check so genuine retries preserve their original response. A time-based deduplication window alone would permit duplicates again after the window expires.

This policy is now implemented for both card issuance endpoints; see [Card issuance](card-issuance.md) for transaction, migration and response details. A replay remains the original response, while a new request blocked by the account-level rule should explain that a card already exists.
