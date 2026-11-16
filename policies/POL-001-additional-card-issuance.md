---
policy_id: POL-001
version: 1.0.0
effective_date: "2026-01-01"
status: approved
approved: true
title: Additional Card Issuance and Account Restriction Policy
applicable_intents:
  - issue_card
  - create_card
triggering_events:
  - card_issuance_requested
  - blocked_card_detected
conditions:
  has_blocked_card: true
required_actions:
  - evaluate_unblock_eligibility
  - offer_unblocking_journey_if_eligible
  - prohibit_issuance_until_resolved
restrictions:
  - prohibit_issuance_if_any_card_blocked
  - never_suggest_unblocking_permanently_compromised
  - require_customer_confirmation_for_unblock
source: policies/POL-001-additional-card-issuance.md
---

# POL-001: Additional Card Issuance and Account Restriction Rules

## 1. Scope and Objective
This policy governs requests to issue new, additional, or secondary payment cards on existing customer transaction and savings accounts. The primary security objective is to prevent unauthorized credential sprawl and mitigate ongoing financial crime risks when an account is subject to active restrictions.

## 2. Core Restrictions on Issuance
Under banking enterprise risk rules:
1. If an account has any card in a `blocked` status, issuing a new additional card for that account is strictly **prohibited** until the underlying card restriction is resolved.
2. The orchestrator and specialist agents must verify account status and active card statuses before scheduling or confirming any card issuance action.
3. This pre-issuance security restriction cannot be bypassed by natural language reasoning or LLM override.

## 3. Customer Remediation and Unblocking Journeys
When an issuance request is blocked due to an existing card restriction:
1. **Temporarily Frozen Cards:**
   - If the existing blocked card is subject to a customer-initiated temporary freeze (`temporary_freeze`, `customer_freeze`, `temporary`), the customer is eligible for an unblocking remediation journey.
   - The supervisor must explain the restriction clearly to the customer and proactively inquire whether the customer desires to unblock the frozen card first.
   - If the customer accepts, the workflow transitions to the FinCrime domain (`fincrime_agent`) for formal review, deterministic confirmation, and unblocking execution.
   - Upon confirmed unblock success, the additional card issuance journey may resume.
2. **Permanently Compromised Cards:**
   - If the card is blocked due to loss, theft, counterfeit, fraud, or permanent compromise (`lost`, `stolen`, `fraud`, `compromised`, `destroyed`), agents and supervisors must **never** suggest or offer unblocking the card.
   - Instead, the customer must be guided toward the card replacement journey under policy **POL-002**.
