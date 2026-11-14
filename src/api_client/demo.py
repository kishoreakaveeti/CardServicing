import os
from uuid import uuid4
from api_client.client import ApiClient

def main():
    with ApiClient(os.getenv('API_BASE_URL', 'http://127.0.0.1:8010')) as client:
        print('Health:', client.health())
        key = str(uuid4())
        card = client.create_card('alex.smith', 'Alex', '0012345678', idempotency_key=key)
        print('Created:', card)
        assert client.create_card('alex.smith', 'Alex', '0012345678', idempotency_key=key) == card
        print('Retry returned the same card.')

if __name__ == '__main__':
    main()
