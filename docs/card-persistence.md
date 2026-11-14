# Card persistence and diagnostics

Normal entry point `card_servicing.main:app` initializes `data/cards.db`.
`create_app()` defaults to isolated memory storage and disabled telemetry for tests;
inject `card_store=SQLiteCardStore(path)` or use `local_infrastructure=True`.

## Schema and transaction

`cards` stores UUID, UTC creation time, user name, given name, account number,
unique simulation card number and status. `card_statuses` seeds `created`.
`card_idempotency` stores the unique client key, canonical SHA-256 request fingerprint,
card foreign key, original response JSON and status 201. Schema version is 1.
The schema is dedicated to card origination and durable request replay.

Every SQLite connection enables foreign keys. `BEGIN IMMEDIATE` reserves the writer
before key lookup, coordinating concurrent threads and processes. Matching retries
replay the saved response; mismatches raise 409. Card and response snapshot insert
in one transaction, committing before return. Failure rolls back both. UUID-based
simulation numbers also have a database uniqueness constraint. A fresh key represents
a new origination; multiple cards for one account are allowed.

## Try idempotency in Swagger

Open `/docs`, expand POST /cards, enter key `card-001` and body:

```json
{"user_name":"alex.smith","given_name":"Alex","account_number":"0012345678"}
```

Execute twice: both responses are 201 with the same card number. The second has
`Idempotency-Replayed: true`. Change the account with that key to receive 409;
use a new key for a new card. Restart the API and retry to verify durable replay.

## Inspect SQLite

```sql
SELECT id, user_name, given_name, account_number, card_number, status, created_at FROM cards;
SELECT idempotency_key, card_id, response_status_code FROM card_idempotency;
PRAGMA user_version;
```

Use synthetic development data: names, accounts and full response snapshots are
readable in SQLite. This simulation does not issue real payment-network cards.

## Diagnostics and failures

MongoDB database `card_servicing` holds `application_logs` and `request_traces`.
The bounded 1,000-record queue exports asynchronously with seven-day TTL indexes.
Logs include event, card UUID, status and optional HMAC-SHA256 account hash, never
user/given names, card numbers, request bodies, keys or credentials. Hashing requires
an `ACCOUNT_HASH_KEY` of at least 32 bytes; otherwise account metadata is omitted.
`X-Trace-ID` correlates logs and request timings. Queue overflow or MongoDB failures
fall back to structured console diagnostics. Shutdown drains for up to three seconds;
abrupt exits may lose diagnostics. This is not a durable audit ledger or outbox.

SQLite lock waits are bounded at two seconds for writes. SQL errors return 503
without exposing exception messages. Liveness remains independent; readiness checks
the SQLite schema and created reference row using read-only queries. MongoDB,
Redis and RabbitMQ do not gate readiness. See [health checks](health-checks.md).
