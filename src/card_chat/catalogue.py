"""Compile a small, trusted tool catalogue against a local OpenAPI contract."""
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Literal

from jsonschema import Draft202012Validator, FormatChecker, SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CatalogueError(ValueError):
    pass


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    operation_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confirmation: Literal['required', 'none']
    label: str | None = Field(default=None, min_length=1)
    confirmation_label: str | None = None
    field_labels: dict[str, str] = Field(default_factory=dict)
    result_labels: dict[str, str] = Field(default_factory=dict)
    sensitive_fields: list[str] = Field(default_factory=list)
    idempotency_header: Literal['Idempotency-Key'] | None = None
    context_fields: list[str] = Field(default_factory=list)
    trusted_fields: dict[str, Literal['account', 'username']] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=lambda: ['tools:execute'])


class Catalogue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    openapi: str
    tools: list[Entry] = Field(min_length=1)


def resolve(node, document, seen=()):
    if isinstance(node, list):
        return [resolve(value, document, seen) for value in node]
    if not isinstance(node, dict):
        return node
    if '$ref' in node:
        reference = node['$ref']
        if not reference.startswith('#/') or reference in seen:
            raise CatalogueError('Only nonrecursive local OpenAPI references are supported')
        target = document
        try:
            for token in reference[2:].split('/'):
                target = target[token.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            raise CatalogueError('OpenAPI contains an unresolved local reference') from None
        return resolve({**target, **{k: v for k, v in node.items() if k != '$ref'}}, document, (*seen, reference))
    return {key: resolve(value, document, seen) for key, value in node.items()}


def flat_object(schema):
    if schema.get('type') != 'object' or 'properties' not in schema:
        raise CatalogueError('JSON input must be a flat object with properties')
    for key in schema:
        if key not in {'type', 'properties', 'required', 'additionalProperties', 'title', 'description', 'examples'}:
            raise CatalogueError('Unsupported object schema keyword: ' + key)
    for name, prop in schema['properties'].items():
        if name.startswith('_') or prop.get('type') not in ('string', 'integer', 'number', 'boolean'):
            raise CatalogueError('Input properties must be named scalar fields: ' + name)
        supported = {'type', 'title', 'description', 'examples', 'default', 'enum', 'const', 'format',
                     'minLength', 'maxLength', 'pattern', 'minimum', 'maximum', 'exclusiveMinimum',
                     'exclusiveMaximum', 'multipleOf', 'x-sensitive'}
        if set(prop) - supported:
            raise CatalogueError('Unsupported scalar schema shape for field: ' + name)
        if prop.get('format') and prop['format'] not in FormatChecker.checkers:
            raise CatalogueError('Unsupported scalar format for field: ' + name)
    if not set(schema.get('required', [])) <= set(schema['properties']):
        raise CatalogueError('Required input fields must exist in properties')
    schema['additionalProperties'] = False
    Draft202012Validator.check_schema(schema)
    return schema


@dataclass
class ToolSpec:
    entry: Entry
    method: str
    path: str
    input_schema: dict
    path_fields: set[str]
    body_fields: set[str] | None
    responses: dict[int, dict]

    @property
    def name(self):
        return self.entry.name

    def definition(self):
        return {'name': self.name, 'description': self.entry.description,
                'input_schema': deepcopy(self.input_schema)}

    def validate(self, arguments, *, partial=False):
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be an object')
        values = {key: value.strip() if isinstance(value, str) else value for key, value in arguments.items()}
        for key, value in values.items():
            if isinstance(value, str) and any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('Control characters are not allowed')
            if key in self.path_fields and (not isinstance(value, str) or value in ('.', '..')):
                raise ValueError('Path identifiers must be strings without dot segments')
        schema = deepcopy(self.input_schema)
        if partial:
            schema.pop('required', None)
        if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(values):
            raise ValueError('Arguments do not match the tool schema')
        return values

    def missing(self, arguments):
        return [key for key in self.input_schema.get('required', []) if key not in arguments]

    def masked(self, arguments):
        sensitive = set(self.entry.sensitive_fields)
        result = {}
        for key, value in arguments.items():
            prop = self.input_schema['properties'][key]
            credential = any(word in key.lower() for word in ('cvv', 'cvc', 'pin', 'password', 'secret', 'credential', 'token'))
            if credential:
                value = '[private]'
            elif key in sensitive or prop.get('x-sensitive') or any(word in key.lower() for word in ('account', 'card_number', 'user_name')):
                text = str(value)
                value = '*' * max(4, len(text) - 4) + (text[-4:] if len(text) > 4 else '')
            result[self.entry.field_labels.get(key, prop.get('title', key))] = value
        return result

    @property
    def safe_retry(self):
        return self.method == 'GET' or self.entry.idempotency_header is not None


def compile_entry(entry, document):
    matches = [(method.upper(), path, item, operation)
               for path, item in document.get('paths', {}).items()
               for method, operation in item.items()
               if isinstance(operation, dict) and operation.get('operationId') == entry.operation_id]
    if len(matches) != 1:
        raise CatalogueError(f'{entry.name}: operation_id must identify exactly one OpenAPI operation')
    method, path, item, operation = matches[0]
    entry = entry.model_copy(update={'label': entry.label or operation.get('summary') or entry.name})
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'} or not path.startswith('/') or path.startswith('//'):
        raise CatalogueError(entry.name + ': unsupported HTTP method/path')
    if any(segment in ('.', '..') for segment in path.split('/')) or '?' in path or '#' in path:
        raise CatalogueError(entry.name + ': unsafe operation path')
    if '{' in re.sub(r'\{[^{}]+\}', '', path) or '}' in re.sub(r'\{[^{}]+\}', '', path):
        raise CatalogueError(entry.name + ': malformed path placeholder')
    if method != 'GET' and entry.confirmation != 'required':
        raise CatalogueError(entry.name + ': writes require explicit confirmation')
    if document.get('security') or operation.get('security'):
        raise CatalogueError(entry.name + ': operation security schemes need an execution adapter')
    properties, required, path_fields = {}, [], set()
    headers = []
    for parameter in item.get('parameters', []) + operation.get('parameters', []):
        param = resolve(parameter, document)
        if param.get('in') == 'header' and param.get('name', '').lower() == 'idempotency-key':
            headers.append(param)
            continue
        if param.get('in') != 'path' or not param.get('required') or param.get('schema', {}).get('type') != 'string':
            raise CatalogueError(entry.name + ': only required string path parameters and Idempotency-Key are supported')
        name = param['name']
        if name in properties:
            raise CatalogueError(entry.name + ': duplicate parameter')
        properties[name] = resolve(param['schema'], document)
        if param.get('description'):
            properties[name].setdefault('description', param['description'])
        required.append(name)
        path_fields.add(name)
    if set(re.findall(r'\{([^{}]+)\}', path)) != path_fields:
        raise CatalogueError(entry.name + ': path placeholders do not match parameters')
    if bool(headers) != bool(entry.idempotency_header) or len(headers) > 1:
        raise CatalogueError(entry.name + ': declare idempotency_header exactly when OpenAPI declares Idempotency-Key')
    if headers and headers[0].get('schema', {}).get('type') != 'string':
        raise CatalogueError(entry.name + ': Idempotency-Key must be a string header')
    body_fields = None
    if 'requestBody' in operation:
        body = resolve(operation['requestBody'], document)
        if method == 'GET' or not body.get('required') or set(body.get('content', {})) != {'application/json'}:
            raise CatalogueError(entry.name + ': only required application/json request bodies are supported')
        schema = flat_object(body['content']['application/json']['schema'])
        body_fields = set(schema['properties'])
        if body_fields & path_fields:
            raise CatalogueError(entry.name + ': body/path field names must not collide')
        properties.update(schema['properties'])
        required.extend(schema.get('required', []))
    schema = flat_object({'type': 'object', 'properties': properties, 'required': required})
    if (set(entry.context_fields) | set(entry.trusted_fields)) - set(properties):
        raise CatalogueError(entry.name + ': context/trusted fields must name declared inputs')
    if set(entry.context_fields) & (set(entry.trusted_fields) | set(entry.sensitive_fields)):
        raise CatalogueError(entry.name + ': private fields cannot be model-visible')
    if any(properties[k].get('x-sensitive') or k.lower().endswith('_id') or any(word in k.lower() for word in
            ('account', 'card', 'pin', 'cvv', 'cvc', 'password', 'secret', 'token', 'user', 'name', 'credential',
             'customer', 'email', 'phone', 'identifier', 'ssn', 'pan'))
           for k in entry.context_fields):
        raise CatalogueError(entry.name + ': identifiers and credentials cannot be model-visible')
    responses = {}
    for code, response in operation.get('responses', {}).items():
        if str(code).isdigit() and 200 <= int(code) < 300:
            response = resolve(response, document)
            content = response.get('content', {})
            if set(content) != {'application/json'} or content['application/json']['schema'].get('type') != 'object':
                raise CatalogueError(entry.name + ': successful responses must be JSON objects')
            responses[int(code)] = content['application/json']['schema']
    if not responses:
        raise CatalogueError(entry.name + ': a successful JSON response schema is required')
    result_fields = set.intersection(*(set(s.get('properties', {})) for s in responses.values()))
    if (set(entry.field_labels) | set(entry.sensitive_fields)) - set(properties) or set(entry.result_labels) - result_fields:
        raise CatalogueError(entry.name + ': labels/sensitive fields must name declared input/result properties')
    for response in responses.values():
        Draft202012Validator.check_schema(response)
        if any(response['properties'][field].get('type') not in ('string', 'integer', 'number', 'boolean')
               for field in entry.result_labels):
            raise CatalogueError(entry.name + ': result labels must select scalar properties')
    return ToolSpec(entry, method, path, schema, path_fields, body_fields, responses)


def load_catalogue(config=None):
    config = config or {}
    path = Path(config.get('TOOL_CATALOGUE_PATH') or Path(__file__).with_name('tools.json')).resolve()
    try:
        catalogue = Catalogue.model_validate_json(path.read_text(encoding='utf-8'))
        document = json.loads((path.parent / catalogue.openapi).read_text(encoding='utf-8'))
        if document.get('openapi') != '3.1.0':
            raise CatalogueError('Tool catalogue requires an OpenAPI 3.1.0 document')
        names = [entry.name for entry in catalogue.tools]
        if len(set(names)) != len(names):
            raise CatalogueError('Tool catalogue contains duplicate tool names')
        return {entry.name: compile_entry(entry, document) for entry in catalogue.tools}
    except CatalogueError:
        raise
    except ValidationError as exc:
        fields = ', '.join('.'.join(map(str, error['loc'])) for error in exc.errors())
        raise CatalogueError('Invalid catalogue fields: ' + fields) from None
    except (OSError, ValueError, KeyError, TypeError, SchemaError):
        raise CatalogueError('Invalid catalogue/OpenAPI file: check version, fields, JSON schema and local file references') from None


if __name__ == '__main__':
    from card_chat.config import settings
    for tool in load_catalogue(settings()).values():
        print(f'{tool.name}: {tool.method} {tool.path}; confirmation={tool.entry.confirmation}; safe_retry={tool.safe_retry}')
