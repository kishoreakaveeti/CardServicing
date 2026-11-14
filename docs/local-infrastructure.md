# Local infrastructure

The API now selects its telemetry sink at startup. The existing Mongo setup is
the default. For stdout/none with Mongo stopped, use the
[adapter-switching guide](infrastructure-adapters.md) and `compose.api.yaml`
(`scripts/start-docker-api.ps1 -Standalone`). Redis and RabbitMQ remain outside
the creation path.

Verified on 14 November 2026: all three infrastructure containers healthy;
SQLite temporary SQL round trip, authenticated MongoDB write/read/delete,
Redis expiry round trip, and RabbitMQ confirmed publish/consume/ack passed.
The API image also built successfully and passed live creation, replay, conflict,
restart durability and MongoDB diagnostic checks. The isolated test suite passed
45 tests. One upstream Starlette warning reports future HTTPX TestClient changes.

The API can run on Windows or in a Linux container with VS Code debugger
attachment. MongoDB Community, Redis, and RabbitMQ run in separate Linux containers
under Docker Desktop/WSL 2. SQLite is built into
Python; no SQLite server installation is needed. MongoDB Community is our local
document database, rather than a separate product called MongoDB Lite.

Infrastructure smoke checks and Docker debugging are provided for local verification. Automated test results are recorded when run; use the current pytest
output rather than a fixed count in this setup guide.

## Start and verify

From the project root, with Docker Desktop running:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,infra]"
.\scripts\setup-infra.ps1
.\.venv\Scripts\python.exe scripts/check_infra.py
```

The setup script finds Docker even when a newly installed CLI is not yet on PATH,
creates `.env` with generated local passwords if absent, and waits for healthy
containers. It preserves existing `.env` settings. The check creates the SQLite
file and verifies temporary SQL data, authenticated MongoDB write/read/delete,
Redis write/read/delete with expiry, and a confirmed RabbitMQ publish/consume/ack.
It removes its test documents, keys, and queue. The smoke check does not create or
migrate card tables; the API initializes that schema at startup. It can create
the SQLite file if missing, and leaves any existing card data intact.

| Component | Host connection | Notes |
| --- | --- | --- |
| SQLite | `data/cards.db` | Local file, excluded from Git |
| MongoDB | `127.0.0.1:27018` | Database `card_servicing`; credentials in `.env`, authentication database `admin` |
| Redis | `127.0.0.1:6380`, database 0 | Disposable cache, no persistence, 128 MB cache limit |
| RabbitMQ AMQP | `127.0.0.1:5673` | Credentials in `.env`, default virtual host `/` |
| RabbitMQ management | <http://127.0.0.1:15673> | Sign in with `RABBITMQ_USER` and `RABBITMQ_PASSWORD` from `.env` |

All published ports bind to localhost. MongoDB and RabbitMQ data use named Docker
volumes and survive container replacement. Redis intentionally starts empty.
MongoDB uses a local administrator account for these initial exercises; introduce
a database-scoped telemetry account with appropriate index permissions in a later
security increment.
Redis has no password in this localhost-only learning setup.

The `.env` values are consumed by Compose, the check script, and the normal
FastAPI entry point. FastAPI now uses SQLite for cards and asynchronous MongoDB
diagnostics; see [Card persistence](card-persistence.md). Redis caching and
RabbitMQ card processing remain future increments. The RabbitMQ smoke check
verifies transport, not the planned card outbox workflow.

## Dashboards and data viewers

The API can now run in Docker: see [Docker debugging](docker-debugging.md).
For that mode, use <http://127.0.0.1:8011/docs> instead of host port 8010.
Infrastructure dashboards and the host SQLite file location are unchanged.

Use this table as the access reference. Swagger UI and RabbitMQ management are
available in a browser. MongoDB, Redis, and SQLite do not have browser dashboards
in the current Compose setup; optional desktop viewers are listed below and have
not been installed by the infrastructure setup script.

| Component | Where to open it | What to inspect |
| --- | --- | --- |
| Docker API / Swagger UI | <http://127.0.0.1:8011/docs> | Container endpoints, schemas, and interactive requests |
| Host API / Swagger UI | <http://127.0.0.1:8010/docs> | Alternative Windows-host execution |
| API OpenAPI JSON | <http://127.0.0.1:8011/openapi.json> (Docker) / <http://127.0.0.1:8010/openapi.json> (host) | Live generated API contract |
| Docker API health | <http://127.0.0.1:8011/health/live> / <http://127.0.0.1:8011/health/ready> | Liveness / SQLite card readiness; `/health` aliases liveness |
| RabbitMQ | <http://127.0.0.1:15673> | Connections, exchanges, queues, message counts, and consumers |
| MongoDB | MongoDB Compass desktop app, connecting to `127.0.0.1:27018` | Databases, collections, documents, and indexes |
| Redis | Redis Insight desktop app, connecting to `127.0.0.1:6380` | Cache keys, values, types, and TTLs |
| SQLite | DB Browser for SQLite desktop app, opening `data/cards.db` | Tables, rows, schema, and SQL queries |
| Containers | Docker Desktop > Containers > `card-servicing` | Container status, logs, and resource usage |

### Where to find usernames and passwords

The file is named **`.env`**, not `local.env`. It is at the project root alongside
`compose.yaml`: `A:\PrincipalEngineer\CardServicing\.env` on this machine. In VS Code,
press **Ctrl+P**, type `.env`, and select that exact file (not `.env.example`).

- RabbitMQ: use the values of `RABBITMQ_USER` and `RABBITMQ_PASSWORD`.
- MongoDB: use `MONGO_USER` and `MONGO_PASSWORD`; authentication database is `admin`.
- Redis: no password is configured for this local setup.
- SQLite: no username or password; open the database file directly.
- Swagger UI: the current API has no authentication.

`.env.example` documents the fields; the generated passwords are in `.env`.
Keep actual passwords out of documentation and Git.

### Swagger UI

For Docker execution, run `scripts/start-docker-api.ps1 -DebugApi -Build`, select
**Attach to Docker API** in VS Code and press **F5**, then use
<http://127.0.0.1:8011/docs>. Rebuild and reattach after code changes. To run without
debugging, omit `-DebugApi`. See [Docker debugging](docker-debugging.md).

For the alternative Windows-host execution:

In VS Code, open **Run and Debug** (**Ctrl+Shift+D**), choose **Debug API**, and
press **F5**. Wait for Uvicorn to start, then open
<http://127.0.0.1:8010/docs> in your browser. Alternatively, from the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn card_servicing.main:app --reload
```

Expand an endpoint, select **Try it out**, enter the request, and select
**Execute**. Requests made here hit breakpoints when the API runs in debug mode.
Start only one host API instance on port 8010. `setup-infra.ps1` starts only
infrastructure; `start-docker-api.ps1` starts the API and its MongoDB dependency,
not Redis or RabbitMQ. Avoid running different host/container code versions
against the shared SQLite file at the same time.

### RabbitMQ management

With the RabbitMQ container running, open <http://127.0.0.1:15673> and sign in with
the RabbitMQ credentials from `.env`. Explore **Queues and Streams**,
**Exchanges**, and **Connections**. Port `5673` is for AMQP clients and is not a
browser dashboard. The smoke check uses a temporary queue that disappears when
it finishes; persistent card queues will be added with the event workflow.

### MongoDB Compass (optional installation)

Install [MongoDB Compass](https://www.mongodb.com/docs/compass/connect/) on Windows.
Add a connection to `mongodb://127.0.0.1:27018`. In its authentication options,
enter the MongoDB username/password from `.env` and set the authentication
database to `admin`, then connect. Browse the `card_servicing` database and collections.
Port `27018` speaks the database protocol; it is not an HTTP dashboard.
Open `application_logs` and `request_traces` for API diagnostics. The separate
card event projection is not implemented yet; the smoke check deletes its test document.

Without installing Compass, use the shell already inside the container:

```powershell
docker compose exec mongodb mongosh --username card_servicing --authenticationDatabase admin
```

Enter `MONGO_PASSWORD` at the password prompt. If `MONGO_USER` was customized,
replace `card_servicing` in that command. Use `show dbs`, `use card_servicing`, and
`show collections` to explore, then `exit` to leave.

### Redis Insight (optional installation)

Install [Redis Insight for desktop](https://redis.io/docs/latest/operate/redisinsight/install/install-on-desktop/).
Add a local connection with host `127.0.0.1`, port `6380`, database `0`, no password,
and TLS disabled. Leave the username unset, or use `default` if required by the
client. Use its key browser to inspect values and expiry. Port `6380` is not an
HTTP dashboard. The cache is currently empty unless you add test keys; API caching
is not wired in yet.

Without installing a viewer, run these read-only checks:

```powershell
docker compose exec redis redis-cli PING
docker compose exec redis redis-cli --scan
docker compose exec redis redis-cli INFO memory
```

### SQLite viewer (optional installation)

Install [DB Browser for SQLite](https://sqlitebrowser.org/), select **Open Database**,
and open `A:\PrincipalEngineer\CardServicing\data\cards.db`. Use
**Database Structure**, **Browse Data**, and **Execute SQL**
to inspect it. No server, port, or login is required. Starting the API creates
`card_statuses`, `cards`, and `card_idempotency`.
For the required Swagger header and replay examples, see the
[idempotency walkthrough](card-persistence.md#try-idempotency-in-swagger). The infrastructure
smoke check alone still creates only a temporary table.

If the file is missing, run `scripts/check_infra.py` using the command above to
create it. Close any viewer write transaction before debugging API writes to
avoid holding a database lock.

## Daily commands

Restart VS Code or its terminal after installing Docker so `docker` is on PATH.
The setup script also works without that restart.

```powershell
docker compose --profile api ps -a
docker compose --profile api logs --tail 50
```

`api` is the optional API profile. MongoDB, Redis, and RabbitMQ have no profile
and are included by default. Enabling `api` includes all four services.

| Task | Command |
| --- | --- |
| Stop all four services | `docker compose --profile api stop` |
| Restart existing stopped containers | `docker compose --profile api start` |
| Stop/start only the API | `docker compose stop api` / `docker compose start api` |
| Stop/start one infrastructure service | `docker compose stop rabbitmq` / `docker compose start rabbitmq` (or `redis`, `mongodb`) |
| Create/update all services in debug mode | `docker compose -f compose.yaml -f compose.debug.yaml --profile api up -d --build --wait` |
| Create/update all services in normal mode | `docker compose -f compose.yaml --profile api up -d --build --wait` |
| Build/update only the API and its dependency for debugging | `.\scripts\start-docker-api.ps1 -DebugApi -Build` |
| Build/update only the API and its dependency normally | `.\scripts\start-docker-api.ps1 -Build` |
| Remove all project containers/network, retaining data | `docker compose --profile api down` |

`stop`/`start` preserve existing container startup configuration, including debug
mode. `start` neither rebuilds code nor creates removed containers. `up` applies
the specified Compose configuration, recreating containers when needed. Debug
mode is selected through `compose.debug.yaml`, not through the profile alone.
After starting debug mode, attach VS Code separately. Omit `--build` when reusing
an existing image without source changes.

The API health check repeatedly requests `/health/ready` every 10 seconds, with
six failed probes required to mark unhealthy after the startup grace period.
Each probe rechecks SQLite; an earlier success is not cached. MongoDB is optional
telemetry, so its outage does not make card readiness fail. `--wait` checks
startup health of the selected services, including infrastructure health checks;
Compose then continues its periodic checks. It does not automatically restart an
unhealthy container or remove traffic. See [Health checks](health-checks.md).

Named MongoDB/RabbitMQ volumes and the host SQLite bind-mounted file survive
container removal. Avoid `docker compose --profile api down -v` unless you intend
to erase MongoDB and RabbitMQ data; Redis intentionally has no durable data.
Changing the initialization passwords in `.env` does not change
credentials already stored in existing volumes; use the service's user-management
commands to rotate them.

If startup fails, inspect Docker Desktop and Compose logs for port conflicts,
image download failures, or unhealthy services. No cloud service is used.
The image tags select MongoDB 8.0, Redis 8, and RabbitMQ 4 with management UI;
tags may receive updates. For a stricter reproducible baseline, record tested
image digests before a later deployment exercise.

Image references: [MongoDB Docker Official Image](https://hub.docker.com/_/mongo),
[Redis Docker Official Image](https://hub.docker.com/_/redis), and
[RabbitMQ installation](https://www.rabbitmq.com/docs/download).
