# Chat-first banking demo

Start with [README](../README.md): it has separate prerequisite/configuration/run/debug instructions for [Docker](../README.md#1-run-and-debug-using-local-docker-containers) and [native execution](../README.md#2-run-and-debug-natively-on-your-computer), including operation without native Mongo or any Mongo instance.

The browser UI is `http://localhost:8080` in either mode. Native port 8020 is the Experience/BFF API, not the UI. The application uses React, Experience, Agent, remote HTTP MCP, Cards, SQLite, Identity and optional Mongo diagnostics; it does not start a stdio MCP child.

## Try it

1. Select **Register / Sign up**, or type **I want to register**. Credentials go through the secure form and never enter the model.
2. View accounts. If empty, create a savings/transaction account, optionally choose a card, review and confirm.
3. View cards. Ordinary repeat issuance returns an existing card and explains that no new card was created. Explicitly select the additional-card option and confirm to create another.
4. Sign out/in to verify persistence. Register a second synthetic customer to check isolation.

Action buttons and chat reuse the same workflow. Buttons, secure form submissions and confirmation bypass the model. `STACK_MODEL_PROVIDER=stub` needs no model key and supports direct selection plus a few deterministic common intents. For richer language interpretation, configure Anthropic in README.

The optional original OIDC sign-in uses `alex.synthetic`/`sam.synthetic` with the generated `DEMO_PASSWORD`; those identities are distinct from SQLite-registered customers. Use synthetic data only. A card number beginning with `SIM-` is not a payment-network PAN.

## Debug and inspect

Use **Debug Full Stack (Docker)** for container attach, or **Debug Full Stack (Local)** for native launch. Do not run both application modes on 8080. Stop the native Vite background task as well as Python debug sessions before switching to Docker.

Conversations, private bindings, reviews and accepted operations persist in the Agent SQLite journal. Session records persist in the BFF database; account/card data persists in Cards SQLite. Mongo holds redacted operational diagnostics, not business records. Database state differs between native files and Docker named volumes.

For details see [customer banking](customer-banking.md), [card issuance](card-issuance.md), [end-to-end trace](chat-end-to-end.md), [model privacy](model-privacy.md), [tool catalogue](tool-catalogue.md) and [diagrams](diagrams/index.html). Earlier free-text credential collection and removed two-process debug profiles are not current behavior.
