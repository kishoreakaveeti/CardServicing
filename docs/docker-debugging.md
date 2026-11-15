# Debug the full application in Docker

The authoritative installation and launch instructions are in [README: Docker containers](../README.md#1-run-and-debug-using-local-docker-containers). For native execution use [README: native mode](../README.md#2-run-and-debug-natively-on-your-computer). This guide covers the current full stack, not the archived API-only scripts.

## Prerequisites

Complete README shared setup: host Python/venv and generated credentials, Docker Desktop running Linux containers, VS Code Python/Python Debugger extensions, and Chrome for the browser profile. Host Node/npm, MongoDB Community Server, SQLite server/CLI, JDK and native Keycloak installations are unnecessary for Docker mode. Compose provides runtimes, Mongo and Identity; Python includes SQLite support. Compass is optional.

## Switch from native mode

1. Stop local debug sessions with Shift+F5.
2. Stop the **Start Local Frontend** task with its Terminal trash-can button or Tasks: Terminate Task. It can retain port 8080 after the debug session ends.
3. Resolve any native Identity/Mongo server using Docker's published ports. Existing Docker dependencies may stay running.

## Start and attach

Select **Debug Full Stack (Docker)** in Run and Debug and press F5. Its shared task builds/starts debug containers, waits for health/listeners, attaches four Python debuggers and opens Chrome at `http://localhost:8080`. You do not need a separate start command before this profile. **Debug Full Stack** is an alias.

For manual preparation, run:

```powershell
.\scripts\stack.ps1 -DebugComponent All -Build
```

Then select the full Docker profile or individual attach profiles. The full profile reuses prepared services. Individual attach configurations do not start containers.

| Profile | Host port | Source mapping |
| --- | --- | --- |
| Attach to Experience API | 5681 | workspace `src` to `/app/src` |
| Attach to Agent API | 5682 | Same |
| Attach to MCP Server | 5683 | Same |
| Attach to Cards API | 5684 | Same |

For only Agent, run `stack.ps1 -DebugComponent Agent -Build`, then select **Attach to Agent API**. Use `Experience`, `MCP` or `Cards` similarly. **Debug Frontend** opens a browser against an already running frontend.

## Trigger and inspect a request

Use the customer journey in README. Set a breakpoint in Agent `interpret_turn` for a chat intent; direct selections/private forms intentionally bypass it. For confirmed issuance, use MCP `execute`, Cards `issue_card`/`create_card`, or SQLite `issue_idempotent`. The optional additional-card flag must appear in the review before another card is created. Existing-card requests report that no new card was created.

Normal debug startup does not wait for attachment before HTTP starts. For Agent/MCP startup/discovery breakpoints, first prepare a healthy debug stack, then run:

```powershell
.\scripts\stack.ps1 -DebugComponent MCP -Build -WaitForDebugger
```

Attach using **Attach to MCP Server** and continue. This special command starts only the chosen service with `--no-deps`, so dependencies must already run. Use `Agent` similarly.

## Mongo, persistence and stopping

Mongo starts automatically from `mongo:8.0`; no Windows Mongo server installation is needed. Setting `TELEMETRY_SINK=stdout` or `none` changes logging but the current full Compose stack still starts its configured Mongo container. To run with no Mongo instance, use native application mode with console/disabled telemetry. See [Mongo logging](mongodb-logging.md).

Cards, Agent and Experience SQLite databases live in separate Docker named volumes. Native `data/local/` databases are separate; a native customer will not automatically exist in Docker. Do not expect DB Browser to open a Docker database at the native filesystem path.

After source edits, disconnect and relaunch the Docker profile to rebuild images. Restarting an old image does not include changed code. Shift+F5 disconnects debuggers but leaves containers running. Use `stack.ps1 -Stop` to remove containers/network while retaining volumes. Never remove volumes merely to resolve a debugger problem.

Older `Debug Chat + API`, `Debug API`, `Debug Chat` and `Attach to Docker API` profile names are no longer shipped. Their historical commands are not a current full-stack launch path.
