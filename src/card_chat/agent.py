"""One stateless conversation agent; the workflow owns authority and execution."""
from copy import deepcopy
from dataclasses import dataclass, field

from card_chat.provider import Model

QUESTIONS = {'given_name': 'What is your given name?',
             'user_name': 'What is your user name?',
             'account_number': 'What is your existing account number? Please include any leading zeros.'}


@dataclass(frozen=True)
class CustomerReply:
    text: str
    supplied_fields: dict[str, str] = field(default_factory=dict)
    requests_review: bool = False


@dataclass(frozen=True)
class ProposedToolCall:
    name: str
    arguments: dict


class CardServicingAgent:
    def __init__(self, model: Model):
        self.model = model

    async def interpret_turn(self, history: list, draft: dict,
                             allowed_tools: dict[str, dict]) -> CustomerReply | ProposedToolCall:
        """Interpret supplied values or propose an allowed tool; never perform a write.

        The workflow supplies no tools during collection and create_card only after
        confirmation. Copies keep the model adapter away from workflow-owned state.
        """
        current = deepcopy(draft)
        if allowed_tools:
            if set(allowed_tools) != {'create_card'}:
                raise ValueError('Only create_card is supported')
            name, arguments = await self.model.propose(current, deepcopy(allowed_tools['create_card']))
            if name not in allowed_tools:
                raise ValueError('Model proposed a tool that is not allowed this turn')
            return ProposedToolCall(name, arguments)

        extraction = await self.model.extract(deepcopy(history), current)
        if extraction.intent == 'unsupported':
            return CustomerReply('This demo currently supports creating a new card for an existing account. Would you like to do that?')
        if extraction.intent == 'servicing':
            return CustomerReply('Would you like to create a new card for an existing account?')
        if extraction.intent != 'create':
            return CustomerReply('I can help create a new simulated card for an existing account. Would you like to start?')
        candidate = {**current, **extraction.fields}
        question = next((question for key, question in QUESTIONS.items() if key not in candidate),
                        'Please review your details below.')
        return CustomerReply(question, extraction.fields, set(QUESTIONS).issubset(candidate))
