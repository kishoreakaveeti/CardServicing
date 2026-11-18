---
policy_id: POL-005
version: 1.0.0
effective_date: "2026-01-01"
status: approved
approved: true
title: Overseas Lost Card Assistance (Synthetic Demonstration)
applicable_intents:
  - overseas_lost_card
conditions:
  rules_version: 1.0.0
  return_window_hours: 48
  simulation_only: true
restrictions:
  - never_freeze_account
  - require_card_block_confirmation
  - no_real_digital_issuance_or_cash_disbursement
source: policies/POL-005-overseas-lost-card.md
---

# Overseas lost card assistance

## Assistance and priorities
Secure the lost card by blocking that card only, after the customer's review and
confirmation. Do not freeze the customer's bank account. Card blocking does not
freeze the account. For a customer returning home within 48 hours, prioritize
checking digital card availability to restore access quickly. If digital access
is unavailable, consider emergency cash eligibility next. Avoid international
replacement delivery as the primary option for a short return journey. If neither
alternative is available, request specialist assistance. A replacement may still
be offered separately under the post-block policy with separate confirmation.

Digital-card and emergency-cash checks in this demonstration are synthetic,
read-only scenarios. They neither establish real eligibility nor issue a card or
disburse money. Specialist handoff is a request for human assistance, not a claim
that a case was created or accepted. Never promise funds, delivery or availability.
