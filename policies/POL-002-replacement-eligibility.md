---
policy_id: POL-002
version: 1.0.0
effective_date: "2026-01-01"
status: approved
approved: true
title: Replacement Card Eligibility and Lifecycle Policy
applicable_intents:
  - replace_card
triggering_events:
  - replacement_requested
  - card_blocked_permanent
  - card_lost_or_stolen
conditions:
  card_status: blocked
  block_reasons:
    - lost
    - stolen
    - fraud
    - compromised
    - destroyed
required_actions:
  - verify_card_is_permanently_blocked
  - prepare_replacement_proposal
  - require_independent_confirmation
restrictions:
  - distinguish_from_additional_issuance
  - do_not_require_unblocking_compromised_card
  - never_treat_interchangeable_with_issue_card
source: policies/POL-002-replacement-eligibility.md
---

# POL-002: Replacement Card Eligibility Rules

## 1. Scope and Distinction
This policy defines the governance, eligibility verification, and execution controls for replacing existing payment cards that have been rendered unusable or compromised.

Enterprise risk principles strictly distinguish `replace_card` from `issue_card`:
- `issue_card`: Creates a brand-new additional payment card linked to the customer account, subject to account-level block restrictions under POL-001.
- `replace_card`: Re-issues a credential replacing a compromised or defective card for the same account, while keeping the original compromised card permanently blocked and retired.
- **Rule:** `replace_card` and `issue_card` must **never** be treated as interchangeable operations by supervisors, agents, or tools.

## 2. Eligibility Criteria
A replacement card may only be issued when all following criteria are satisfied:
1. The target card is currently in a `blocked` status.
2. The block reason indicates permanent loss, theft, compromise, or damage (`lost`, `stolen`, `fraud`, `compromised`, `destroyed`).
3. An active card that is not blocked cannot be replaced; it must first be reported and blocked.
4. The customer is not required to unblock the original compromised card. Unblocking a compromised card violates risk policy.

## 3. Workflow Delegation and Authorization
1. Eligible replacement requests are delegated exclusively to the Cards domain (`cards_agent`).
2. The specialist verifies customer ownership and account eligibility.
3. Every card replacement requires independent customer confirmation through the deterministic UI review flow prior to execution in the trusted core banking API.
