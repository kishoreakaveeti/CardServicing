# HTTP stack verification — 15 November 2026

This is a historical record of earlier verification runs, not the current setup guide or a statement of current test counts/configuration. Use [README](../README.md) for the supported Docker/native startup paths and current behavior.

## Chat-first follow-up

The main interaction is now free text. A model proposal can identify a tool with
incomplete arguments; the Agent returns `Reply.input_tool` and React opens the
corresponding private form. The manual action picker is a collapsed secondary
path. MCP's original required fields, authorization and confirmation still gate
execution. Both Agent modes are covered by the new intent-to-form tests.

The local ignored `.env.stack` now explicitly selects `STACK_MODEL_PROVIDER=anthropic`
using the existing configured model/key; fresh installations still default to
the credential-free stub unless configured otherwise. Caching remains off.

Verification: **143 tests passed**, frontend type check and production build
passed, and diagrams were regenerated. One live non-sensitive request, "I would
like to create a new card, please", selected `create_card` with no review yet,
pending private inputs. It used one model call, 1,062 input tokens and 97 output
tokens, with zero cache reads/writes. No confirmation or Cards write was
requested by this live test. Rebuilt debug containers are healthy; VS Code
sessions need reconnecting after their processes restart. Interactive desktop
windows were not inspected or controlled for this change.

## Private-input follow-up

The follow-up started from the working stack with **105 passing tests** and a
passing diagram check; there were no baseline failures. Prior local changes
were preserved. The expanded suite now includes actual Anthropic SDK request
capture with a mocked HTTP transport, private-paste rejection, scoped reference
ownership, permissions and revocation, safe corrections/topic changes, invalid
correction/stale-review handling, and both Agent API modes with real remote MCP
and Cards. See [model privacy and measurements](model-privacy.md).

Automated measurements: structured review/creation **0 model calls**; optional
model-assisted proposal **1 call**, followed by **0 additional** calls for
confirmation, retry after a committed response is lost, and duplicate clicks in
both modes. The correct private account and same validated payload/execution key
reach Cards; only one card row is stored. Token/cache counters in transport
tests are synthetic, not live usage or evidence of actual cache hits. Caching
defaults off. No live Claude measurements were made.

Final verification passed:

- **141 tests**, with the single existing Starlette/httpx deprecation warning.
- Frontend production build and TypeScript check; npm audit reported zero
  vulnerabilities. These checks use the Node build stage; nginx runtime images
  intentionally do not contain npm.
- All seven generated diagrams and code anchors current; Cards OpenAPI export
  unchanged/current; `git diff --check` passed.
- Normal and debug Compose configurations validated. The normal stack was
  rebuilt and all six containers were healthy afterward.
- Both authenticated Compose modes passed through actual Keycloak, Experience,
  Agent, remote MCP and Cards, with masked picker options, cross-user reference
  rejection, stale review denial, duplicate handling and zero model calls for
  structured creation.
- Actual Keycloak credentials were checked on private boundaries: invalid
  credentials, wrong audiences and tampered delegation were denied. The Agent
  container's prompt-cache flag was confirmed false by default.

The final normal stack remains available at http://localhost:8080. Use the secure
form/picker, not pasted private JSON. No live Claude call, token benchmark or
actual cache hit was used to establish these results.

The earlier results below describe the initial HTTP refactor; its interactive
VS Code/browser limitations remain unchanged by this follow-up.

## Initial HTTP refactor

The working tree initially contained local edits in README.md,
docs/architecture.md, docs/chat-demo.md, docs/docker-debugging.md and
docs/local-infrastructure.md. Those edits were preserved; current-stack links
were added to the retained walkthroughs. No AGENTS.md instructions were found.

Before editing: **95 tests passed**, with one existing Starlette/httpx
deprecation warning. The former SVG freshness check passed at that time; it has since been retired. No baseline
failure was observed.

## Executed checks

| Check | Result |
| --- | --- |
| Original workflow suite after replacing stdio with remote HTTP MCP | 95 passed |
| Expanded Python suite | **105 passed**, one existing deprecation warning; includes token validation, ownership, durable jobs and fail-closed entry points |
| Existing real-MCP uncertain-response tests | Passed: same stored arguments/key after committed write, one Cards row, completed duplicate uses saved result |
| Catalogue-only second operation | Passed over real remote MCP: block_card fixture, no Agent/UI routing branch; unsafe uncertain block cannot replay |
| Review persistence and accepted-running job recovery | Passed across fresh Agent app instances using the same SQLite journal |
| Normal Compose image build and startup | All six containers healthy |
| Authenticated black-box HTTP smoke with actual Keycloak | Both request-response and submit-and-poll passed through Experience → Agent → MCP → Cards |
| Black-box browser-session boundary checks | Anonymous denial, CSRF denial, stale review, cross-user denial, masked review, account ownership, duplicate confirmations passed |
| Private-network checks with actual Keycloak token | Missing/invalid credentials rejected at Agent/MCP/Cards; wrong audience rejected; tampered customer assertion rejected |
| Fresh realm import | Passed: all destination scopes, OIDC PKCE sign-in and account claims; temporary verification realm removed afterward |
| Frontend TypeScript check and production build | Passed in Docker with Node 22 |
| Frontend npm audit | Zero reported vulnerabilities after updating Vite to 6.4.6 |
| Compose normal/debug syntax | Both `config --quiet` checks passed |
| Final normal-mode restoration | All six containers healthy; only loopback 8080/8081 published; debugpy absent from all four Python images; authenticated smoke passed again |
| Full debug startup script | All services healthy, four loopback listener mappings present; no HTTP startup wait/deadlock |
| Direct DAP source mapping checks | Experience, Agent, MCP and Cards each attached, bound a Windows-to-container breakpoint, paused, continued and detached |
| Individual debug modes | All four Python modes passed mapped-breakpoint checks with the other services running; Frontend-only mode served TypeScript source maps with all four Python listeners disabled |
| Startup breakpoint procedure | MCP catalogue and Agent discovery both paused before HTTP startup using `-WaitForDebugger`, then continued without a readiness deadlock |
| OpenAPI export and diagram freshness | Passed; code anchors verified |

The black-box smoke uses HTTP clients to exercise the same OIDC/session/CSRF and
typed APIs consumed by React. It does **not** claim that React was interactively
driven in a browser. The browser-access host was unavailable. Windows VS Code
was present and its Run and Debug controls were inspected, but repeated external
focus/window-bound changes prevented reliable UI input. Direct DAP tests verify
the actual debugpy processes and source mappings independently of VS Code's UI.

No live Claude smoke test was run. All end-to-end execution used the explicit
stub model. Anthropic adapter tests check automatic tool selection and model
payloads using a mocked Messages client.

## Interactive checks still pending

1. In VS Code select **Debug Full Stack** and press F5. Verify the shared
   **Start Full Debug Stack** task runs once, then four Python sessions and one
   Chrome session appear in Call Stack. Follow the breakpoint walkthrough in
   [the run/debug guide](http-stack.md#one-confirmed-request-breakpoint-walkthrough).
2. In the launched browser sign in as `alex.synthetic`, start a conversation and
   submit the guide's JSON. Verify React's `submit` breakpoint binds, review is
   masked, and no Cards write occurs before the confirmation button. Then step
   through Experience, Agent confirmation, MCP dispatch and the Cards write.
3. Repeat using the **Submit and poll** checkbox. Confirm a second click returns
   the saved result. Detach all debuggers and verify the application still serves
   requests. Press F5 again to check reuse/reopening of detached listeners.
4. For each individual configuration, first run
   `scripts/stack.ps1 -DebugComponent Experience` (or Agent, MCP, Cards, Frontend),
   select its matching launch and press F5. Check that the remaining services
   continue running and that only the selected Python debugger is enabled.
5. Use the guide's `-WaitForDebugger` commands for early catalogue/discovery
   breakpoints. This mode waits for a listener only; attach and continue before
   expecting the chosen service's HTTP health endpoint.

The local HTTP/sign-in setup, one-process Agent, unencrypted synthetic journal,
one-hour delegation expiry and lack of production account-registry/revocation
integration are deliberate demo limits documented in the guide.
