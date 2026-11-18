"""Model-grounded policy recommendations; never an authority to execute banking tools."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from card_chat.model_context import sanitise
from card_chat.policy.models import PendingCustomerQuestion, PolicyEvaluationResult

Action = Literal['none', 'offer_replacement', 'offer_unblock']


class SafePolicyFacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    has_blocked_card: StrictBool = False
    is_temporary_freeze: StrictBool = False
    is_permanently_compromised: StrictBool = False


class PolicyPassage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: str = Field(pattern=r'^evidence_[a-l]$')
    policy_id: str = Field(pattern=r'^POL-\d{3}$')
    version: str = Field(min_length=1, max_length=32, pattern=r'^[0-9A-Za-z_.-]+$')
    title: str = Field(max_length=300)
    section: str = Field(max_length=300)
    text: str = Field(min_length=1, max_length=4000)
    conditions: dict = Field(default_factory=dict)
    restrictions: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)


class RecommendationContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: Literal['after_block', 'restricted_issuance']
    facts: SafePolicyFacts
    allowed_recommendations: list[Action] = Field(min_length=1, max_length=3)
    passages: list[PolicyPassage] = Field(min_length=1, max_length=12)


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    evidence_id: str = Field(pattern=r'^evidence_[a-l]$')
    quote: str = Field(min_length=12, max_length=350)


class PolicyRecommendation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    action: Action
    message: str = Field(min_length=12, max_length=700)
    citations: list[EvidenceCitation] = Field(min_length=1, max_length=6)


class RecommendationUnavailable(ValueError):
    """Missing, unsafe or ungrounded model recommendation; no exception text is exposed."""


def verify_payload(payload):
    context = RecommendationContext.model_validate(payload)
    encoded = json.dumps(context.model_dump())
    if len(encoded.encode('utf-8')) > 40_000:
        raise RecommendationUnavailable('Policy context exceeds budget')
    from card_chat.pii.gateway import get_default_pii_gateway
    gateway = get_default_pii_gateway()
    if gateway:
        gateway.verify_outbound(encoded)
    return context


def redact_policy_value(value):
    """Export a redacted copy of reference text; never relax the outbound DLP gate.

    Conservative detectors can classify ordinary policy vocabulary as sensitive.
    Redact those spans too, along with any accidentally embedded private values.
    Exact citation matching is against these model-visible, redacted passages.
    """
    if isinstance(value, dict):
        return {key: redact_policy_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_policy_value(item) for item in value]
    if not isinstance(value, str):
        return value
    from card_chat.pii.gateway import get_default_pii_gateway
    gateway = get_default_pii_gateway()
    if gateway:
        sensitive = gateway.SECRET_ENTITIES | gateway.REVERSIBLE_ENTITIES | gateway.IRREVERSIBLE_ENTITIES
        spans = gateway.analyzer.analyze(text=value, language='en', score_threshold=gateway.score_threshold)
        merged = []
        for span in sorted((s for s in spans if s.entity_type in sensitive), key=lambda s: s.start):
            if merged and span.start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], span.end))
            else:
                merged.append((span.start, span.end))
        for start, end in reversed(merged):
            value = value[:start] + '[redacted]' + value[end:]
    return value


class PolicyNextStepPlanner:
    def __init__(self, model, timeout=35):
        self.model, self.timeout = model, timeout

    async def recommend(self, evaluation: PolicyEvaluationResult, facts: dict, stage: str,
                        allowed_tools: dict) -> PolicyEvaluationResult:
        if evaluation.policy_error:
            return evaluation
        if not evaluation.retrieved_policies:
            raise RecommendationUnavailable('Approved evidence is required')
        safe = SafePolicyFacts(**{key: facts.get(key, False) is True
                                  for key in SafePolicyFacts.model_fields})
        actions = ['none']
        # These are capability and banking-state gates, not proactive-offer rules.
        # Policy conditions and passages are interpreted by the model below.
        if stage == 'after_block' and safe.has_blocked_card and 'replace_card' in allowed_tools:
            actions.append('offer_replacement')
        if (stage == 'restricted_issuance' and safe.has_blocked_card and safe.is_temporary_freeze
                and not safe.is_permanently_compromised and 'unblock_card' in allowed_tools):
            actions.append('offer_unblock')
        evidence = evaluation.retrieved_policies
        # Mandatory sources must not be evicted by semantic result ordering.
        evidence = sorted(evidence, key=lambda item: not item.get('mandatory', False))[:12]
        passages = []
        originals = {}
        for index, item in enumerate(evidence):
            if evaluation.pinned_versions.get(item['policy_id']) != item['version']:
                raise RecommendationUnavailable('Evidence version is not pinned')
            identifier = 'evidence_' + chr(ord('a') + index)
            originals[identifier] = item
            passages.append(PolicyPassage(evidence_id=identifier, policy_id=item['policy_id'],
                version=item['version'], title=redact_policy_value(item['title']), section=redact_policy_value(item['section_title']),
                text=redact_policy_value(item['content'][:4000]), conditions=redact_policy_value(item.get('conditions', {})),
                restrictions=redact_policy_value(item.get('restrictions', [])),
                required_actions=redact_policy_value(item.get('required_actions', []))))
        context = verify_payload(RecommendationContext(stage=stage, facts=safe,
            allowed_recommendations=actions, passages=passages).model_dump())
        method = getattr(self.model, 'recommend_policy', None)
        if not callable(method):
            raise RecommendationUnavailable('Model does not support policy recommendations')
        raw = await asyncio.wait_for(method(context.model_dump()), timeout=self.timeout)
        recommendation = PolicyRecommendation.model_validate(raw)
        if recommendation.action not in actions:
            raise RecommendationUnavailable('Proposed step is not authorized or eligible')
        citations = []
        cited_ids = set()
        for citation in recommendation.citations:
            passage = next((p for p in passages if p.evidence_id == citation.evidence_id), None)
            if passage is None or ' '.join(citation.quote.split()) not in ' '.join(passage.text.split()):
                raise RecommendationUnavailable('Citation does not quote retrieved evidence')
            source = originals[citation.evidence_id]
            cited_ids.add(source['policy_id'])
            citations.append({key: source[key] for key in ('policy_id', 'version', 'source', 'section_title')}
                             | {'quote': citation.quote})
        required = 'POL-003' if stage == 'after_block' else 'POL-001'
        if required not in cited_ids:
            raise RecommendationUnavailable('Applicable policy must be cited')
        text = recommendation.message
        # Citations retain exact IDs in the checkpoint; customer-facing wording
        # uses names so the existing conservative PIN scrubber stays unchanged.
        for identifier in cited_ids:
            text = text.replace(identifier, 'banking policy ' + str(int(identifier[4:])))
        if sanitise(text) != text or re.search(
                r'\b(?:I|we) (?:have |already )?(?:issued|ordered|replaced|unblocked|unfrozen)\b', text, re.I):
            raise RecommendationUnavailable('Unsafe recommendation text')
        from card_chat.pii.gateway import get_default_pii_gateway
        gateway = get_default_pii_gateway()
        if gateway:
            gateway.verify_outbound(text)
        if recommendation.action == 'offer_replacement' and safe.is_temporary_freeze:
            governing = next(p for p in passages if p.policy_id == 'POL-003' and
                             originals[p.evidence_id].get('mandatory'))
            if governing.conditions.get('offer_on_temporary_freeze') is not True:
                raise RecommendationUnavailable('Offer conflicts with an explicit policy prohibition')
        pending = None
        if recommendation.action != 'none':
            if not text.rstrip().endswith('?'):
                raise RecommendationUnavailable('Offer must ask for customer acceptance')
            replacing = recommendation.action == 'offer_replacement'
            blocked = facts.get('blocked_cards') or [{}]
            target = (facts.get('blocked_card_id') or facts.get('last_blocked_card_id') or
                      blocked[0].get('id') or blocked[0].get('card_id'))
            pending = PendingCustomerQuestion(
                question_type='replacement_offer' if replacing else 'unblock_offer',
                question_text=text, target_card_id=target,
                target_account_number=facts.get('account_number') or blocked[0].get('account_number'),
                policy_id=required, policy_version=evaluation.pinned_versions[required],
                yes_intent='replace_card' if replacing else 'unblock_card',
                no_intent='complete' if replacing else 'cancel',
                subsequent_intent=None if replacing else 'issue_card')
        mode = getattr(self.model, 'policy_recommendation_mode', 'model')
        usage = {key: value for key, value in getattr(self.model, 'last_usage', {}).items()
                 if key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
                 and type(value) is int and value >= 0}
        usage['model_calls'] = 0 if mode == 'stub' else 1
        return evaluation.model_copy(update={
            'pending_question': pending,
            'decision_reason': text,
            'recommendation': {'action': recommendation.action, 'message': text, 'citations': citations,
                               'mode': mode, 'stage': stage, 'usage': usage},
        })
