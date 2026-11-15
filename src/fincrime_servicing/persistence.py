"""Independent SQLite persistence for FinCrime card blocks and audit logs."""
from contextlib import closing
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import sqlite3
from uuid import uuid4

from card_servicing.ports import StorageUnavailable
from card_servicing.idempotency import IdempotencyConflict
from fincrime_servicing.models import CardBlockResult


def storage_operation(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (sqlite3.Error, OSError, ValueError) as exc:
            if isinstance(exc, IdempotencyConflict):
                raise
            raise StorageUnavailable('FinCrime storage unavailable') from None
    return wrapped


SCHEMA = """
CREATE TABLE IF NOT EXISTS blocked_cards (
    id TEXT PRIMARY KEY NOT NULL,
    card_id TEXT NOT NULL UNIQUE,
    account_number TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'blocked',
    blocked_at TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_blocked_cards_account ON blocked_cards(account_number);
CREATE INDEX IF NOT EXISTS ix_blocked_cards_customer ON blocked_cards(customer_id);

CREATE TABLE IF NOT EXISTS fincrime_audit_log (
    id TEXT PRIMARY KEY NOT NULL,
    event TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    account_number TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fincrime_idempotency (
    request_key TEXT PRIMARY KEY NOT NULL,
    fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class SQLiteFinCrimeStore:
    dependency_name = 'sqlite_fincrime'

    def __init__(self, path: str):
        self.path = Path(path)
        self.ready = False

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute('PRAGMA foreign_keys = ON')
        except Exception:
            db.close()
            raise
        return db

    @storage_operation
    def initialize(self):
        if str(self.path) != ':memory:':
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
        self.ready = True

    @storage_operation
    def block_card_idempotent(self, customer_id: str, card_id: str, account_number: str,
                              reason: str, request_key: str | None, synced: bool = True) -> tuple[CardBlockResult, bool]:
        if not self.ready:
            self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        identity = f"{customer_id}:{card_id}:{reason}"
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if request_key:
                prior = db.execute(
                    'SELECT fingerprint, response_json FROM fincrime_idempotency WHERE request_key=?',
                    (request_key,)).fetchone()
                if prior:
                    if prior[0] != identity:
                        raise IdempotencyConflict()
                    return CardBlockResult.model_validate_json(prior[1]), True

            # Check if already blocked in FinCrime table
            existing = db.execute(
                'SELECT id, card_id, account_number, customer_id, reason, status, blocked_at, synced FROM blocked_cards WHERE card_id=?',
                (card_id,)).fetchone()
            if existing:
                res = CardBlockResult(
                    card_id=existing[1], account_number=existing[2],
                    status=existing[5], reason=existing[4],
                    blocked_at=existing[6], synced=bool(existing[7]),
                    message="Card was already blocked."
                )
            else:
                record_id = uuid4().hex
                db.execute(
                    'INSERT INTO blocked_cards (id, card_id, account_number, customer_id, reason, status, blocked_at, synced) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (record_id, card_id, account_number, customer_id, reason, 'blocked', now, 1 if synced else 0)
                )
                audit_id = uuid4().hex
                db.execute(
                    'INSERT INTO fincrime_audit_log (id, event, customer_id, card_id, account_number, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (audit_id, 'card_blocked', customer_id, card_id, account_number, f"Reason: {reason}", now)
                )
                res = CardBlockResult(
                    card_id=card_id, account_number=account_number,
                    status='blocked', reason=reason,
                    blocked_at=now, synced=synced,
                    message="Card successfully blocked."
                )

            if request_key:
                db.execute(
                    'INSERT OR REPLACE INTO fincrime_idempotency (request_key, fingerprint, response_json, created_at) VALUES (?, ?, ?, ?)',
                    (request_key, identity, res.model_dump_json(), now)
                )
            return res, False

    @storage_operation
    def unblock_card_idempotent(self, customer_id: str, card_id: str, account_number: str,
                                reason: str, request_key: str | None, synced: bool = True) -> tuple[CardBlockResult, bool]:
        if not self.ready:
            self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        identity = f"{customer_id}:{card_id}:{reason}:unblock"
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if request_key:
                prior = db.execute(
                    'SELECT fingerprint, response_json FROM fincrime_idempotency WHERE request_key=?',
                    (request_key,)).fetchone()
                if prior:
                    if prior[0] != identity:
                        raise IdempotencyConflict()
                    return CardBlockResult.model_validate_json(prior[1]), True

            existing = db.execute(
                'SELECT id, card_id, account_number, customer_id, reason, status, blocked_at, synced FROM blocked_cards WHERE card_id=?',
                (card_id,)).fetchone()
            if existing:
                account_number = existing[2] or account_number
                db.execute(
                    "UPDATE blocked_cards SET status='unblocked', synced=? WHERE card_id=?",
                    (1 if synced else 0, card_id)
                )

            audit_id = uuid4().hex
            db.execute(
                'INSERT INTO fincrime_audit_log (id, event, customer_id, card_id, account_number, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (audit_id, 'card_unblocked', customer_id, card_id, account_number, f"Reason: {reason}", now)
            )
            res = CardBlockResult(
                card_id=card_id, account_number=account_number,
                status='created', reason=reason,
                synced=synced,
                message="Card successfully unblocked."
            )

            if request_key:
                db.execute(
                    'INSERT OR REPLACE INTO fincrime_idempotency (request_key, fingerprint, response_json, created_at) VALUES (?, ?, ?, ?)',
                    (request_key, identity, res.model_dump_json(), now)
                )
            return res, False

    @storage_operation
    def mark_card_synced(self, card_id: str, synced: bool = True):
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE blocked_cards SET synced=? WHERE card_id=?', (1 if synced else 0, card_id))

    @storage_operation
    def is_account_blocked(self, account_number: str) -> bool:
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT 1 FROM blocked_cards WHERE account_number=? AND status='blocked'",
                (account_number,)).fetchone()
            return row is not None

    @storage_operation
    def get_blocked_cards_for_account(self, account_number: str) -> list[CardBlockResult]:
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT card_id, account_number, status, reason, blocked_at, synced FROM blocked_cards WHERE account_number=? AND status='blocked'",
                (account_number,)).fetchall()
            return [CardBlockResult(card_id=r[0], account_number=r[1], status=r[2], reason=r[3], blocked_at=r[4], synced=bool(r[5])) for r in rows]

    @storage_operation
    def get_blocked_card(self, card_id: str) -> CardBlockResult | None:
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db:
            r = db.execute(
                "SELECT card_id, account_number, status, reason, blocked_at, synced FROM blocked_cards WHERE card_id=?",
                (card_id,)).fetchone()
            if not r:
                return None
            return CardBlockResult(card_id=r[0], account_number=r[1], status=r[2], reason=r[3], blocked_at=r[4], synced=bool(r[5]))
