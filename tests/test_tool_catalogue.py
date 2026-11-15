import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from card_chat.catalogue import CatalogueError, load_catalogue
from test_chat import create_app, prepare_review
from card_chat.gateway import Gateway, connect
from card_chat.turns import CustomerReply, ProposedToolCall
from test_chat import StubModel, running_api

FIXTURES = Path(__file__).parent / 'fixtures'


def configured(monkeypatch):
    monkeypatch.setenv('TOOL_CATALOGUE_PATH', str(FIXTURES / 'tool-catalogue.json'))


@pytest.mark.parametrize('lose_result', [False, True])
def test_config_only_second_operation_real_mcp_http(monkeypatch, lose_result):
    configured(monkeypatch)
    original = Gateway.execute
    tool_calls = []
    async def capture(self, name, arguments, key=None):
        result = await original(self, name, arguments, key)
        tool_calls.append((name, arguments, key))
        if lose_result:
            raise TimeoutError('Synthetic response loss for non-idempotent operation')
        return result
    monkeypatch.setattr(Gateway, 'execute', capture)
    runtime = {'PYTHONPATH': str(Path(__file__).parent) + os.pathsep + str(Path('src').resolve())}
    with running_api(runtime_env=runtime, app_target='catalogue_api:app') as url:
        monkeypatch.setenv('CARD_API_BASE_URL', url)
        model = StubModel()
        model.outputs = [CustomerReply('Which synthetic card should I block, and why?'),
                         ProposedToolCall('block_card', {'card_id': '000042', 'reason': 'customer requested'}),
                         ProposedToolCall('block_card', {'card_id': '000042', 'reason': 'updated reason'})]
        with TestClient(create_app(model=model)) as client:
            assert set(client.app.state.gateway.tools) == {'create_card', 'block_card'}
            path = '/conversations/' + client.post('/conversations').json()['id']
            assert client.post(path + '/messages', json={'text': 'block my card'}).json()['review'] is None
            review = prepare_review(client, path.rsplit('/', 1)[1], {'card_id': '000042', 'reason': 'customer requested'}, 'block_card').json()
            assert review['confirmation_label'] == 'Block this card'
            assert httpx.get(url + '/test-stats').json()['writes'] == 0
            fresh = prepare_review(client, path.rsplit('/', 1)[1], {'card_id': '000042', 'reason': 'updated reason'}, 'block_card').json()
            assert client.post(path + '/confirm', json={'review': review['review']}).status_code == 409
            assert httpx.get(url + '/test-stats').json()['writes'] == 0
            for definitions in model.seen_tools:
                assert set(definitions) == {'create_card', 'block_card', 'logout'}
                assert set(definitions['block_card']['input_schema']['properties']) == {'card_id', 'reason'}
                assert 'idempotency_key' not in json.dumps(definitions)
            confirmation = {'review': fresh['review']}
            response = client.post(path + '/confirm', json=confirmation).json()
            if lose_result:
                assert response['can_confirm'] is False
                assert 'cannot be safely retried' in response['message']
            else:
                assert 'Block card succeeded.' in response['message']
                assert '000042' in response['message']
            duplicate = client.post(path + '/confirm', json=confirmation).json()
            assert duplicate['message'] == response['message'] if not lose_result else 'cannot be safely retried' in duplicate['message']
            assert len(tool_calls) == 1 and len(model.seen_tools) == 1
            assert httpx.get(url + '/test-stats').json() == {'writes': 1, 'card_id': '000042', 'reason': 'updated reason'}


def test_read_without_confirmation_uses_same_handler(monkeypatch):
    data = json.loads((FIXTURES / 'tool-catalogue.json').read_text())
    data['tools'] = [{'name': 'test_stats', 'operation_id': 'test_stats', 'description': 'Read test status.',
                      'confirmation': 'none', 'result_labels': {'writes': 'Writes'}}]
    # Catalogue compilation handles empty input schemas and optional display labels.
    data['openapi'] = str(FIXTURES / 'tools.openapi.json')
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'tools.json'
        path.write_text(json.dumps(data))
        monkeypatch.setenv('TOOL_CATALOGUE_PATH', str(path))
        runtime = {'PYTHONPATH': str(Path(__file__).parent) + os.pathsep + str(Path('src').resolve())}
        with running_api(runtime_env=runtime, app_target='catalogue_api:app') as url:
            monkeypatch.setenv('CARD_API_BASE_URL', url)
            model = StubModel(); model.outputs = [ProposedToolCall('test_stats', {})]
            with TestClient(create_app(model=model)) as client:
                cid = client.post('/conversations').json()['id']
                result = client.post(f'/conversations/{cid}/messages', json={'text': 'status'}).json()
                assert result['review'] is None and 'Writes: 0' in result['message']


@pytest.mark.parametrize('change', ['duplicate', 'missing_operation', 'query', 'nested_body', 'external_ref',
                                   'unconfirmed_write', 'collision', 'bad_version', 'bad_labels', 'missing_header_policy'])
def test_invalid_catalogue_fails_before_child_starts(tmp_path, change):
    catalogue = json.loads((FIXTURES / 'tool-catalogue.json').read_text())
    document = json.loads((FIXTURES / 'tools.openapi.json').read_text())
    operation = document['paths']['/cards/{card_id}/block']['post']
    if change == 'duplicate':
        catalogue['tools'].append(deepcopy(catalogue['tools'][0]))
    elif change == 'missing_operation':
        catalogue['tools'][1]['operation_id'] = 'missing'
    elif change == 'query':
        operation['parameters'].append({'in': 'query', 'name': 'q', 'schema': {'type': 'string'}})
    elif change == 'nested_body':
        document['components']['schemas']['BlockRequest']['properties']['reason'] = {'type': 'object', 'properties': {}}
    elif change == 'external_ref':
        operation['requestBody'] = {'$ref': 'https://example.com/untrusted.json'}
    elif change == 'unconfirmed_write':
        catalogue['tools'][1]['confirmation'] = 'none'
    elif change == 'collision':
        document['components']['schemas']['BlockRequest']['properties']['card_id'] = {'type': 'string'}
    elif change == 'bad_version':
        catalogue['version'] = 2
    elif change == 'bad_labels':
        catalogue['tools'][1]['result_labels']['unknown'] = 'Unknown'
    elif change == 'missing_header_policy':
        del catalogue['tools'][0]['idempotency_header']
    (tmp_path / 'api.json').write_text(json.dumps(document))
    catalogue['openapi'] = 'api.json'
    path = tmp_path / 'tools.json'; path.write_text(json.dumps(catalogue))
    with pytest.raises(CatalogueError):
        load_catalogue({'TOOL_CATALOGUE_PATH': str(path)})


def test_path_values_cannot_change_destination_or_escape_prefix():
    from api_client.client import ApiClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    with ApiClient('https://gateway.example/prefix', transport=httpx.MockTransport(handler)) as client:
        client.execute_operation('POST', '/cards/{card_id}/block', path_parameters={'card_id': 'https://other.example/x?y=z'},
                                 body={'reason': 'synthetic'}, execution_headers={})
    assert calls[0].url.host == 'gateway.example'
    assert calls[0].url.raw_path.startswith(b'/prefix/cards/https%3A%2F%2Fother.example%2Fx%3Fy%3Dz/block')
    spec = load_catalogue({'TOOL_CATALOGUE_PATH': str(FIXTURES / 'tool-catalogue.json')})['block_card']
    with pytest.raises(ValueError):
        spec.validate({'card_id': '..', 'reason': 'synthetic'})


def test_optional_body_field_is_not_invented_and_schema_metadata_is_retained():
    from card_chat.catalogue import compile_entry, Entry
    document = json.loads((FIXTURES / 'tools.openapi.json').read_text())
    document['components']['schemas']['BlockRequest']['properties']['note'] = {
        'type': 'string', 'description': 'Optional customer note', 'maxLength': 20}
    entry = Entry.model_validate(json.loads((FIXTURES / 'tool-catalogue.json').read_text())['tools'][1])
    spec = compile_entry(entry, document)
    values = {'card_id': '000042', 'reason': 'synthetic'}
    assert spec.validate(values) == values
    assert spec.missing(values) == []
    assert spec.input_schema['properties']['note']['description'] == 'Optional customer note'
    with pytest.raises(ValueError):
        spec.validate({**values, 'note': 'x' * 21})
