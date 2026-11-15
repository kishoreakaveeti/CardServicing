"""One concrete agent delegates language understanding and action selection."""
from card_chat.provider import Model
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.model_context import build_context


class CardServicingAgent:
    def __init__(self, model: Model):
        self.model = model

    async def interpret_turn(self, history: list, collected: dict,
                             allowed_tools: dict) -> CustomerReply | ProposedToolCall:
        history, collected, allowed_tools = build_context(history, collected, allowed_tools)
        turn = await self.model.turn(history, collected, allowed_tools)
        if isinstance(turn, ProposedToolCall):
            if turn.name not in allowed_tools:
                raise ValueError('Model proposed an unavailable tool')
            if not isinstance(turn.arguments, dict):
                raise ValueError('Tool arguments must be an object')
        elif not isinstance(turn, CustomerReply) or not turn.text.strip():
            raise ValueError('Model returned an invalid conversation turn')
        return turn
