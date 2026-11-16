---
policy_id: POL-003
version: 1.0.0
effective_date: "2026-01-01"
status: approved
approved: true
title: Post-Block Customer Journey Policy
applicable_intents:
  - block_card
triggering_events:
  - card_blocked_success
  - post_block
conditions:
  block_reasons:
    - lost
    - stolen
    - compromised
    - fraud
    - destroyed
  offer_on_temporary_freeze: false
required_actions:
  - offer_replacement_card
  - await_customer_decision
  - delegate_to_cards_agent_on_acceptance
  - complete_workflow_on_decline
restrictions:
  - proactive_offer_only_for_compromised_cards
  - require_explicit_customer_acceptance
source: policies/POL-003-post-block-journey.md
---

# POL-003: Post-Block Customer Journey and Replacement Rules

## 1. Scope and Objective
This policy dictates customer experience standards and follow-up journeys immediately subsequent to the successful blocking of a customer payment card.

## 2. Mandatory Post-Block Replacement Offer
1. **Compromised, Lost, or Stolen Cards:**
   - After successfully blocking a card due to loss, theft, fraud, or compromise, the customer must be proactively offered a replacement card.
   - The supervisor must explain that the card has been secured and ask whether the customer would like to order a replacement.
2. **Temporary Customer Freeze:**
   - For cards blocked under a temporary customer freeze (where `offer_on_temporary_freeze` is false in version 1.0.0), a replacement card is **not** proactively offered because the card is intended to be unblocked by the customer when located.

## 3. Customer Decision Handling
1. **Customer Acceptance:**
   - If the customer affirms or accepts the offer (e.g., *"Yes, please"*, *"Yes"*, *"Order replacement"*), the supervisor transitions the workflow to the Cards specialist (`cards_agent`) with intent `replace_card`.
   - The specialist checks replacement eligibility under **POL-002** and prepares a new card replacement proposal requiring separate customer confirmation.
2. **Customer Decline:**
   - If the customer declines the offer (e.g., *"No, thanks"*, *"No"*), the supervisor acknowledges the decision, confirms the card remains safely blocked, and concludes the workflow without issuing a replacement.
