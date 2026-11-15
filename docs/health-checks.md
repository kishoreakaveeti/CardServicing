# Liveness and card readiness

Readiness also checks the schema version 3 `card_issuance_requests` table used for durable issuance decisions.

SQLite readiness also probes the customer, account, account-card link and banking retry tables. Customer banking shares the Cards database and existing trace middleware; see [Customer banking](customer-banking.md).

| Endpoint | Meaning | Responses |
| --- | --- | --- |
| `/health/live` | Application is responding; no database calls | 200 `{"status":"ok"}` |
| `/health` | Compatibility alias for liveness | Same as `/health/live` |
| `/health/ready` | Application startup completed and card storage is accessible | 200 ready, or 503 not ready |

For Docker, open <http://127.0.0.1:8011/health/live> and
<http://127.0.0.1:8011/health/ready>. For the Windows-host API, use port 8010.
The endpoints also appear in Swagger. Readiness responses disable HTTP caching.

Successful SQLite readiness returns:

```json
{"status":"ready","checks":{"sqlite":"ok"}}
```

An unavailable database or incomplete initialization returns HTTP 503:

```json
{"status":"not_ready","checks":{"sqlite":"unavailable"}}
```

The SQLite probe requires successful schema initialization and opens the configured
file using `mode=ro`, with a 250 ms SQLite lock timeout. It reads the expected
card/idempotency columns and checks the created status reference row in one
read transaction. It never creates a database, runs migrations, inserts records,
or acquires a write reservation. SQL errors are logged with exception type and
trace ID, without exposing paths, SQL text, or credentials in the response.

This is an inexpensive accessibility check, not an integrity scan or a write
guarantee. A read-only filesystem, disk exhaustion, or concurrent writers may still
cause a later card write to fail. Each SQL lock wait is bounded; the probe is
not a hard filesystem/network I/O deadline. Normal writes retain their two-second
lock timeout. The memory-store test configuration reports `card_store` instead
of claiming to check SQLite.

MongoDB is optional operational telemetry and does not determine readiness.
The same is true for `TELEMETRY_SINK=stdout|none`. The API reads readiness through
`CardStorePort`; each adapter names its dependency and translates expected failures
to `StorageUnavailable`. Invalid selected-adapter configuration fails startup;
a valid configuration with unavailable storage remains live but unready. See
[startup configuration](infrastructure-adapters.md).
Redis and RabbitMQ are also excluded until card processing actually depends
on them. The MongoDB Compose dependency requires its container to start, not to
pass its health check before the API starts. Readiness can recover from transient
read failures on the next probe. Initialization failures remain unready until
initialization succeeds (on restart or a subsequent card attempt); probes do
not repair or recreate storage.

Docker Compose now probes `/health/ready`. An unhealthy result marks the container
unhealthy; Compose does not automatically restart it or route traffic away.
The configured API interval is 10 seconds, timeout 5 seconds, retries 6, and
startup grace period 15 seconds. Every request performs a fresh readiness check;
the startup script's `--wait` only controls its initial wait for service health.
Docker does not separately poll `/health/live` in this Compose configuration.
Future Kubernetes configuration should use `/health/live` for liveness and
`/health/ready` for readiness, so a database outage does not cause restart loops.
A startup probe can provide initialization grace. A long debugger pause can make
either HTTP probe time out; continue execution before diagnosing storage failure.

Tests cover startup/lifecycle state, valid storage, missing/corrupt databases,
missing schema/reference data, failure recovery, read probes during a reserved
write transaction, no accidental file creation, liveness compatibility, and a
MongoDB outage independent of readiness.
