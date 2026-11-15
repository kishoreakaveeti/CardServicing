"""Conversation-scoped private bindings and catalogue-driven structured forms."""
import secrets
import string
from fastapi import HTTPException
from card_chat.model_context import sanitise, REFERENCE


def eligible(catalogue, caller, allowed_names=None):
    permissions = set(caller.get('permissions', []))
    return {name: spec for name, spec in catalogue.items()
            if set(spec.entry.permissions) <= permissions
            and (allowed_names is None or name in allowed_names)}


def bind(bindings, cid, owner, tool, field, value):
    for ref, item in bindings.items():
        if item == {'cid': cid, 'owner': owner, 'tool': tool, 'field': field, 'value': value}:
            return ref
    ref = 'selected_' + ('account' if 'account' in field else 'input') + '_' + ''.join(
        secrets.choice(string.ascii_lowercase) for _ in range(28))
    bindings[ref] = {'cid': cid, 'owner': owner, 'tool': tool, 'field': field, 'value': value}
    return ref


def resolve(spec, arguments, bindings, cid, owner):
    result = {}
    for field, value in arguments.items():
        if field not in spec.input_schema['properties']:
            raise ValueError('Unknown argument')
        if field in spec.entry.context_fields:
            if isinstance(value, str) and sanitise(value) != value:
                raise ValueError('Private input must use the form')
            result[field] = value
        else:
            item = bindings.get(value) if isinstance(value, str) else None
            if (not item or not REFERENCE.fullmatch(value) or item['cid'] != cid or item['owner'] != owner
                    or item['tool'] != spec.name or item['field'] != field):
                raise ValueError('Reference is not bound to this conversation and field')
            result[field] = item['value']
    return spec.validate(result, partial=True)


def authorise(spec, arguments, caller):
    if not set(spec.entry.permissions) <= set(caller.get('permissions', [])):
        raise HTTPException(403, 'Action is not permitted')
    for field, source in spec.entry.trusted_fields.items():
        if field not in arguments:
            continue
        if source == 'account' and arguments[field] not in caller.get('accounts', []):
            raise HTTPException(403, 'Selection is no longer authorized')
        if source == 'username' and arguments[field] != caller.get('username'):
            raise HTTPException(403, 'Customer does not match this action')


def mask(value):
    text = str(value)
    return '*' * max(4, len(text) - 4) + (text[-4:] if len(text) > 4 else '')


def form_options(catalogue, caller, bindings, cid, owner):
    tools = []
    for name, spec in sorted(catalogue.items()):
        fields = []
        for field, prop in spec.input_schema['properties'].items():
            source = spec.entry.trusted_fields.get(field)
            if source == 'username':
                continue
            item = {'name': field, 'label': spec.entry.field_labels.get(field, prop.get('title', field)),
                    'type': prop['type'], 'required': field in spec.input_schema.get('required', [])}
            if source == 'account':
                item['options'] = [{'ref': bind(bindings, cid, owner, name, field, value),
                                    'label': 'Account ' + mask(value)} for value in caller.get('accounts', [])]
            fields.append(item)
        tools.append({'name': name, 'label': spec.entry.label, 'fields': fields})
    return {'tools': tools}
