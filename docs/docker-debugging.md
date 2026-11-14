# Run and debug the API in Docker

The API has its own image. MongoDB, Redis, and RabbitMQ keep their separate images;
Compose connects them. SQLite is mounted from `./data` to `/data`, so DB Browser
still opens `data/cards.db` on Windows. Stop the host **Debug API** session before
switching to container development to avoid running different code versions against
the same database. Keep Docker Desktop running.

## Start the container and attach VS Code

From PowerShell at the project root:

```powershell
.\scripts\start-docker-api.ps1 -DebugApi -Build
```

The script finds Docker even when it is not on PATH, builds the image, starts the
API and its MongoDB dependency, and waits for health checks. Run
`scripts/setup-infra.ps1` first if `.env` does not exist. Existing Redis/RabbitMQ
containers can stay running; card processing does not use them yet.

1. Open `src/card_servicing/cards.py` and set a breakpoint on
   `card, replayed = store.create_idempotent(instruction, idempotency_key)`.
2. Open **Run and Debug** (**Ctrl+Shift+D**), choose **Attach to Docker API**, and
   press **F5**. This attaches to the running container; **Debug API** launches
   the separate Windows-host process.
3. Open **<http://127.0.0.1:8011/docs>** and refresh it if already open.
4. Expand **POST /cards**, select **Try it out**, enter a fresh
   **Idempotency-Key** such as `docker-debug-001`, and execute:

```json
{
  "user_name": "alex.smith",
  "given_name": "Alex",
  "account_number": "0012345678"
}
```

The browser waits while VS Code pauses. Inspect `instruction` and
`idempotency_key`. **F11** steps into the SQLite store, **F10** steps over a line,
and **F5** continues so Swagger receives the response. Reusing the same key
exercises replay. Disconnecting VS Code leaves the container running.

| Connection | Address |
| --- | --- |
| Container Swagger UI | <http://127.0.0.1:8011/docs> |
| Container liveness | <http://127.0.0.1:8011/health/live> (`/health` is an alias) |
| Card readiness | <http://127.0.0.1:8011/health/ready> |
| Container OpenAPI | <http://127.0.0.1:8011/openapi.json> |
| VS Code attach | `127.0.0.1:5679` |
| Optional Windows-host API | `127.0.0.1:8010` |

The container listens on `0.0.0.0:8000` internally, published to host port 8011.
Use `API_PORT` to change the published API port if needed. MongoDB connections use
the Compose hostname `mongodb`, rather than `127.0.0.1`, which inside a container
would point back to that container.

The debugger starts without waiting for attachment, so Swagger and health checks
work immediately. It runs one worker without reload for predictable stepping.
Long breakpoint pauses can cause browser/health-check timeouts; continue and retry
the same card key if necessary. Compose checks card readiness; see
[Health checks](health-checks.md) for the distinction and probe limitations.

## Source mappings and rebuilds

VS Code maps `${workspaceFolder}/src` to `/app/src`. The image contains a source
snapshot, without a live source mount. After editing code, disconnect, run the
startup command with **-Build**, and attach again. This preserves SQLite data.
Hollow breakpoints generally mean a stale image or an incorrect source mapping.

Equivalent commands when `docker` is available on PATH:

```powershell
docker compose -f compose.yaml -f compose.debug.yaml up -d --build --wait api
docker compose logs --tail 50 api
docker compose stop api
```

The API has an opt-in profile so the existing infrastructure setup remains usable
for host debugging. Explicitly targeting `api` starts it without a profile flag.

To stop and start the full existing stack while preserving its configured debug
or normal startup mode:

```powershell
docker compose --profile api stop
docker compose --profile api start
```

`start` does not rebuild or recreate containers. To create/update all four services
in debug mode, use
`docker compose -f compose.yaml -f compose.debug.yaml --profile api up -d --build --wait`.
The profile selects the API in addition to the three default infrastructure
services; the debug override selects the debugger command. See the full
[daily command reference](local-infrastructure.md#daily-commands).

## Use the same image without a debugger

The Dockerfile's normal command runs Uvicorn without a debugger listener.
`compose.debug.yaml` overrides the command to launch through debugpy and publishes
5679 on localhost only. debugpy is included in the image but inactive during normal
startup. Keep that override local: debugger access permits executing code.

To switch the already built image to normal execution:

```powershell
docker compose -f compose.yaml up -d --no-build --wait api
```

Swagger remains on port 8011; port 5679 is no longer published. For another debug
session, run `scripts/start-docker-api.ps1 -DebugApi` and attach again.

Build once with a release tag, then promote that existing image rather than
rebuilding for each environment:

```powershell
docker build -t card-servicing:release-001 .
docker image save --output card-servicing-release-001.tar card-servicing:release-001
```

Transfer and load it elsewhere with
`docker image load --input card-servicing-release-001.tar`, or use a registry and deploy by
immutable digest. With this local Compose file, set `API_IMAGE_TAG=release-001` and
start with `--no-build`. Each environment supplies configuration, secrets, storage,
networking, and its own deployment definition. The local bind mount and development
credentials are not production configuration.

`.dockerignore` allowlists inputs and excludes `.env`, databases, Windows `.venv`,
Git metadata, and generated caches. The API runs as non-root. Base image tags and
dependency ranges are not a lockfile: promoting the existing image preserves its
installed versions; rebuilding later can change them. Authentication, production
encryption, and SQLite scaling limits remain separate learning topics. Basic
liveness/readiness endpoints are implemented; deployment probe policies still
need to be selected for each environment.

Verified for this project: image build, healthy debug-mode container, HTTP card
creation, retry replay, conflicts, persistence across container restart, and live
MongoDB logs/traces. VS Code attachment and interactive breakpoint stepping remain
manual checks; the launch configuration and source mappings are provided.

References: [VS Code Python remote debugging](https://code.visualstudio.com/docs/python/debugging),
[Compose profiles](https://docs.docker.com/compose/how-tos/profiles/), and
[dependency health checks](https://docs.docker.com/compose/how-tos/startup-order/).
