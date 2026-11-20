package banking.geographic

import future.keywords.in

default decision = "UNAVAILABLE"
default reason_code = "policy_unavailable"
default is_overseas = false
default review_required = false

# Check missing or ambiguous country context
missing_or_ambiguous {
    not input.country_code
}

missing_or_ambiguous {
    input.country_status != "explicit"
}

# Determine overseas classification based on home_country_code
is_overseas_calc {
    input.country_code
    input.country_code != input.rules.home_country_code
}

is_overseas {
    is_overseas_calc
}

# Check excluded list
is_excluded {
    input.country_code
    input.country_code in input.rules.excluded_country_codes
}

# Human approval checks
requires_overseas_approval {
    not is_excluded
    is_overseas_calc
    input.rules.overseas_replacement_requires_fincrime_approval == true
}

requires_domestic_approval {
    not is_excluded
    not is_overseas_calc
    input.rules.domestic_replacement_requires_fincrime_approval == true
}

review_required {
    requires_overseas_approval
}

review_required {
    requires_domestic_approval
}

# Evaluation precedence:
# 1. Missing or ambiguous -> REQUIRE_CLARIFICATION
decision = "REQUIRE_CLARIFICATION" {
    missing_or_ambiguous
}

reason_code = "country_required" {
    missing_or_ambiguous
}

# 2. Excluded country -> DENY
decision = "DENY" {
    not missing_or_ambiguous
    is_excluded
}

reason_code = "excluded_declared_country" {
    not missing_or_ambiguous
    is_excluded
}

# 3. Requires Human Approval -> REQUIRES_HUMAN_APPROVAL
decision = "REQUIRES_HUMAN_APPROVAL" {
    not missing_or_ambiguous
    not is_excluded
    requires_overseas_approval
}

reason_code = "overseas_fincrime_approval_required" {
    not missing_or_ambiguous
    not is_excluded
    requires_overseas_approval
}

decision = "REQUIRES_HUMAN_APPROVAL" {
    not missing_or_ambiguous
    not is_excluded
    not requires_overseas_approval
    requires_domestic_approval
}

reason_code = "domestic_fincrime_approval_required" {
    not missing_or_ambiguous
    not is_excluded
    not requires_overseas_approval
    requires_domestic_approval
}

# 4. Otherwise -> ALLOW
decision = "ALLOW" {
    not missing_or_ambiguous
    not is_excluded
    not requires_overseas_approval
    not requires_domestic_approval
}

reason_code = "country_eligible" {
    not missing_or_ambiguous
    not is_excluded
    not requires_overseas_approval
    not requires_domestic_approval
}
