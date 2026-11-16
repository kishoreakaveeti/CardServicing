---
policy_id: POL-004
version: 1.0.0
effective_date: "2026-01-01"
status: approved
approved: true
title: Confirmation, Authorization, and Safe Execution Controls Policy
applicable_intents:
  - block_card
  - unblock_card
  - issue_card
  - create_card
  - replace_card
triggering_events:
  - state_changing_action
conditions:
  is_state_changing: true
required_actions:
  - verify_caller_authorization
  - require_deterministic_customer_confirmation
  - generate_and_enforce_idempotency_key
restrictions:
  - llm_decisions_cannot_bypass_authorization
  - retrieved_policies_cannot_override_controls
  - fail_closed_on_policy_conflict_or_missing
source: policies/POL-004-confirmation-and-authorization.md
---

# POL-004: Mandatory Confirmation, Authorization, and Idempotency Controls

## 1. Zero Trust and Non-Override Principle
Every state-changing operation across Cards Servicing and Financial Crime management requires deterministic authorization, explicit customer review, and idempotent execution controls.

Retrieved policy documents, LLM interpretations, and agent reasoning must **never**:
1. Bypass customer authorization or account ownership verification.
2. Override mandatory two-phase customer review and confirmation (`can_confirm = True`).
3. Execute state changes without cryptographic idempotency keys.
4. Access or expose unmasked customer PII or credentials.

## 2. Fail-Closed Governance
If a retrieved policy rule is:
- Missing when required for an operation,
- Ambiguous or conflicting with another active policy,
- Invalid or expired,

The supervisor and orchestrator must **fail closed**: reject the operation, notify the customer that the policy requirement could not be validated, and log a compliance alert.
