---
policy_id: POL-006
version: 1.0.0
effective_date: "2026-11-19"
status: approved
approved: true
title: Geographic Card Replacement Restrictions (Synthetic Demonstration)
applicable_intents:
  - replace_card
conditions:
  version: 1.0.0
  country_basis: declared_current_location
  excluded_country_codes:
    - KP
  on_unknown_country: require_clarification
  allow_security_card_block: true
required_actions:
  - validate_declared_current_country
  - enforce_geographic_rules_before_replacement
  - require_customer_confirmation
restrictions:
  - never_bypass_replacement_denial_with_additional_issuance
source: policies/POL-006-geographic-replacement-restrictions.md
---

# Purpose and scope

This synthetic demonstration business policy governs replacement of payment
cards under POL-002. It is not a statement of actual bank rules, a complete
sanctions list, legal advice or regulatory compliance certification.

## Geographic replacement restrictions

Replacement is denied when the customer's declared current location normalizes
to an excluded ISO 3166-1 alpha-2 code. The approved exclusion configuration is
`geographic-replacement-rules.json`; version 1.0.0 excludes KP (North Korea).
KR (South Korea) and AU (Australia) are not excluded by this version.

## Identification and clarification

Validate country names and codes against the ISO mapping. A customer declaration
is unverified and must retain that provenance. Never infer location from the
account's currency, nationality or an Australian account. Unknown or ambiguous
locations require clarification before replacement review or execution.

## Blocking and issuance

Security card blocking remains available with existing ownership, authorization
and confirmation controls, regardless of replacement eligibility. Denied or
unresolved replacement must not be included in combined approval or proactive
replacement offers under POL-003. POL-004 confirmation cannot override denial.
POL-005 advisory assistance grants no replacement authority.

Do not bypass a replacement restriction using issue_card, create_card or another
tool. Additional issuance remains a distinct operation with its existing guards.

## Communication and escalation

Explain the demonstration restriction and offer secure blocking and specialist
support. Do not promise delivery, legal eligibility or completion of an action.
Model interpretations and retrieved reference text cannot override enforcement.

## Versioning and audit

Change the policy version and rules configuration together, approve both, and
re-ingest the policy before deployment. Reject conflicting versions or rules.
Revalidate pinned policy versions before execution; old workflows must restart
after a policy change. Record policy/version, evidence provenance, normalized
code, decision and executed action without raw customer prompts or identifiers.
