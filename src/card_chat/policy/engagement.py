"""Deterministic customer engagement policy evaluation.

Separates communication style and promotional eligibility from financial security controls.
Security workflows always take precedence over marketing engagement.
"""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SentimentType = Literal['upset', 'worried', 'neutral', 'happy', 'delighted']
UrgencyType = Literal['normal', 'elevated', 'high']
CommunicationStyle = Literal['concise_and_apologetic', 'calm_and_reassuring', 'standard', 'warm_and_helpful']


class SentimentAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sentiment: SentimentType = 'neutral'
    urgency: UrgencyType = 'normal'
    reasons: list[str] = Field(default_factory=list)


def classify_sentiment_and_urgency(text: str) -> SentimentAnalysis:
    """Deterministic sentiment and urgency classifier."""
    lower = text.lower()
    
    # 1. Upset indicators
    upset_patterns = [
        r'\b(?:fed up|terrible|horrible|furious|annoyed|frustrated|ridiculous|disaster|worst bank|hate this|appalling|angry|sick of)\b',
        r'\b(?:just block it|unacceptable|useless|incompetent)\b'
    ]
    if any(re.search(p, lower) for p in upset_patterns):
        sentiment = 'upset'
    # 2. Delighted / Happy indicators
    elif any(re.search(p, lower) for p in [
        r'\b(?:fantastic|delighted|superb|brilliant|amazing|wonderful|exceptional)\b',
        r'\breally happy\b', r'\bthrilled\b'
    ]):
        sentiment = 'delighted'
    elif any(re.search(p, lower) for p in [
        r'\b(?:thank you|thanks|great|awesome|good service|appreciate|helpful)\b',
        r'\bhappy\b'
    ]):
        sentiment = 'happy'
    # 3. Worried indicators
    elif any(re.search(p, lower) for p in [
        r'\b(?:worried|worrying|anxious|scared|nervous|concerned|panic|afraid|hectic)\b',
        r'\b(?:withdraw money|misuse|unauthorised|unauthorized|is he really right|trust my friend)\b'
    ]):
        sentiment = 'worried'
    else:
        sentiment = 'neutral'
        
    # Urgency classification
    if any(re.search(p, lower) for p in [
        r'\b(?:right now|immediately|asap|emergency|urgent|stolen|robbed)\b'
    ]):
        urgency = 'high'
    elif any(re.search(p, lower) for p in [
        r'\b(?:in a hurry|hurry|hectic|worried|delay|delayed|lost my card|lost debit card)\b'
    ]):
        urgency = 'elevated'
    else:
        urgency = 'normal'

    return SentimentAnalysis(sentiment=sentiment, urgency=urgency)


class EngagementDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_id: str
    sentiment: SentimentType
    urgency: UrgencyType
    communication_style: CommunicationStyle
    promotion_permitted: bool
    engagement_action: str | None = None
    suggested_intro: str | None = None


def evaluate_engagement_policy(
    sentiment: SentimentType,
    urgency: UrgencyType,
    *,
    is_security_workflow: bool = False,
    workflow_completed: bool = False,
    marketing_consent: bool = True,
    previous_promoted: bool = False
) -> EngagementDecision:
    """Evaluates engagement rules deterministically based on customer sentiment and workflow state.
    
    Policies:
    - CUSTOMER-ENGAGEMENT-001: Upset customer -> concise, apologize, strictly no promotion.
    - CUSTOMER-ENGAGEMENT-002: Happy/Delighted customer -> warm, optional product exploration after completion.
    - CUSTOMER-ENGAGEMENT-003: Worried customer -> calm, reassuring, no promotion during security flow.
    - CUSTOMER-ENGAGEMENT-004: Neutral customer -> standard clear assistance.
    """
    # Policy 1: Upset customer
    if sentiment == 'upset':
        return EngagementDecision(
            policy_id='CUSTOMER-ENGAGEMENT-001',
            sentiment='upset',
            urgency=urgency,
            communication_style='concise_and_apologetic',
            promotion_permitted=False,
            engagement_action=None,
            suggested_intro="I'm sorry this has been frustrating. I can help block your card now."
        )

    # Policy 3: Worried customer
    if sentiment == 'worried':
        return EngagementDecision(
            policy_id='CUSTOMER-ENGAGEMENT-003',
            sentiment='worried',
            urgency=urgency,
            communication_style='calm_and_reassuring',
            promotion_permitted=False,
            engagement_action=None,
            suggested_intro="Someone finding your card could potentially misuse it. I can help you secure it now."
        )

    # Policy 2: Delighted / Happy customer
    if sentiment in ('happy', 'delighted'):
        # Only offer product exploration AFTER successful workflow completion, and not if already shown
        offer_permitted = workflow_completed and marketing_consent and not previous_promoted
        action = 'explore_neo_bank_products' if offer_permitted else None
        return EngagementDecision(
            policy_id='CUSTOMER-ENGAGEMENT-002',
            sentiment=sentiment,
            urgency=urgency,
            communication_style='warm_and_helpful',
            promotion_permitted=offer_permitted,
            engagement_action=action,
            suggested_intro="You're very welcome! I'm glad I could help." if workflow_completed else None
        )

    # Policy 4: Neutral customer
    return EngagementDecision(
        policy_id='CUSTOMER-ENGAGEMENT-004',
        sentiment='neutral',
        urgency=urgency,
        communication_style='standard',
        promotion_permitted=False,
        engagement_action=None
    )
