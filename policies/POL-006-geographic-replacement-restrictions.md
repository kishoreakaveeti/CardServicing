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
  home_country_code: AU
  excluded_country_codes:
    - KP
  overseas_replacement_requires_fincrime_approval: true
  domestic_replacement_requires_fincrime_approval: false
  unknown_country_action: REQUIRE_CLARIFICATION
  security_card_blocking_allowed: true
  transition_review_enabled: true
  transition_review_lookback_hours: 72
  transition_review_applicable_intents:
    - replace_card
  transition_review_decision: REQUIRES_HUMAN_APPROVAL
  transition_review_reason_code: RECENT_EXCLUDED_COUNTRY_TRANSITION
required_actions:
  - validate_declared_current_country
  - enforce_geographic_rules_before_replacement
  - require_customer_confirmation
  - require_fincrime_approval_for_overseas_replacement
  - require_fincrime_approval_for_recent_excluded_to_permitted_country_transition
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
Australia (AU) is the domestic home country.

Permitted overseas destinations (such as New Zealand NZ, Singapore SG, South
Korea KR, or United States US) are not excluded, but require FinCrime staff
approval prior to replacement card issuance. Domestic replacement in Australia
does not require FinCrime human approval under the baseline demonstration rules.

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

## FinCrime staff approval workflow

When an overseas customer in a non-excluded country requests a replacement, the
workflow suspends and creates a persistent FinCrime review case. Upon approval
by an authorized FinCrime analyst, the workflow resumes and executes replacement
issuance through the trusted Cards API. Rejection concludes the replacement
attempt while preserving card blocking.

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
