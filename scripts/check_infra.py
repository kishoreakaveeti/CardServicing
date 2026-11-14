"""Local infrastructure smoke check. Run from the repository root."""
import sqlite3
from pathlib import Path
from uuid import uuid4

import pika
from dotenv import dotenv_values
from pymongo import MongoClient
from redis import Redis


def main() -> None:
    config = dotenv_values('.env')
    marker = uuid4().hex
    path = Path(config.get('SQLITE_PATH') or 'data/cards.db')
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TEMP TABLE infra_check (value TEXT)')
        db.execute('INSERT INTO infra_check VALUES (?)', (marker,))
        assert db.execute('SELECT value FROM infra_check').fetchone()[0] == marker
    print(f'SQLite OK: {path} (SQLite {sqlite3.sqlite_version})')

    with MongoClient(
        config.get('MONGODB_HOST', '127.0.0.1'), int(config.get('MONGODB_PORT', 27018)), username=config['MONGO_USER'],
        password=config['MONGO_PASSWORD'], authSource='admin',
        serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000,
    ) as mongo:
        collection = mongo[config['MONGODB_DATABASE']]['infra_checks']
        try:
            collection.insert_one({'_id': marker})
            assert collection.find_one({'_id': marker}) is not None
        finally:
            collection.delete_one({'_id': marker})
    print('MongoDB OK: authenticated write/read/delete')

    with Redis.from_url(config['REDIS_URL'], socket_timeout=5, socket_connect_timeout=5) as cache:
        key = f'infra-check:{marker}'
        try:
            cache.set(key, marker, ex=30)
            assert cache.get(key) == marker.encode()
        finally:
            cache.delete(key)
    print('Redis OK: write/read/delete with expiry')

    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host='127.0.0.1', port=int(config.get('RABBITMQ_PORT', 5673)), credentials=pika.PlainCredentials(
            config['RABBITMQ_USER'], config['RABBITMQ_PASSWORD']),
        socket_timeout=5, stack_timeout=10, blocked_connection_timeout=5,
    ))
    try:
        channel = connection.channel()
        queue = channel.queue_declare(queue='', exclusive=True, auto_delete=True).method.queue
        channel.confirm_delivery()
        channel.basic_publish(exchange='', routing_key=queue, body=marker.encode(), mandatory=True)
        method, _, body = channel.basic_get(queue, auto_ack=False)
        assert method is not None and body == marker.encode()
        channel.basic_ack(method.delivery_tag)
    finally:
        connection.close()
    print('RabbitMQ OK: confirmed publish/consume/ack (temporary queue)')


if __name__ == '__main__':
    main()
