"""Multi-agent orchestration and specialized agents for card servicing and FinCrime."""
from card_chat.provider import Model
from card_chat.turns import CustomerReply, ProposedToolCall
from card_chat.model_context import build_context


class CardServicingAgent:
    """Specialized agent handling account lifecycle, card viewing, and new issuance."""
    def __init__(self, model: Model):
        self.model = model
        self.name = "cards_agent"

    async def interpret_turn(self, history: list, collected: dict,
                             allowed_tools: dict) -> CustomerReply | ProposedToolCall:
        history, collected, tools = build_context(history, collected, allowed_tools)
        turn = await self.model.turn(history, collected, tools)
        if isinstance(turn, ProposedToolCall):
            if turn.name not in tools:
                raise ValueError('Model proposed an unavailable tool')
            if not isinstance(turn.arguments, dict):
                raise ValueError('Tool arguments must be an object')
        elif not isinstance(turn, CustomerReply) or not turn.text.strip():
            raise ValueError('Model returned an invalid conversation turn')
        return turn


class FinCrimeAgent:
    """Specialized agent handling financial crime protection, risk checks, and card blocking."""
    def __init__(self, model: Model):
        self.model = model
        self.name = "fincrime_agent"

    async def interpret_turn(self, history: list, collected: dict,
                             allowed_tools: dict) -> CustomerReply | ProposedToolCall:
        history, collected, tools = build_context(history, collected, allowed_tools)
        turn = await self.model.turn(history, collected, tools)
        if isinstance(turn, ProposedToolCall):
            if turn.name not in tools:
                raise ValueError('FinCrime agent proposed an unavailable tool')
            if not isinstance(turn.arguments, dict):
                raise ValueError('Tool arguments must be an object')
        elif not isinstance(turn, CustomerReply) or not turn.text.strip():
            raise ValueError('FinCrime agent returned an invalid conversation turn')
        return turn


class AgentOrchestrator:
    """Orchestrates intent routing to specialized agents and enforces cross-agent policies."""
    def __init__(self, model: Model, cards_agent: CardServicingAgent | None = None,
                 fincrime_agent: FinCrimeAgent | None = None,
                 check_blocked_fn=None,
                 agent_tools_policy: dict | None = None):
        self.model = model
        self.cards_agent = cards_agent or CardServicingAgent(model)
        self.fincrime_agent = fincrime_agent or FinCrimeAgent(model)
        self.check_blocked_fn = check_blocked_fn
        self.agent_tools_policy = agent_tools_policy

    def route(self, history: list, collected: dict, allowed_tools: dict | None = None) -> str:
        """Route to appropriate specialized agent based on context and customer intent."""
        # If only FinCrime tools are available in the current context, route to fincrime
        if allowed_tools:
            non_flow = set(allowed_tools.keys()) - {'logout', 'login', 'register'}
            if non_flow and non_flow.issubset({'block_card', 'unblock_card'}):
                return 'fincrime'
            if non_flow and not any(t in non_flow for t in ('block_card', 'unblock_card')):
                return 'cards'

        # Analyze latest customer message first to detect intent changes
        text = next((t['content'].lower() for t in reversed(history) if t.get('role') == 'user'), '')
        if any(w in text for w in ('unblock', 'un-block', 'unfreeze', 'un-freeze', 'reactivate', 'restore')):
            return 'fincrime'
        if any(w in text for w in ('block', 'freeze', 'stop card', 'lost', 'stolen', 'fraud', 'compromised')):
            return 'fincrime'

        if collected.get('tool') in ('block_card', 'unblock_card'):
            return 'fincrime'
        if collected.get('tool') in ('create_card', 'issue_card', 'create_account', 'view_accounts', 'view_cards'):
            return 'cards'

        return 'cards'

    async def interpret_turn(self, history: list, collected: dict,
                             allowed_tools: dict) -> CustomerReply | ProposedToolCall:
        target = self.route(history, collected, allowed_tools)
        agent = self.fincrime_agent if target == 'fincrime' else self.cards_agent

        policy = self.agent_tools_policy
        if policy is None:
            try:
                from card_chat.mcp_server import get_agent_tools_policy
                policy = get_agent_tools_policy()
            except Exception:
                policy = None

        if policy and agent.name in policy:
            agent_allowed_names = set(policy[agent.name])
            agent_allowed_names.update(['login', 'register', 'logout'])
            all_configured = set().union(*policy.values())
            if agent.name == 'cards_agent':
                for tool_name in allowed_tools:
                    if tool_name not in all_configured:
                        agent_allowed_names.add(tool_name)
            filtered_tools = {k: v for k, v in allowed_tools.items() if k in agent_allowed_names}
        else:
            filtered_tools = allowed_tools

        turn = await agent.interpret_turn(history, collected, filtered_tools)

        # Cross-agent dependency check:
        # If Cards agent proposes to issue/create a card, check if the account already has a blocked card
        if isinstance(turn, ProposedToolCall) and turn.name in ('issue_card', 'create_card'):
            account_num = turn.arguments.get('account_number') or collected.get('arguments', {}).get('account_number')
            if account_num and self.check_blocked_fn:
                is_blocked = await self.check_blocked_fn(account_num)
                if is_blocked:
                    return CustomerReply(
                        'Cannot issue a new card for this account because it has an active blocked card. '
                        'Under banking security rules, the blocked card must be unblocked or resolved first.'
                    )
        return turn
