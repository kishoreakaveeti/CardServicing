"""The only model-bound projection. Execution state is never serialised wholesale."""
from copy import deepcopy
import re

REFERENCE = re.compile(r'^selected_[a-z_]+_[a-z]{24,40}$')
PRIVATE_PASTE = '[Private input omitted. Use the secure action form.]'
PRIVATE_NOTICE = 'Please use the secure action form and masked account picker for private details. They are not sent to the language model.'
# Conservative fallback, not a general-purpose DLP guarantee. Structured input
# and deny-by-default field projections are the primary privacy boundary.
PRIVATE_TEXT = re.compile(
    r'\d(?:[\s-]?\d){5,}|(?<!ending in )(?<!ending with )\b\d{3,4}\b|sk-ant-\S+|\bBearer\s+\S+|'
    r'\b(?:cvv|cvc|pin|password|secret|credential|api[_ -]?key|access[_ -]?token)\b|'
    r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|'
    r'\bmy name is\b|'
    r'(?:account[_ -]?(?:number|id)|card[_ -]?(?:number|id)|customer[_ -]?(?:number|id)|'
    r'user[_ -]?(?:name|id)|given[_ -]?name|phone|ssn)\s*(?:[\":=]|is\b)',
    re.IGNORECASE)


def sanitise(text, private_values=()):
    if not isinstance(text, str):
        return PRIVATE_PASTE
    if PRIVATE_TEXT.search(text):
        return PRIVATE_PASTE
    for value in sorted((str(v) for v in private_values if v is not None), key=len, reverse=True):
        if value:
            text = re.sub(r'(?<!\w)' + re.escape(value) + r'(?!\w)', '[private detail]', text, flags=re.IGNORECASE)
    return text


def tools_for_model(catalogue):
    """Stable schemas: private arguments are reference strings, never raw values."""
    definitions = {}
    for name, spec in sorted(catalogue.items()):
        definition = spec.definition()
        definition['description'] = sanitise(definition['description'])[:2000]
        properties = {}
        for field, prop in spec.input_schema['properties'].items():
            if field in spec.entry.context_fields:
                # No examples, defaults or arbitrary annotations enter prompts.
                properties[field] = {k: deepcopy(v) for k, v in prop.items()
                                     if k in ('type', 'minLength', 'maxLength', 'minimum', 'maximum')}
                properties[field]['x-model-input'] = 'safe'
            else:
                properties[field] = {'type': 'string', 'pattern': REFERENCE.pattern,
                    'description': 'Use the exact opaque reference supplied in verified draft facts. If absent, ask the customer to use the secure action form.',
                    'x-model-input': 'reference'}
        definition['input_schema'] = {'type': 'object', 'properties': properties,
            # An intent proposal may omit unknown inputs. Only the original
            # execution schema can authorize a complete reviewed submission.
            'required': [], 'additionalProperties': False}
        definitions[name] = definition
    return definitions


def build_context(history, draft, tools, *, private_values=(), recent=8):
    """Allowlist roles, facts and schema keywords. Drop tool results entirely.

    The adapter applies this again at its boundary, including for direct calls.
    Old/ambiguous references are handled by the workflow, not invented summaries.
    """
    definitions = {}
    for name, tool in sorted(tools.items()):
        schema = tool.get('input_schema', {})
        props = {}
        for key, prop in schema.get('properties', {}).items():
            if prop.get('x-model-input') == 'safe':
                props[key] = {k: deepcopy(v) for k, v in prop.items()
                              if k in ('type', 'minLength', 'maxLength', 'minimum', 'maximum', 'x-model-input')}
            else:
                props[key] = {'type': 'string', 'pattern': REFERENCE.pattern,
                             'description': 'Opaque reference from verified draft facts; never provide private values.',
                             'x-model-input': 'reference'}
        definitions[name] = {'name': name, 'description': sanitise(tool.get('description', ''))[:2000],
                            'input_schema': {'type': 'object', 'properties': props,
                                'required': [k for k in schema.get('required', []) if k in props],
                                'additionalProperties': False}}
    turns = [{'role': turn['role'], 'content': sanitise(turn['content'], private_values),
              'kind': 'model' if turn['role'] == 'assistant' else 'chat'}
             for turn in history if isinstance(turn, dict) and turn.get('role') in ('user', 'assistant')
             and (turn['role'] != 'assistant' or turn.get('kind') == 'model')
             and isinstance(turn.get('content'), str)]
    facts = {}
    name = draft.get('tool') if isinstance(draft, dict) else None
    if name in definitions:
        arguments = {}
        for key, value in draft.get('arguments', {}).items():
            prop = definitions[name]['input_schema']['properties'].get(key, {})
            if prop.get('x-model-input') == 'reference':
                if isinstance(value, str) and REFERENCE.fullmatch(value):
                    arguments[key] = value
            elif prop.get('x-model-input') == 'safe' and isinstance(value, (str, int, float, bool)):
                if len(str(value)) <= 2000 and sanitise(str(value), private_values) == str(value):
                    arguments[key] = value
        facts = {'tool': name, 'arguments': arguments}
    turns = turns[-recent:]
    latest_user = max((index for index, turn in enumerate(turns) if turn['role'] == 'user'), default=-1)
    for index, turn in enumerate(turns):
        turn['content'] = turn['content'][:4000 if index == latest_user else 2000]
    return turns, facts, definitions


def ambiguous_old_reference(text):
    return bool(re.search(r'\b(?:first|previous|earlier|old|original|1st) (?:one|account|card|details|selection)\b|'
                          r'\b(?:one|account|card|details|selection)\b.{0,40}\b(?:from before|from earlier|previously|last time|before)\b', text, re.I))
