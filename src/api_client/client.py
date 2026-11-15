from typing import Any
from urllib.parse import quote

import httpx


class ApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8010",
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=10.0,
            transport=transport,
        )
        self.last_response_headers = httpx.Headers()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def create_card(self, user_name: str, given_name: str, account_number: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._request('POST', '/cards', headers={'Idempotency-Key': idempotency_key},
                             json={'user_name': user_name, 'given_name': given_name, 'account_number': account_number})

    def execute_operation(self, method: str, path: str, *, path_parameters: dict,
                          body: dict | None, execution_headers: dict) -> tuple[int, Any]:
        """Execute a trusted, compiled operation; customer values fill parameters only."""
        for name, value in path_parameters.items():
            path = path.replace('{' + name + '}', quote(value, safe=''))
        response = self._client.request(method, path, json=body, headers=execution_headers)
        response.raise_for_status()
        self.last_response_headers = response.headers
        return response.status_code, response.json()

    def list_items(self) -> list[dict[str, Any]]:
        return self._request("GET", "/items")

    def get_item(self, item_id: int) -> dict[str, Any]:
        return self._request("GET", f"/items/{item_id}")

    def create_item(
        self, name: str, description: str = ""
    ) -> dict[str, Any]:
        return self._request(
            "POST", "/items", json={"name": name, "description": description}
        )

    def update_item(
        self, item_id: int, name: str, description: str = ""
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/items/{item_id}",
            json={"name": name, "description": description},
        )

    def delete_item(self, item_id: int) -> None:
        response = self._client.delete(f"/items/{item_id}")
        response.raise_for_status()

    def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> Any:
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
