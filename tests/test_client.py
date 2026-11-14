import httpx
import json
import pytest

from api_client.client import ApiClient


def test_card_client_contract_and_error():
    requests = []
    with make_client(requests, {'card_number': 'SIM-test'}, 201) as client:
        assert client.create_card('alex', 'Alex', '001', idempotency_key='key')['card_number'] == 'SIM-test'
    assert requests[0].url.path == '/cards'
    assert requests[0].headers['Idempotency-Key'] == 'key'
    assert json.loads(requests[0].read()) == {'user_name': 'alex', 'given_name': 'Alex', 'account_number': '001'}
    with make_client([], {'detail': 'conflict'}, 409) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.create_card('alex', 'Alex', '001', idempotency_key='key')


@pytest.mark.parametrize('base', ['https://gateway.example/demo/card-service', 'https://gateway.example/demo/card-service/'])
def test_gateway_prefix_preserves_card_contract(base):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(201, json={'card_number': 'SIM-synthetic'})
    with ApiClient(base_url=base, transport=httpx.MockTransport(handler)) as client:
        assert client.create_card('alex.synthetic', 'Alex', '0012345678', idempotency_key='gateway-key')['card_number'] == 'SIM-synthetic'
    request = requests[0]
    assert request.method == 'POST'
    assert str(request.url) == 'https://gateway.example/demo/card-service/cards'
    assert request.headers['Idempotency-Key'] == 'gateway-key'
    assert json.loads(request.content)['account_number'] == '0012345678'


def make_client(
    requests: list[httpx.Request], response_body: object, status_code: int = 200
) -> ApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json=response_body)

    return ApiClient(transport=httpx.MockTransport(handler))


def test_health_calls_health_endpoint() -> None:
    requests: list[httpx.Request] = []
    with make_client(requests, {"status": "ok"}) as client:
        assert client.health() == {"status": "ok"}

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/health"


def test_list_items_calls_items_endpoint() -> None:
    requests: list[httpx.Request] = []
    with make_client(requests, []) as client:
        assert client.list_items() == []

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/items"


def test_get_item_calls_item_endpoint() -> None:
    requests: list[httpx.Request] = []
    item = {"id": 3, "name": "notebook", "description": ""}
    with make_client(requests, item) as client:
        assert client.get_item(3) == item

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/items/3"


def test_create_item_sends_item_payload() -> None:
    requests: list[httpx.Request] = []
    item = {"id": 1, "name": "notebook", "description": "work"}
    with make_client(requests, item) as client:
        assert client.create_item("notebook", "work") == item

    assert requests[0].method == "POST"
    assert requests[0].url.path == "/items"
    assert requests[0].read() == b'{"name":"notebook","description":"work"}'


def test_update_item_sends_item_payload() -> None:
    requests: list[httpx.Request] = []
    item = {"id": 3, "name": "planner", "description": "weekly"}
    with make_client(requests, item) as client:
        assert client.update_item(3, "planner", "weekly") == item

    assert requests[0].method == "PUT"
    assert requests[0].url.path == "/items/3"
    assert requests[0].read() == b'{"name":"planner","description":"weekly"}'


def test_delete_item_calls_item_endpoint() -> None:
    requests: list[httpx.Request] = []
    client = make_client(requests, None, status_code=204)
    with client:
        client.delete_item(3)

    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/items/3"
