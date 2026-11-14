import pytest
from fastapi.testclient import TestClient

from card_servicing.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_items(client: TestClient) -> None:
    response = client.get("/items")

    assert response.status_code == 200
    assert response.json() == []


def test_create_item(client: TestClient) -> None:
    response = client.post(
        "/items", json={"name": "notebook", "description": "work notes"}
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "notebook",
        "description": "work notes",
    }


def test_create_item_rejects_empty_name(client: TestClient) -> None:
    response = client.post("/items", json={"name": ""})

    assert response.status_code == 422


def test_get_item(client: TestClient) -> None:
    created = client.post("/items", json={"name": "notebook"}).json()

    response = client.get(f"/items/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_item_returns_404(client: TestClient) -> None:
    response = client.get("/items/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Item not found"}


def test_update_item(client: TestClient) -> None:
    created = client.post("/items", json={"name": "notebook"}).json()

    response = client.put(
        f"/items/{created['id']}",
        json={"name": "planner", "description": "weekly"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": created["id"],
        "name": "planner",
        "description": "weekly",
    }


def test_update_missing_item_returns_404(client: TestClient) -> None:
    response = client.put("/items/999", json={"name": "planner"})

    assert response.status_code == 404


def test_delete_item(client: TestClient) -> None:
    created = client.post("/items", json={"name": "notebook"}).json()

    response = client.delete(f"/items/{created['id']}")

    assert response.status_code == 204
    assert client.get(f"/items/{created['id']}").status_code == 404


def test_delete_missing_item_returns_404(client: TestClient) -> None:
    response = client.delete("/items/999")

    assert response.status_code == 404
