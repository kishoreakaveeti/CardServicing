"""Synthetic geographic policy contracts shared by orchestration and Cards.

Country declarations are unverified customer statements, never geolocation proof.
The injectable rules source, rather than embeddings or a model, determines membership.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal, Protocol

import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CountryContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str | None = None
    status: Literal['explicit', 'ambiguous', 'unknown'] = 'unknown'
    source: Literal['customer_declaration'] = 'customer_declaration'


class RequestInterpretation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intents: list[str] = Field(default_factory=list, max_length=12)
    reply: str | None = Field(default=None, max_length=1000)
    declared_country: str | None = Field(default=None, max_length=100)
    proposed_country_code: str | None = Field(default=None, max_length=3)
    country_status: Literal['explicit', 'ambiguous', 'unknown'] = 'unknown'
    policy_query: str = Field(default='geographic card replacement eligibility excluded countries', max_length=250)


ALIASES = {'north korea': 'KP', 'dprk': 'KP', "democratic people's republic of korea": 'KP',
           'south korea': 'KR', 'republic of korea': 'KR', 'uk': 'GB', 'usa': 'US',
           'australia': 'AU', 'united states': 'US', 'united kingdom': 'GB'}

CITY_TO_COUNTRY = {
    'sydney': 'AU', 'melbourne': 'AU', 'brisbane': 'AU', 'perth': 'AU', 'adelaide': 'AU',
    'canberra': 'AU', 'darwin': 'AU', 'hobart': 'AU', 'gold coast': 'AU',
    'pyongyang': 'KP', 'seoul': 'KR', 'busan': 'KR', 'london': 'GB', 'manchester': 'GB',
    'tokyo': 'JP', 'osaka': 'JP', 'singapore': 'SG', 'new york': 'US',
    'san francisco': 'US', 'los angeles': 'US', 'chicago': 'US', 'seattle': 'US',
    'auckland': 'NZ', 'wellington': 'NZ', 'christchurch': 'NZ',
    'paris': 'FR', 'berlin': 'DE', 'frankfurt': 'DE', 'toronto': 'CA', 'vancouver': 'CA',
}


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip().casefold()
    if clean in ALIASES:
        return ALIASES[clean]
    if clean in CITY_TO_COUNTRY:
        return CITY_TO_COUNTRY[clean]
    # Korea alone is ambiguous. Fuzzy matching must never choose one Korea.
    if clean == 'korea':
        return None
    try:
        return pycountry.countries.lookup(clean).alpha_2
    except LookupError:
        return None


def declared_country(text: str, proposed: RequestInterpretation | None = None) -> CountryContext | None:
    """Accept only explicit current-location language or a standalone clarification.

    An LLM proposal must match an actual declaration and the ISO database. A bare
    model code, delivery destination, negation or historical mention grants nothing.
    """
    text = text.strip().rstrip('.!')
    patterns = (
        r"(?:i(?:'m| am|’m)\s+(?:currently\s+|now\s+)?(?:in|located in)|my current (?:location|country) is)\s+([^.!?;]+)",
        r"(?:i(?:'m| am|’m)\s+(?:travelling|traveling|commuting|riding|on vacation|on holiday|visiting|staying)\s+(?:(?:on|in)\s+[^.!?;]+\s+)?in)\s+([^.!?;]+)",
        r"(?:(?:travelling|traveling|commuting|on vacation|on holiday|visiting|staying|vacationing)\s+(?:(?:on|in)\s+[^.!?;]+\s+)?in)\s+([^.!?;]+)",
    )
    candidates = [m.group(1) for p in patterns for m in re.finditer(p, text, re.I)]
    if not candidates and (normalize_country(text) or text.casefold() == 'korea'):
        candidates = [text]
    if not candidates:
        if re.search(r"\b(?:i(?:'m| am|’m) (?:not|no longer)|my current (?:location|country))\b", text, re.I):
            return CountryContext(status='ambiguous')
        return None
    codes = set()
    ambiguous = False
    for candidate in candidates:
        if re.search(r'\b(?:or|but|not|no longer|maybe|possibly)\b', candidate, re.I):
            ambiguous = True
        # Split sentence boundaries and workflow instructions, not country names.
        candidate = re.split(r'[.!?;,]|\s+(?:and |please |block |replace |but |or )', candidate, maxsplit=1, flags=re.I)[0].strip()
        code = normalize_country(candidate)
        if not code:
            ambiguous = True
        else:
            codes.add(code)
    if ambiguous or len(codes) != 1:
        return CountryContext(status='ambiguous')
    code = next(iter(codes))
    if proposed and proposed.country_status == 'explicit':
        if normalize_country(proposed.declared_country) != code or normalize_country(proposed.proposed_country_code) != code:
            return CountryContext(status='ambiguous')
    return CountryContext(code=code, status='explicit')


class GeographicRules(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_id: Literal['POL-006'] = 'POL-006'
    version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    country_basis: Literal['declared_current_location'] = 'declared_current_location'
    excluded_country_codes: list[str]
    on_unknown_country: Literal['require_clarification'] = 'require_clarification'
    allow_security_card_block: Literal[True] = True

    @field_validator('excluded_country_codes')
    @classmethod
    def canonical_codes(cls, codes):
        if len(set(codes)) != len(codes) or any(normalize_country(code) != code for code in codes):
            raise ValueError('Exclusions require unique canonical ISO alpha-2 codes')
        return codes


class RulesSource(Protocol):
    def load(self) -> GeographicRules: ...


class FileRulesSource:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.getenv('POLICIES_DIR', 'policies'))

    def load(self):
        rules = GeographicRules.model_validate_json((self.directory / 'geographic-replacement-rules.json').read_text())
        if any(normalize_country(code) != code for code in rules.excluded_country_codes):
            raise ValueError('Rules require canonical ISO alpha-2 codes')
        return rules


class GeographicDecision(BaseModel):
    action: Literal['replace_card'] = 'replace_card'
    country_code: str | None = None
    evidence_source: str = 'customer_declaration'
    decision: Literal['allowed', 'denied', 'clarification_required', 'unavailable']
    policy_id: str = 'POL-006'
    version: str = ''
    artifact_hash: str = ''
    rule_id: str = 'declared_location_replacement'
    reason_code: str
    explanation: str
    evidence_citations: list[dict[str, str]] = Field(default_factory=list)


def evaluate_geography(context, rules: GeographicRules, evidence, pinned_version=None):
    base = {'country_code': None, 'version': rules.version}
    governing = [p for p in evidence if p.policy_id == rules.policy_id and p.mandatory]
    base['artifact_hash'] = hashlib.sha256((rules.model_dump_json() +
        ''.join(sorted({p.content_hash for p in governing}))).encode()).hexdigest()
    expected = rules.model_dump(exclude={'policy_id'})
    if (not governing or any(p.version != rules.version or p.conditions != expected for p in governing)
            or (pinned_version and pinned_version != rules.version)):
        return GeographicDecision(**base, decision='unavailable', reason_code='geographic_policy_conflict',
            explanation='Replacement policy changed or is unavailable. Please start a new request or contact support.')
    ctx = CountryContext.model_validate(context or {})
    base['country_code'] = ctx.code
    if ctx.status != 'explicit' or not ctx.code or normalize_country(ctx.code) != ctx.code:
        return GeographicDecision(**base, decision='clarification_required', reason_code='country_required',
            explanation='Which country should your replacement card be issued for? Your declaration is required before replacement can be reviewed. Card blocking remains available.')
    denied = ctx.code in rules.excluded_country_codes
    return GeographicDecision(**base, decision='denied' if denied else 'allowed',
        reason_code='excluded_declared_country' if denied else 'country_eligible',
        explanation=('Replacement is restricted from your declared location under our synthetic demonstration policy. '
                     'You can still block your card securely and contact specialist support.' if denied else
                     'Your declared country is eligible under the demonstration geographic policy; other replacement controls still apply.'))


def local_geographic_decision(context, source=None, pinned_version=None, document_source=None):
    """Cards boundary: approved local artifact and rules; no vector dependency."""
    from .adapters.document_source import FileSystemDocumentSource
    from .models import PolicyEvidence
    source = source or FileRulesSource()
    rules = source.load()
    document_source = document_source or FileSystemDocumentSource(getattr(source, 'directory', os.getenv('POLICIES_DIR','policies')))
    docs = [d for d in document_source.list_documents() if d.policy_id == 'POL-006']
    if len(docs) != 1:
        raise ValueError('Exactly one approved geographic policy required')
    doc = docs[0]
    evidence = [PolicyEvidence(policy_id=doc.policy_id, version=doc.version, title=doc.title,
        section_title='Mandatory Document Rules', content=doc.raw_content, source=doc.source,
        mandatory=True, conditions=doc.conditions, content_hash=doc.content_hash)]
    return evaluate_geography(context, rules, evidence, pinned_version)
